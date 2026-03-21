import json
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async

from django.contrib.auth import get_user_model
from .models import ConversationParticipant
from .services import ChatService

User = get_user_model()


class ChatConsumer(AsyncWebsocketConsumer):

    async def connect(self):
        self.user = self.scope["user"]

        # 🔐 Authentication check
        if not self.user or not self.user.is_authenticated:
            print("❌ Unauthorized WebSocket connection")
            await self.close()
            return

        # 📦 Get conversation ID
        self.conversation_id = self.scope["url_route"]["kwargs"].get("conversation_id")
        self.room_group_name = f"chat_{self.conversation_id}"

        print(f"🔌 CONNECT: user={self.user}, room={self.room_group_name}")

        # 🔒 Authorization check
        is_member = await self.is_participant()
        if not is_member:
            print(f"❌ User {self.user} not part of conversation {self.conversation_id}")
            await self.close()
            return

        # ✅ Join group
        await self.channel_layer.group_add(
            self.room_group_name,
            self.channel_name
        )

        await self.accept()


    async def disconnect(self, close_code):
        print(f"🔌 DISCONNECT: user={self.user}, code={close_code}")

        if hasattr(self, "room_group_name"):
            await self.channel_layer.group_discard(
                self.room_group_name,
                self.channel_name
            )


    async def receive(self, text_data):
        print(f"📩 RAW DATA: {text_data}")

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

            saved_message = await self.save_message(message, message_type)

            await self.channel_layer.group_send(
                self.room_group_name,
                {
                    "type": "chat_message",
                    "data": saved_message
                }
            )

        # ==============================
        # 🔹 MARK AS READ
        # ==============================
        elif action == "mark_read":
            await self.mark_as_read()

            await self.channel_layer.group_send(
                self.room_group_name,
                {
                    "type": "read_receipt",
                    "user_id": self.user.id
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
#         }))