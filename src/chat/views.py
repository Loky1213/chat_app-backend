from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework import status
from drf_spectacular.utils import extend_schema, OpenApiResponse, inline_serializer
from rest_framework import serializers
from .models import ConversationParticipant
from django.core.cache import cache
from collections import defaultdict
from django.db.models import Count, Q, F
from .models import Conversation, Message, UserPresence, UserReadReceipt
from rest_framework.response import Response
from chat.services import get_user_presence
from .serializers import (
    ConversationListSerializer,
    ConversationDetailSerializer,
    MessageSerializer,
    CreatePrivateChatSerializer,
    CreateGroupSerializer,
    AddMembersSerializer,
    PromoteAdminSerializer,
    RemoveAdminSerializer,
    ForwardMessageSerializer,
)

from .services import ChatService
from utils.api_response import success_response, error_response
from utils.pagination import StandardPagination, MessageCursorPagination
import logging
from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync

logger = logging.getLogger(__name__)


# ==============================
# 🔹 CREATE PRIVATE CHAT
# ==============================
class CreatePrivateChatView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        request=CreatePrivateChatSerializer,
        responses={200: OpenApiResponse(description="Chat created")},
        tags=["Chat"],
        summary="Create private chat"
    )
    def post(self, request):
        serializer = CreatePrivateChatSerializer(data=request.data)

        if serializer.is_valid():
            conversation = ChatService.create_private_chat(
                request.user,
                serializer.validated_data["user_id"]
            )

            return success_response(
                data={"conversation_id": conversation.id},
                message="Chat created",
                status_code=status.HTTP_200_OK
            )

        return error_response(
            message="Validation failed",
            errors=serializer.errors,
            status_code=status.HTTP_400_BAD_REQUEST
        )


# ==============================
# 🔹 CREATE GROUP
# ==============================
class CreateGroupChatView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        request=CreateGroupSerializer,
        responses={201: OpenApiResponse(description="Group created")},
        tags=["Chat"],
        summary="Create group chat"
    )
    def post(self, request):
        serializer = CreateGroupSerializer(data=request.data)

        if serializer.is_valid():
            conversation = ChatService.create_group_chat(
                request.user,
                serializer.validated_data["name"],
                serializer.validated_data["user_ids"]
            )

            return success_response(
                data={"conversation_id": conversation.id},
                message="Group created",
                status_code=status.HTTP_201_CREATED
            )

        return error_response(
            message="Validation failed",
            errors=serializer.errors,
            status_code=status.HTTP_400_BAD_REQUEST
        )


# ==============================
# 🔹 LIST CONVERSATIONS
# ==============================
class ConversationListView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        responses=ConversationListSerializer(many=True),
        tags=["Chat"],
        summary="List user conversations"
    )
    def get(self, request):
        conversations = Conversation.objects.filter(
            conversationparticipant__user=request.user
        ).annotate(
            unread_count_annotated=Count(
                "messages",
                filter=~Q(messages__messageread__user=request.user) & ~Q(messages__sender=request.user),
                distinct=True
            )
        ).select_related("last_message").prefetch_related(
            "conversationparticipant_set__user__presence"
        ).order_by(F("last_message__created_at").desc(nulls_last=True), "-created_at").distinct()

        serializer = ConversationListSerializer(
            conversations,
            many=True,
            context={"request": request}
        )

        return success_response(
            data=serializer.data,
            message="Conversations retrieved",
            status_code=status.HTTP_200_OK
        )


# ==============================
# 🔹 CONVERSATION DETAIL
# ==============================
class ConversationDetailView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        responses=ConversationDetailSerializer,
        tags=["Chat"],
        summary="Get conversation detail"
    )
    def get(self, request, conversation_id):
        try:
            conversation = Conversation.objects.get(id=conversation_id)
        except Conversation.DoesNotExist:
            return error_response(
                message="Conversation not found",
                status_code=status.HTTP_404_NOT_FOUND
            )

        # 🔒 Permission check
        if not conversation.conversationparticipant_set.filter(
            user=request.user
        ).exists():
            return error_response(
                message="Not allowed",
                status_code=status.HTTP_403_FORBIDDEN
            )

        serializer = ConversationDetailSerializer(conversation)

        return success_response(
            data=serializer.data,
            message="Conversation detail retrieved",
            status_code=status.HTTP_200_OK
        )


# ==============================
# 🔹 MESSAGE LIST
# ==============================
class MessageListView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        responses=MessageSerializer(many=True),
        tags=["Chat"],
        summary="Get messages"
    )
    def get(self, request, conversation_id):
        # 🔒 Check membership
        if not Conversation.objects.filter(
            id=conversation_id,
            conversationparticipant__user=request.user
        ).exists():
            return error_response(
                message="Not allowed",
                status_code=status.HTTP_403_FORBIDDEN
            )

        messages = Message.objects.filter(
            conversation_id=conversation_id
        ).select_related("sender", "reply_to", "reply_to__sender").prefetch_related(
            "messageread_set", "reactions"
        ).order_by("-created_at")

        # Remove "delete for me"
        messages = messages.exclude(
            deleted_for_users=request.user
        )

        paginator = MessageCursorPagination()
        page = paginator.paginate_queryset(messages, request, view=self)

        if page is not None:
            serializer = MessageSerializer(
                page,
                many=True,
                context={"request": request}
            )
            return paginator.get_paginated_response(serializer.data)

        # Fallback if pagination fails
        serializer = MessageSerializer(
            messages,
            many=True,
            context={"request": request}
        )
        return success_response(
            data=serializer.data,
            message="Messages retrieved",
            status_code=status.HTTP_200_OK
        )


