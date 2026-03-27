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
    RemoveAdminView,
    MarkAsReadView,
    ForwardMessageView,
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
    path("conversations/<int:conversation_id>/", ConversationDetailView.as_view(), name="conversation-detail"),

    # ==============================
    # 🔹 MESSAGES
    # ==============================
    path("conversations/<int:conversation_id>/messages/", MessageListView.as_view(), name="message-list"),

    # ==============================
    # 🔹 GROUP MANAGEMENT
    # ==============================
    path("conversations/<int:conversation_id>/add-members/", AddMembersView.as_view(), name="add-members"),
    path("conversations/<int:conversation_id>/remove-member/<int:user_id>/", RemoveMemberView.as_view(), name="remove-member"),
    path("conversations/<int:conversation_id>/promote-admin/", PromoteAdminView.as_view(), name="promote-admin"),
    path("conversations/<int:conversation_id>/remove-admin/", RemoveAdminView.as_view(), name="remove-admin"),

    # ==============================
    # 🔹 READ RECEIPTS
    # ==============================
    path("conversations/<int:conversation_id>/mark-read/", MarkAsReadView.as_view(), name="mark-as-read"),

    # ==============================
    # 🔹 FORWARD MESSAGES
    # ==============================
    path("messages/forward/", ForwardMessageView.as_view(), name="forward-messages"),
]