import json
import asyncio
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async

from django.contrib.auth import get_user_model
from django.core.cache import cache
from asgiref.sync import sync_to_async
import logging

from .models import ConversationParticipant, Message
from .services import ChatService

logger = logging.getLogger(__name__)

User = get_user_model()


class ChatConsumer(AsyncWebsocketConsumer):

    async def connect(self):
        self.user = self.scope["user"]

        # 🔐 Authentication check
        if not self.user or not self.user.is_authenticated:
            logger.warning(f"Unauthorized WebSocket connection attempt denied.")
            await self.close()
            return

        # 📦 Get conversation ID
        self.conversation_id = self.scope["url_route"]["kwargs"].get("conversation_id")
        self.room_group_name = f"chat_{self.conversation_id}"

        logger.info(f"CONNECT: user={self.user}, room={self.room_group_name}")

        # 🔒 Authorization check
        is_member = await self.is_participant()
        if not is_member:
            logger.warning(f"User {self.user} NOT authorized to join conversation {self.conversation_id}")
            await self.close()
            return

        # ✅ Join group
        await self.channel_layer.group_add(
            self.room_group_name,
            self.channel_name
        )

        await self.accept()

        # 📣 Broadcast online status if first connection
        conn_count = await self.increment_connection()
        
        if conn_count == 1:
            await self.channel_layer.group_send(
                self.room_group_name,
                {
                    "type": "presence_update",
                    "status": "user_online",
                    "user_id": str(self.user.id)
                }
            )

    @database_sync_to_async
    def increment_connection(self):
        redis_key = f"user_connections:{self.user.id}"
        try:
            return cache.incr(redis_key)
        except ValueError:
            cache.set(redis_key, 1, timeout=86400)
            return 1

    @database_sync_to_async
    def decrement_connection(self):
        redis_key = f"user_connections:{self.user.id}"
        try:
            count = cache.decr(redis_key)
            if count < 0:
                cache.set(redis_key, 0, timeout=86400)
                return 0
            return count
        except ValueError:
            cache.set(redis_key, 0, timeout=86400)
            return 0


    async def disconnect(self, close_code):
        logger.info(f"DISCONNECT: user={self.user}, code={close_code}")

        if hasattr(self, "room_group_name"):
            conn_count = await self.decrement_connection()

            # Delay to avoid flicker on immediate reload
            await asyncio.sleep(1.5)

            redis_key = f"user_connections:{self.user.id}"
            current_count = await sync_to_async(cache.get)(redis_key)

            if current_count is not None:
                if int(current_count) <= 0:
                    # 📣 Broadcast offline status
                    await self.channel_layer.group_send(
                        self.room_group_name,
                        {
                            "type": "presence_update",
                            "status": "user_offline",
                            "user_id": str(self.user.id)
                        }
                    )

            await self.channel_layer.group_discard(
                self.room_group_name,
                self.channel_name
            )


    async def receive(self, text_data):
        logger.debug(f"RAW DATA received from user {self.user}: {text_data}")

        # 🔒 Safe JSON parsing
        try:
            data = json.loads(text_data)
        except json.JSONDecodeError:
            await self.send(text_data=json.dumps({
                "error": "Invalid JSON format"
            }))
            return

        action = data.get("action")

        # ==============================
        # 🔹 SEND MESSAGE
        # ==============================
        if action == "send_message":
            message = data.get("message")
            message_type = data.get("type", "text")

            if not message:
                await self.send(text_data=json.dumps({
                    "error": "Message content is required"
                }))
                return

            # Ensure sender unread count is always 0
            await self.reset_unread_count(self.user.id)

            await self.save_message(message, message_type)

        # ==============================
        # 🔹 DELETE MESSAGE
        # ==============================
        elif action == "delete_message":
            message_id = data.get("message_id")
            mode = data.get("mode", "everyone")

            if not message_id:
                await self.send(text_data=json.dumps({"error": "Message ID required"}))
                return

            try:
                await self.delete_message_action(message_id, mode)
                
                # Broadcast for everyone, or send back to sender for "me"
                if mode == "everyone":
                    await self.channel_layer.group_send(
                        self.room_group_name,
                        {
                            "type": "message_deleted_event",
                            "message_id": message_id,
                            "mode": mode
                        }
                    )
                else:
                    await self.send(text_data=json.dumps({
                        "type": "message_deleted",
                        "message_id": message_id,
                        "mode": mode
                    }))
            except Exception as e:
                logger.exception(f"Exception during delete_message_action for user {self.user}: {str(e)}")
                await self.send(text_data=json.dumps({"error": str(e)}))

        # ==============================
        # 🔹 MARK AS READ
        # ==============================
        elif action == "mark_read":
            message_id = data.get("message_id")

            try:
                # Reset Redis unread count (O(1) performance)
                await self.reset_unread_count(self.user.id)

                if message_id:
                    await self.mark_message_seen_action(message_id)
                    await self.channel_layer.group_send(
                        self.room_group_name,
                        {
                            "type": "message_seen_event",
                            "message_id": message_id,
                            "user_id": str(self.user.id)
                        }
                    )
                else:
                    # Legacy fallback
                    await self.mark_as_read()
                    await self.channel_layer.group_send(
                        self.room_group_name,
                        {
                            "type": "read_receipt",
                            "user_id": str(self.user.id)
                        }
                    )
            except Exception as e:
                logger.error(f"Error marking as read for user {self.user}: {str(e)}", exc_info=True)

        # ==============================
        # 🔹 TYPING
        # ==============================
        elif action == "typing":
            await self.channel_layer.group_send(
                self.room_group_name,
                {
                    "type": "user_typing_event",
                    "user_id": str(self.user.id)
                }
            )

        else:
            await self.send(text_data=json.dumps({
                "error": "Invalid action"
            }))


    # ==============================
    # 🔹 RECEIVE MESSAGE (BROADCAST)
    # ==============================
    async def chat_message(self, event):
        await self.send(text_data=json.dumps({
            "type": "message",
            "data": event["data"]
        }))

    # ==============================
    # 🔹 READ RECEIPT BROADCAST
    # ==============================
    async def read_receipt(self, event):
        await self.send(text_data=json.dumps({
            "type": "read_receipt",
            "user_id": event["user_id"]
        }))

    async def presence_update(self, event):
        await self.send(text_data=json.dumps({
            "type": event["status"],
            "user_id": event["user_id"]
        }))

    async def message_deleted_event(self, event):
        await self.send(text_data=json.dumps({
            "type": "message_deleted",
            "message_id": event["message_id"],
            "mode": event["mode"]
        }))

    async def message_seen_event(self, event):
        await self.send(text_data=json.dumps({
            "type": "message_seen",
            "message_id": event["message_id"],
            "user_id": event["user_id"]
        }))

    async def user_typing_event(self, event):
        if event["user_id"] != str(self.user.id):
            await self.send(text_data=json.dumps({
                "type": "typing",
                "user_id": event["user_id"]
            }))


    # ==============================
    # 🔹 DATABASE OPERATIONS
    # ==============================

    @database_sync_to_async
    def is_participant(self):
        return ConversationParticipant.objects.filter(
            user=self.user,
            conversation_id=self.conversation_id
        ).exists()


    @database_sync_to_async
    def save_message(self, content, message_type):
        message = ChatService.send_message(
            user=self.user,
            conversation_id=self.conversation_id,
            content=content,
            message_type=message_type
        )

        return {
            "id": str(message.id),
            "content": message.content,
            "message_type": message.message_type,
            "sender": {
                "id": str(self.user.id),
                "username": self.user.username
            },
            "created_at": str(message.created_at)
        }


    @database_sync_to_async
    def mark_as_read(self):
        ChatService.mark_as_read(
            user=self.user,
            conversation_id=self.conversation_id
        )

    @database_sync_to_async
    def delete_message_action(self, message_id, mode):
        ChatService.delete_message(
            request_user=self.user,
            message_id=message_id,
            mode=mode
        )

    @database_sync_to_async
    def mark_message_seen_action(self, message_id):
        ChatService.mark_message_seen(
            user=self.user,
            message_id=message_id
        )

    @database_sync_to_async
    def get_conversation_participants(self):
        return list(ConversationParticipant.objects.filter(
            conversation_id=self.conversation_id
        ).values_list("user_id", flat=True))

    @database_sync_to_async
    def get_unread_count_fallback(self, user_id):
        try:
            participant = ConversationParticipant.objects.get(
                user_id=user_id,
                conversation_id=self.conversation_id
            )
            if participant.last_read_message_id:
                return Message.objects.filter(
                    conversation_id=self.conversation_id,
                    id__gt=participant.last_read_message_id
                ).exclude(sender_id=user_id).count()
            else:
                return Message.objects.filter(
                    conversation_id=self.conversation_id
                ).exclude(sender_id=user_id).count()
        except ConversationParticipant.DoesNotExist:
            return 0

    async def get_unread_count(self, user_id):
        redis_key = f"chat:unread:{user_id}:{self.conversation_id}"
        count = await sync_to_async(cache.get)(redis_key)
        if count is None:
            count = await self.get_unread_count_fallback(user_id)
            await sync_to_async(cache.add)(redis_key, count, timeout=604800)
        return count

    async def increment_unread_count(self, user_id):
        redis_key = f"chat:unread:{user_id}:{self.conversation_id}"
        
        # Only call cache.incr() if key already exists
        count = await sync_to_async(cache.get)(redis_key)
        if count is not None:
            return await sync_to_async(cache.incr)(redis_key)
            
        # Initialize by computing from DB and adding safely
        count = await self.get_unread_count_fallback(user_id)
        added = await sync_to_async(cache.add)(redis_key, count, timeout=604800)
        if not added:
            count = await sync_to_async(cache.incr)(redis_key)
        return count

    async def reset_unread_count(self, user_id):
        redis_key = f"chat:unread:{user_id}:{self.conversation_id}"
        
        # Prevent accidental overwrite when cache.get() returns None
        count = await sync_to_async(cache.get)(redis_key)
        if count is not None:
            if count != 0:
                await sync_to_async(cache.set)(redis_key, 0, timeout=604800)
        else:
            await sync_to_async(cache.add)(redis_key, 0, timeout=604800)


class NotificationConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.user = self.scope["user"]

        if not self.user or not self.user.is_authenticated:
            logger.warning("Unauthorized Notification WebSocket connection attempt denied.")
            await self.close()
            return

        self.user_group_name = f"user_{self.user.id}"

        # Join personal user group for notifications
        await self.channel_layer.group_add(
            self.user_group_name,
            self.channel_name
        )

        await self.accept()
        logger.info(f"Notification CONNECT: user={self.user}, group={self.user_group_name}")

    async def disconnect(self, close_code):
        logger.info(f"Notification DISCONNECT: user={self.user}, code={close_code}")
        if hasattr(self, "user_group_name"):
            await self.channel_layer.group_discard(
                self.user_group_name,
                self.channel_name
            )

    async def new_message(self, event):
        payload = {
            "type": "new_message",
            "conversation_id": event["conversation_id"],
            "last_message": event.get("last_message")
        }
        if "unread_count" in event:
            payload["unread_count"] = event["unread_count"]

        await self.send(text_data=json.dumps(payload))



# import json
# from channels.generic.websocket import AsyncWebsocketConsumer
# from channels.db import database_sync_to_async

# from django.contrib.auth import get_user_model
# from .models import ConversationParticipant
# from .services import ChatService

# User = get_user_model()


