import json
import asyncio
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async

from django.contrib.auth import get_user_model
from django.core.cache import cache
from asgiref.sync import sync_to_async
import logging

from .models import ConversationParticipant, Message, UserPresence
from .services import ChatService, get_user_presence
from .utils.logger import log_chat_event

logger = logging.getLogger(__name__)

User = get_user_model()


class ChatConsumer(AsyncWebsocketConsumer):

    async def connect(self):
        self.user = self.scope["user"]

        if not self.user or not self.user.is_authenticated:
            logger.warning("Unauthorized WebSocket connection attempt denied.")
            await self.close()
            return

        self.conversation_id = self.scope["url_route"]["kwargs"].get("conversation_id")
        self.room_group_name = f"chat_{self.conversation_id}"

        logger.info(f"CONNECT: user={self.user}, room={self.room_group_name}")

        is_member = await self.is_participant()
        if not is_member:
            logger.warning(f"User {self.user} NOT authorized to join conversation {self.conversation_id}")
            await self.close()
            return

        await self.channel_layer.group_add(self.room_group_name, self.channel_name)
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
            await self.channel_layer.group_discard(self.room_group_name, self.channel_name)

    async def receive(self, text_data):
        logger.debug(f"RAW DATA received from user {self.user}: {text_data}")

        try:
            data = json.loads(text_data)
        except json.JSONDecodeError:
            await self.send(text_data=json.dumps({"error": "Invalid JSON format"}))
            return

        action = data.get("action")

        # ==============================
        # SEND MESSAGE
        # ==============================
        if action == "send_message":
            message = data.get("message")
            message_type = data.get("type", "text")
            reply_to_id = data.get("reply_to")

            if not message:
                await self.send(text_data=json.dumps({"error": "Message content is required"}))
                return

            await self.reset_unread_count(self.user.id)
            saved_message = await self.save_message(message, message_type, reply_to_id=reply_to_id)

            redis_key = f"ws_sent:{saved_message['id']}:{self.conversation_id}"
            is_sent = await sync_to_async(cache.get)(redis_key)

            if not is_sent:
                await sync_to_async(cache.set)(redis_key, True, timeout=5)

                # ── Chat room members get type: "message" (the primary message event) ──
                # This is the ONLY event broadcast to the chat room group for new messages.
                #
                # FIX: Previously we also sent a "new_message" event to the room group,
                # which meant every chat room member received BOTH "message" AND "new_message"
                # for the same send. The frontend processed both and inserted the message twice.
                # Now only "message" goes to the room; "new_message" goes to user_ groups only.
                await self.channel_layer.group_send(
                    self.room_group_name,
                    {
                        "type": "chat_message",
                        "data": saved_message
                    }
                )

                # ── Per-user notification groups get type: "new_message" ──
                # These are received by the global NotificationConsumer (useGlobalWebSocket).
                # They update the sidebar only — NOT the message list.
                participants = await self.get_conversation_participants()
                for p_id in participants:
                    if str(p_id) != str(self.user.id):
                        unread_count = await self.increment_unread_count(str(p_id))

                        await self.channel_layer.group_send(
                            f"user_{p_id}",
                            {
                                "type": "new_message",
                                "conversation_id": self.conversation_id,
                                "last_message": {
                                    "id": saved_message["id"],
                                    "content": saved_message["content"],
                                    "created_at": saved_message["created_at"],
                                    # FIX: include full sender object so normalizeMessage
                                    # can parse msg.sender on the frontend
                                    "sender": saved_message["sender"],
                                    "conversation_id": self.conversation_id,
                                    "message_type": saved_message.get("message_type", "text"),
                                    "is_forwarded": saved_message.get("is_forwarded", False),
                                },
                                "unread_count": unread_count
                            }
                        )

                # ── The sender's own notification group also gets a sidebar update ──
                # This keeps the sender's sidebar order/last_message in sync without
                # relying solely on the optimistic updateConversationOnSend.
                await self.channel_layer.group_send(
                    f"user_{self.user.id}",
                    {
                        "type": "new_message",
                        "conversation_id": self.conversation_id,
                        "last_message": {
                            "id": saved_message["id"],
                            "content": saved_message["content"],
                            "created_at": saved_message["created_at"],
                            "sender": saved_message["sender"],
                            "conversation_id": self.conversation_id,
                            "message_type": saved_message.get("message_type", "text"),
                            "is_forwarded": saved_message.get("is_forwarded", False),
                        },
                        "unread_count": 0  # sender always has 0 unread
                    }
                )

        # ==============================
        # DELETE MESSAGE
        # ==============================
        elif action == "delete_message":
            message_id = data.get("message_id")
            mode = data.get("mode", "everyone")

            if not message_id:
                await self.send(text_data=json.dumps({"error": "Message ID required"}))
                return

            try:
                await self.delete_message_action(message_id, mode)

                if mode == "everyone":
                    await self.channel_layer.group_send(
                        self.room_group_name,
                        {"type": "message_deleted_event", "message_id": message_id, "mode": mode}
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
        # REACTION
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

            dedup_key = f"reaction_ws:{message_id}:{emoji}:{self.user.id}"
            is_dup = await sync_to_async(cache.get)(dedup_key)
            if is_dup:
                return

            try:
                reactions = await sync_to_async(ChatService.toggle_reaction)(self.user, message_id, emoji)
                await sync_to_async(cache.set)(dedup_key, True, timeout=3)
                await self.channel_layer.group_send(
                    self.room_group_name,
                    {"type": "reaction_update_event", "message_id": message_id, "reactions": reactions}
                )
            except Exception as e:
                await self.send(text_data=json.dumps({"error": str(e)}))

        # ==============================
        # MARK AS READ
        # ==============================
        elif action == "mark_read":
            message_id = data.get("message_id")

            try:
                await self.reset_unread_count(self.user.id)

                if message_id:
                    await self.mark_message_seen_action(message_id)
                    await self.channel_layer.group_send(
                        self.room_group_name,
                        {"type": "message_seen_event", "message_id": message_id, "user_id": str(self.user.id)}
                    )
                else:
                    await self.mark_as_read()
                    await self.channel_layer.group_send(
                        self.room_group_name,
                        {"type": "read_receipt", "user_id": str(self.user.id)}
                    )
            except Exception as e:
                logger.error(f"Error marking as read for user {self.user}: {str(e)}", exc_info=True)

        # ==============================
        # TYPING
        # ==============================
        elif action == "typing":
            await self.channel_layer.group_send(
                self.room_group_name,
                {"type": "user_typing_event", "user_id": str(self.user.id)}
            )

        else:
            await self.send(text_data=json.dumps({"error": "Invalid action"}))

    # ==============================
    # BROADCAST HANDLERS
    # ==============================

    async def chat_message(self, event):
        await self.send(text_data=json.dumps({"type": "message", "data": event["data"]}))

    async def reaction_update_event(self, event):
        await self.send(text_data=json.dumps({
            "type": "reaction_update",
            "message_id": event["message_id"],
            "reactions": event["reactions"]
        }))

    async def read_receipt(self, event):
        await self.send(text_data=json.dumps({"type": "read_receipt", "user_id": event["user_id"]}))

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
        # Don't echo back to sender
        if event["user_id"] != str(self.user.id):
            await self.send(text_data=json.dumps({"type": "typing", "user_id": event["user_id"]}))

    # ==============================
    # DATABASE OPERATIONS
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
        ChatService.mark_as_read(user=self.user, conversation_id=self.conversation_id)

    @database_sync_to_async
    def delete_message_action(self, message_id, mode):
        ChatService.delete_message(request_user=self.user, message_id=message_id, mode=mode)

    @database_sync_to_async
    def mark_message_seen_action(self, message_id):
        ChatService.mark_message_seen(user=self.user, message_id=message_id)

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

        await self.channel_layer.group_add(self.user_group_name, self.channel_name)
        await self.channel_layer.group_add("global_presence", self.channel_name)
        await self.accept()

        logger.info(f"Notification CONNECT: user={self.user}, group={self.user_group_name}")

        try:
            log_chat_event(action="WS_EVENT", user_id=self.user.id, extra={"event": "notification_connect"})
        except Exception:
            pass

        conn_count, is_visible = await self.increment_connection()
        if conn_count == 1 and is_visible:
            # Only broadcast online if user is visible AND not already marked online
            # (prevents duplicate broadcasts on rapid reconnects)
            already_online = await sync_to_async(cache.get)(f"online_user_{self.user.id}")
            if not already_online:
                await sync_to_async(cache.set)(f"online_user_{self.user.id}", True, timeout=60)
            await self.channel_layer.group_send(
                "global_presence",
                {"type": "presence_update", "user_id": str(self.user.id), "status": "user_online"}
            )

    @database_sync_to_async
    def increment_connection(self):
        from django_redis import get_redis_connection
        redis_conn = get_redis_connection("default")
        redis_key = f"global_connections:{self.user.id}"

        # Check if user is visible (privacy setting)
        is_visible = True
        try:
            presence = UserPresence.objects.get(user_id=self.user.id)
            is_visible = presence.is_visible
        except UserPresence.DoesNotExist:
            pass

        # Only set Redis keys if user is visible
        if is_visible:
            cache.set(f"online_user_{self.user.id}", True, timeout=60)
            redis_conn.sadd("online_users", str(self.user.id))

        try:
            conn_count = cache.incr(redis_key)
        except ValueError:
            cache.set(redis_key, 1, timeout=86400)
            conn_count = 1

        return conn_count, is_visible

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
            log_chat_event(action="WS_EVENT", user_id=self.user.id, extra={"event": "notification_disconnect", "code": close_code})
        except Exception:
            pass

        if hasattr(self, "user_group_name"):
            await self.decrement_connection()
            await asyncio.sleep(1.5)  # delay to avoid flicker on page reload

            redis_key = f"global_connections:{self.user.id}"
            current_count = await sync_to_async(cache.get)(redis_key)

            if current_count is not None and int(current_count) <= 0:
                # Check if user was visible (only broadcast offline if they were visible)
                was_online = await sync_to_async(cache.get)(f"online_user_{self.user.id}")
                
                await sync_to_async(cache.delete)(f"online_user_{self.user.id}")
                from django_redis import get_redis_connection
                redis_conn = get_redis_connection("default")
                redis_conn.srem("online_users", str(self.user.id))
                
                # Only broadcast offline if user was actually online (prevents duplicate offline events)
                if was_online:
                    await self.channel_layer.group_send(
                        "global_presence",
                        {"type": "presence_update", "user_id": str(self.user.id), "status": "user_offline"}
                    )

            await self.channel_layer.group_discard(self.user_group_name, self.channel_name)
            await self.channel_layer.group_discard("global_presence", self.channel_name)

    async def receive(self, text_data):
        try:
            data = json.loads(text_data)
        except json.JSONDecodeError:
            return

        if data.get("action") == "ping":
            # Only refresh online status if user is visible
            is_visible = await self._check_user_visibility()
            if is_visible:
                from django_redis import get_redis_connection
                redis_conn = get_redis_connection("default")
                await sync_to_async(cache.set)(f"online_user_{self.user.id}", True, timeout=60)
                redis_conn.sadd("online_users", str(self.user.id))
            await self.send(text_data=json.dumps({"type": "pong"}))

    @database_sync_to_async
    def _check_user_visibility(self):
        """Check if user has is_visible=True (privacy setting)."""
        try:
            presence = UserPresence.objects.get(user_id=self.user.id)
            return presence.is_visible
        except UserPresence.DoesNotExist:
            return True

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