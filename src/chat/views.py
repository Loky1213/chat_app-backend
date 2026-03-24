from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework import status
from drf_spectacular.utils import extend_schema, OpenApiResponse

from django.db.models import Count, Q, F
from .models import Conversation, Message
from .serializers import (
    ConversationListSerializer,
    ConversationDetailSerializer,
    MessageSerializer,
    CreatePrivateChatSerializer,
    CreateGroupSerializer,
    AddMembersSerializer,
    PromoteAdminSerializer,
    RemoveAdminSerializer,
)

from .services import ChatService
from utils.api_response import success_response, error_response
from utils.pagination import StandardPagination, MessageCursorPagination
import logging

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
            "conversationparticipant_set__user"
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
        ).select_related("sender").prefetch_related(
            "messageread_set"
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