# class ChatConsumer(AsyncWebsocketConsumer):

#     async def connect(self):
#         self.user = self.scope["user"]

#         if not self.user.is_authenticated:
#             await self.close()
#             return

#         self.conversation_id = self.scope["url_route"]["kwargs"]["conversation_id"]
#         self.room_group_name = f"chat_{self.conversation_id}"

#         # 🔒 Check if user is part of conversation
#         is_member = await self.is_participant()

#         if not is_member:
#             await self.close()
#             return

#         # Join room
#         await self.channel_layer.group_add(
#             self.room_group_name,
#             self.channel_name
#         )

#         await self.accept()


#     async def disconnect(self, close_code):
#         await self.channel_layer.group_discard(
#             self.room_group_name,
#             self.channel_name
#         )


#     async def receive(self, text_data):
#         data = json.loads(text_data)

#         action = data.get("action")

#         # ==============================
#         # 🔹 SEND MESSAGE
#         # ==============================
#         if action == "send_message":
#             message = data.get("message")
#             message_type = data.get("type", "text")

#             saved_message = await self.save_message(message, message_type)

#             await self.channel_layer.group_send(
#                 self.room_group_name,
#                 {
#                     "type": "chat_message",
#                     "data": saved_message
#                 }
#             )

#         # ==============================
#         # 🔹 MARK AS READ
#         # ==============================
#         elif action == "mark_read":
#             await self.mark_as_read()

#             await self.channel_layer.group_send(
#                 self.room_group_name,
#                 {
#                     "type": "read_receipt",
#                     "user_id": str(self.user.id)
#                 }
#             )