# ==============================
# 🔹 ADD MEMBERS
# ==============================
class AddMembersView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        request=AddMembersSerializer,
        tags=["Chat"],
        summary="Add members to group"
    )
    def post(self, request, conversation_id):
        serializer = AddMembersSerializer(data=request.data)

        if serializer.is_valid():
            ChatService.add_members(
                request.user,
                conversation_id,
                serializer.validated_data["user_ids"]
            )

            return success_response(
                message="Members added",
                status_code=status.HTTP_200_OK
            )

        return error_response(
            message="Validation failed",
            errors=serializer.errors,
            status_code=status.HTTP_400_BAD_REQUEST
        )


# ==============================
# 🔹 REMOVE MEMBER
# ==============================
class RemoveMemberView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["Chat"],
        summary="Remove member"
    )
    def delete(self, request, conversation_id, user_id):
        ChatService.remove_member(
            request.user,
            conversation_id,
            user_id
        )

        return success_response(
            message="Member removed",
            status_code=status.HTTP_200_OK
        )


# ==============================
# 🔹 PROMOTE ADMIN
# ==============================
class PromoteAdminView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        request=PromoteAdminSerializer,
        tags=["Chat"],
        summary="Promote user to admin"
    )
    def post(self, request, conversation_id):
        serializer = PromoteAdminSerializer(data=request.data)

        if serializer.is_valid():
            ChatService.promote_to_admin(
                request.user,
                conversation_id,
                serializer.validated_data["user_id"]
            )

            return success_response(
                message="User promoted to admin",
                status_code=status.HTTP_200_OK
            )

        return error_response(
            message="Validation failed",
            errors=serializer.errors,
            status_code=status.HTTP_400_BAD_REQUEST
        )


# ==============================
# 🔹 MARK AS READ
# ==============================
class MarkAsReadView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["Chat"],
        summary="Mark messages as read"
    )
    def post(self, request, conversation_id):
        ChatService.mark_as_read(request.user, conversation_id)

        return success_response(
            message="Marked as read",
            status_code=status.HTTP_200_OK
        )


# ==============================
# 🔹 REMOVE ADMIN
# ==============================
class RemoveAdminView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        request=RemoveAdminSerializer,
        tags=["Chat"],
        summary="Remove user from admin"
    )
    def post(self, request, conversation_id):
        serializer = RemoveAdminSerializer(data=request.data)

        if serializer.is_valid():
            ChatService.remove_admin(
                request.user,
                conversation_id,
                serializer.validated_data["user_id"]
            )

            return success_response(
                message="User removed from admin",
                status_code=status.HTTP_200_OK
            )

        return error_response(
            message="Validation failed",
            errors=serializer.errors,
            status_code=status.HTTP_400_BAD_REQUEST
        )


# ==============================
# 🔹 FORWARD MESSAGES
# ==============================
class ForwardMessageView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        request=ForwardMessageSerializer,
        responses={200: OpenApiResponse(description="Messages forwarded")},
        tags=["Chat"],
        summary="Forward a message to multiple conversations"
    )
    def post(self, request):
        serializer = ForwardMessageSerializer(data=request.data)

        if serializer.is_valid():
            try:
                created_messages = ChatService.forward_message(
                    request.user,
                    serializer.validated_data["message_id"],
                    serializer.validated_data["target_ids"]
                )

                channel_layer = get_channel_layer()

                # Get all participants for target conversations in ONE query
                
                participants_dict = defaultdict(list)
                conv_ids = [msg.conversation_id for msg in created_messages]
                
                # O(1) query
                all_participants = ConversationParticipant.objects.filter(
                    conversation_id__in=conv_ids
                ).values_list('conversation_id', 'user_id')
                
                for conv_id, user_id in all_participants:
                    participants_dict[conv_id].append(user_id)

                for msg in created_messages:
                    # Serialize the message
                    msg_serializer = MessageSerializer(msg, context={"request": request})
                    data = msg_serializer.data
                    
                    # Issue 2: Duplicate WS event protection
                    
                    redis_key = f"ws_sent:{msg.id}:{msg.conversation_id}"
                    
                    if not cache.get(redis_key):
                        cache.set(redis_key, True, timeout=5)

                        # Task 4: Broadcast to chat_{conversation_id}
                        async_to_sync(channel_layer.group_send)(
                            f"chat_{msg.conversation_id}",
                            {
                                "type": "chat_message",
                                "data": data
                            }
                        )

                        # Issue 4: Reset sender's unread logic cleanly
                        ChatService.reset_unread_count(request.user.id, msg.conversation_id)

                        # Task 5: Broadcast to user_{user_id} efficiently
                        for p_id in participants_dict[msg.conversation_id]:
                            if p_id != request.user.id:
                                # Increment unread count synchronously
                                unread_count = ChatService.increment_unread_count(p_id, msg.conversation_id)
                                
                                # Issue 3: Improve WebSocket Notification Payload exactly
                                async_to_sync(channel_layer.group_send)(
                                    f"user_{p_id}",
                                    {
                                        "type": "new_message",
                                        "conversation_id": msg.conversation_id,
                                        "last_message": {
                                            "content": data["content"],
                                            "created_at": data["created_at"],
                                            "sender_id": str(data["sender"]["id"]),
                                            "is_forwarded": data.get("is_forwarded", True)
                                        },
                                        "unread_count": unread_count
                                    }
                                )

                return success_response(
                    message="Messages forwarded",
                    status_code=status.HTTP_200_OK
                )
            except Exception as e:
                return error_response(
                    message=str(e),
                    status_code=status.HTTP_400_BAD_REQUEST
                )

        return error_response(
            message="Validation failed",
            errors=serializer.errors,
            status_code=status.HTTP_400_BAD_REQUEST
        )


