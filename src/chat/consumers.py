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
from .utils.logger import log_chat_event

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

        try:
            log_chat_event(
                action="WS_EVENT",
                user_id=self.user.id,
                conversation_id=int(self.conversation_id),
                extra={"event": "connect"}
            )
        except Exception:
            pass

    async def disconnect(self, close_code):
        logger.info(f"DISCONNECT: user={self.user}, code={close_code}")

        if hasattr(self, "room_group_name"):
            try:
                log_chat_event(
                    action="WS_EVENT",
                    user_id=self.user.id,
                    conversation_id=int(self.conversation_id),
                    extra={"event": "disconnect", "code": close_code}
                )
            except Exception:
                pass

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
            reply_to_id = data.get("reply_to")

            if not message:
                await self.send(text_data=json.dumps({
                    "error": "Message content is required"
                }))
                return

            # Ensure sender unread count is always 0
            await self.reset_unread_count(self.user.id)

            saved_message = await self.save_message(message, message_type, reply_to_id=reply_to_id)

            # Issue 2: Duplicate WS event protection
            redis_key = f"ws_sent:{saved_message['id']}:{self.conversation_id}"
            is_sent = await sync_to_async(cache.get)(redis_key)
            
            if not is_sent:
                await sync_to_async(cache.set)(redis_key, True, timeout=5)

                await self.channel_layer.group_send(
                    self.room_group_name,
                    {
                        "type": "chat_message",
                        "data": saved_message
                    }
                )

                # 🔹 BROADCAST NOTIFICATION TO ALL PARTICIPANTS
                participants = await self.get_conversation_participants()
                for p_id in participants:
                    if str(p_id) != str(self.user.id):
                        # Issue 4: Ensure other participants' unread counts are incremented
                        unread_count = await self.increment_unread_count(str(p_id))
                        
                        # Issue 3: Weak Notification Payload + Issue 5
                        await self.channel_layer.group_send(
                            f"user_{p_id}",
                            {
                                "type": "new_message",
                                "conversation_id": self.conversation_id,
                                "last_message": {
                                    "content": saved_message["content"],
                                    "created_at": saved_message["created_at"],
                                    "sender_id": str(saved_message["sender"]["id"]),
                                    "is_forwarded": saved_message.get("is_forwarded", False)
                                },
                                "unread_count": unread_count
                            }
                        )

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
        # 🔹 REACTION TRIGGER
        # ==============================
        elif action == "react":
            message_id = data.get("message_id")
            emoji = data.get("emoji")

            if not message_id or not emoji or not isinstance(emoji, str):
                await self.send(text_data=json.dumps({"error": "Invalid reaction payload"}))
                return

            emoji = emoji.strip()
            if not emoji or len(emoji) > 10:
                await self.send(text_data=json.dumps({"error": "Invalid reaction payload"}))
                return

            # Dedup: prevent rapid duplicate broadcasts
            dedup_key = f"reaction_ws:{message_id}:{emoji}:{self.user.id}"
            is_dup = await sync_to_async(cache.get)(dedup_key)
            if is_dup:
                return

            try:
                reactions = await sync_to_async(ChatService.toggle_reaction)(self.user, message_id, emoji)

                await sync_to_async(cache.set)(dedup_key, True, timeout=3)

                await self.channel_layer.group_send(
                    self.room_group_name,
                    {
                        "type": "reaction_update_event",
                        "message_id": message_id,
                        "reactions": reactions
                    }
                )
            except Exception as e:
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

    async def reaction_update_event(self, event):
        await self.send(text_data=json.dumps({
            "type": "reaction_update",
            "message_id": event["message_id"],
            "reactions": event["reactions"]
        }))

    # ==============================
    # 🔹 READ RECEIPT BROADCAST
    # ==============================
    async def read_receipt(self, event):
        await self.send(text_data=json.dumps({
            "type": "read_receipt",
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
    def save_message(self, content, message_type, reply_to_id=None):
        message = ChatService.send_message(
            user=self.user,
            conversation_id=self.conversation_id,
            content=content,
            message_type=message_type,
            reply_to_id=reply_to_id
        )

        from .serializers import MessageSerializer
        return MessageSerializer(message, context={"request": None}).data


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

    async def get_unread_count(self, user_id):
        return await sync_to_async(ChatService.get_unread_count)(user_id, self.conversation_id)

    async def increment_unread_count(self, user_id):
        return await sync_to_async(ChatService.increment_unread_count)(user_id, self.conversation_id)

    async def reset_unread_count(self, user_id):
        return await sync_to_async(ChatService.reset_unread_count)(user_id, self.conversation_id)


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

        # Join global presence group
        await self.channel_layer.group_add(
            "global_presence",
            self.channel_name
        )

        await self.accept()
        logger.info(f"Notification CONNECT: user={self.user}, group={self.user_group_name}")

        try:
            log_chat_event(
                action="WS_EVENT",
                user_id=self.user.id,
                extra={"event": "notification_connect"}
            )
        except Exception:
            pass

        conn_count = await self.increment_connection()
        
        if conn_count == 1:
            await self.channel_layer.group_send(
                "global_presence",
                {
                    "type": "presence_update",
                    "user_id": str(self.user.id),
                    "status": "user_online"
                }
            )

    @database_sync_to_async
    def increment_connection(self):
        redis_key = f"global_connections:{self.user.id}"
        cache.set(f"online_user_{self.user.id}", True, timeout=60)
        
        try:
            return cache.incr(redis_key)
        except ValueError:
            cache.set(redis_key, 1, timeout=86400)
            return 1

    @database_sync_to_async
    def decrement_connection(self):
        redis_key = f"global_connections:{self.user.id}"
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
        logger.info(f"Notification DISCONNECT: user={self.user}, code={close_code}")

        try:
            log_chat_event(
                action="WS_EVENT",
                user_id=self.user.id,
                extra={"event": "notification_disconnect", "code": close_code}
            )
        except Exception:
            pass
        
        if hasattr(self, "user_group_name"):
            conn_count = await self.decrement_connection()

            # Delay to avoid flicker on immediate reload
            await asyncio.sleep(1.5)

            redis_key = f"global_connections:{self.user.id}"
            current_count = await sync_to_async(cache.get)(redis_key)

            if current_count is not None:
                if int(current_count) <= 0:
                    await sync_to_async(cache.delete)(f"online_user_{self.user.id}")
                    
                    await self.channel_layer.group_send(
                        "global_presence",
                        {
                            "type": "presence_update",
                            "user_id": str(self.user.id),
                            "status": "user_offline"
                        }
                    )

            await self.channel_layer.group_discard(
                self.user_group_name,
                self.channel_name
            )

            await self.channel_layer.group_discard(
                "global_presence",
                self.channel_name
            )

    async def receive(self, text_data):
        try:
            data = json.loads(text_data)
        except json.JSONDecodeError:
            return

        if data.get("action") == "ping":
            await sync_to_async(cache.set)(f"online_user_{self.user.id}", True, timeout=60)
            await self.send(text_data=json.dumps({"type": "pong"}))

    async def presence_update(self, event):
        await self.send(text_data=json.dumps({
            "type": "presence_update",
            "user_id": event["user_id"],
            "status": event["status"]
        }))

    async def new_message(self, event):
        payload = {
            "type": "new_message",
            "conversation_id": event["conversation_id"],
            "last_message": event.get("last_message")
        }
        if "unread_count" in event:
            payload["unread_count"] = event["unread_count"]

        await self.send(text_data=json.dumps(payload))

    async def unread_reset(self, event):
        await self.send(text_data=json.dumps({
            "type": "unread_reset",
            "conversation_id": event["conversation_id"]
        }))



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