#     # ==============================
#     # 🔹 RECEIVE MESSAGE (BROADCAST)
#     # ==============================
#     async def chat_message(self, event):
#         await self.send(text_data=json.dumps({
#             "type": "message",
#             "data": event["data"]
#         }))


#     # ==============================
#     # 🔹 READ RECEIPT BROADCAST
#     # ==============================
#     async def read_receipt(self, event):
#         await self.send(text_data=json.dumps({
#             "type": "read_receipt",
#             "user_id": event["user_id"]
#         }))


#     # ==============================
#     # 🔹 DATABASE OPERATIONS
#     # ==============================

#     @database_sync_to_async
#     def is_participant(self):
#         return ConversationParticipant.objects.filter(
#             user=self.user,
#             conversation_id=self.conversation_id
#         ).exists()


#     @database_sync_to_async
#     def save_message(self, content, message_type):
#         message = ChatService.send_message(
#             user=self.user,
#             conversation_id=self.conversation_id,
#             content=content,
#             message_type=message_type
#         )

#         return {
#             "id": str(message.id),
#             "content": message.content,
#             "message_type": message.message_type,
#             "sender": {
#                 "id": str(self.user.id),
#                 "username": self.user.username
#             },
#             "created_at": str(message.created_at)
#         }


#     @database_sync_to_async
#     def mark_as_read(self):
#         ChatService.mark_as_read(
#             user=self.user,
#             conversation_id=self.conversation_id
#         )







# import json
# from channels.generic.websocket import AsyncWebsocketConsumer


# class ChatConsumer(AsyncWebsocketConsumer):

#     async def connect(self):
#         # 👇 Ignore authentication for now
#         self.user = self.scope.get("user", None)
#         print("🔥 CONNECT HIT | USER:", self.user)

#         # 👇 Get conversation id safely
#         self.conversation_id = self.scope["url_route"]["kwargs"].get("conversation_id", "test")
#         self.room_group_name = f"chat_{self.conversation_id}"

#         print("📡 ROOM:", self.room_group_name)

#         # 👇 Accept connection immediately
#         await self.accept()

#         # 👇 Join group (no Redis needed yet, but safe)
#         try:
#             await self.channel_layer.group_add(
#                 self.room_group_name,
#                 self.channel_name
#             )
#         except Exception as e:
#             print("⚠️ Group add failed (ignore for now):", e)


#     async def disconnect(self, close_code):
#         print("🔌 DISCONNECTED")

#         try:
#             await self.channel_layer.group_discard(
#                 self.room_group_name,
#                 self.channel_name
#             )
#         except Exception:
#             pass


#     async def receive(self, text_data):
#         print("📩 RECEIVED:", text_data)

#         data = json.loads(text_data)

#         # 👇 Echo back (simple test)
#         await self.send(text_data=json.dumps({
#             "message": data.get("message", "No message received"),
#             "status": "ok"
#         })) "created_at": str(message.created_at)
#         }


#     @database_sync_to_async
#     def mark_as_read(self):
#         ChatService.mark_as_read(
#             user=self.user,
#             conversation_id=self.conversation_id
#         )







# import json
# from channels.generic.websocket import AsyncWebsocketConsumer


# class ChatConsumer(AsyncWebsocketConsumer):

#     async def connect(self):
#         # 👇 Ignore authentication for now
#         self.user = self.scope.get("user", None)
#         print("🔥 CONNECT HIT | USER:", self.user)

#         # 👇 Get conversation id safely
#         self.conversation_id = self.scope["url_route"]["kwargs"].get("conversation_id", "test")
#         self.room_group_name = f"chat_{self.conversation_id}"

#         print("📡 ROOM:", self.room_group_name)

#         # 👇 Accept connection immediately
#         await self.accept()

#         # 👇 Join group (no Redis needed yet, but safe)
#         try:
#             await self.channel_layer.group_add(
#                 self.room_group_name,
#                 self.channel_name
#             )
#         except Exception as e:
#             print("⚠️ Group add failed (ignore for now):", e)


#     async def disconnect(self, close_code):
#         print("🔌 DISCONNECTED")

#         try:
#             await self.channel_layer.group_discard(
#                 self.room_group_name,
#                 self.channel_name
#             )
#         except Exception:
#             pass


#     async def receive(self, text_data):
#         print("📩 RECEIVED:", text_data)

#         data = json.loads(text_data)

#         # 👇 Echo back (simple test)
#         await self.send(text_data=json.dumps({
#             "message": data.get("message", "No message received"),
#             "status": "ok"
#         }))