# ==============================
# 🔹 GET MY PRESENCE
# ==============================
class MyPresenceView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["Chat"],
        summary="Get current user presence state"
    )
    def get(self, request):
        user = request.user

        try:
            is_hidden = user.presence.is_online_override is False
        except Exception:
            is_hidden = False

        return Response({
            "is_online": get_user_presence(user),
            "is_hidden": is_hidden
        })


# ==============================
# 🔹 ONLINE USERS (PRESENCE SNAPSHOT)
# ==============================
class OnlineUsersView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["Chat"],
        summary="Get all currently online users"
    )
    def get(self, request):
        from django_redis import get_redis_connection
        redis_conn = get_redis_connection("default")

        # Get all connected users from Redis
        raw_ids = redis_conn.smembers("online_users")
        user_ids = [int(uid.decode()) for uid in raw_ids]

        if not user_ids:
            return Response({"online_users": []})

        # Filter hidden users in ONE DB query (no loops)
        hidden_user_ids = set(
            UserPresence.objects.filter(
                user_id__in=user_ids,
                is_online_override=False
            ).values_list("user_id", flat=True)
        )

        # Remove hidden users
        visible_users = [
            str(uid) for uid in user_ids if uid not in hidden_user_ids
        ]

        return Response({
            "online_users": visible_users
        })


# ==============================
# 🔹 TOGGLE PRESENCE
# ==============================
class TogglePresenceView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        request=inline_serializer(
            name="PresenceToggleRequest",
            fields={
                "hide_online": serializers.BooleanField()
            }
        ),
        responses={
            200: inline_serializer(
                name="PresenceToggleResponse",
                fields={
                    "is_online": serializers.BooleanField(),
                    "is_hidden": serializers.BooleanField()
                }
            )
        },
        tags=["Chat"],
        summary="Toggle user presence visibility"
    )
    def post(self, request):
        hide_online = request.data.get("hide_online")

        if hide_online not in [True, False]:
            return Response({"error": "hide_online must be true or false"}, status=400)

        user = request.user
        override = False if hide_online else None

        UserPresence.objects.update_or_create(
            user=user,
            defaults={"is_online_override": override}
        )

        # Refresh the relation so get_user_presence sees updated value
        user.refresh_from_db()

        final_status = get_user_presence(user)

        # Broadcast update
        try:
            channel_layer = get_channel_layer()
            async_to_sync(channel_layer.group_send)(
                "global_presence",
                {
                    "type": "presence_update",
                    "user_id": str(user.id),
                    "status": "user_online" if final_status else "user_offline"
                }
            )
        except Exception:
            pass

        return Response({
            "is_online": final_status,
            "is_hidden": hide_online
        })


# ==============================
# 🔹 TOGGLE READ RECEIPTS
# ==============================
class ToggleReadReceiptView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        request=inline_serializer(
            name="ReadReceiptToggleRequest",
            fields={
                "is_enabled": serializers.BooleanField()
            }
        ),
        responses={
            200: inline_serializer(
                name="ReadReceiptToggleResponse",
                fields={
                    "is_enabled": serializers.BooleanField()
                }
            )
        },
        tags=["Chat"],
        summary="Toggle read receipts visibility"
    )
    def patch(self, request):
        value = request.data.get("is_enabled", True)

        if not isinstance(value, bool):
            return Response(
                {"error": "is_enabled must be true or false"},
                status=status.HTTP_400_BAD_REQUEST
            )

        obj, _ = UserReadReceipt.objects.update_or_create(
            user=request.user,
            defaults={"is_enabled": value}
        )

        return Response({"is_enabled": obj.is_enabled})