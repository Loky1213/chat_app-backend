from django.urls import path

from .views import (
    CreatePrivateChatView,
    CreateGroupChatView,
    ConversationListView,
    ConversationDetailView,
    MessageListView,
    AddMembersView,
    RemoveMemberView,
    PromoteAdminView,
    MarkAsReadView,
)

urlpatterns = [

    # ==============================
    # 🔹 CHAT CREATION
    # ==============================
    path("private/create/", CreatePrivateChatView.as_view(), name="create-private-chat"),
    path("group/create/", CreateGroupChatView.as_view(), name="create-group-chat"),

    # ==============================
    # 🔹 CONVERSATIONS
    # ==============================
    path("conversations/", ConversationListView.as_view(), name="conversation-list"),
    path("conversations/<uuid:conversation_id>/", ConversationDetailView.as_view(), name="conversation-detail"),

    # ==============================
    # 🔹 MESSAGES
    # ==============================
    path("conversations/<uuid:conversation_id>/messages/", MessageListView.as_view(), name="message-list"),

    # ==============================
    # 🔹 GROUP MANAGEMENT
    # ==============================
    path("conversations/<uuid:conversation_id>/add-members/", AddMembersView.as_view(), name="add-members"),
    path("conversations/<uuid:conversation_id>/remove-member/<uuid:user_id>/", RemoveMemberView.as_view(), name="remove-member"),
    path("conversations/<uuid:conversation_id>/promote-admin/", PromoteAdminView.as_view(), name="promote-admin"),

    # ==============================
    # 🔹 READ RECEIPTS
    # ==============================
    path("conversations/<uuid:conversation_id>/mark-read/", MarkAsReadView.as_view(), name="mark-as-read"),
]