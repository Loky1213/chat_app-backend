from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Count
from django.utils import timezone

from rest_framework.exceptions import PermissionDenied, NotFound

from .models import (
    Conversation,
    ConversationParticipant,
    Message,
)

User = get_user_model()


class ChatService:

    # ==============================
    # 🔹 CREATE PRIVATE CHAT
    # ==============================
    @staticmethod
    @transaction.atomic
    def create_private_chat(user1, user2_id):
        try:
            user2 = User.objects.get(id=user2_id)
        except User.DoesNotExist:
            raise NotFound("User not found")

        # Prevent self chat
        if user1.id == user2.id:
            raise PermissionDenied("Cannot create chat with yourself")

        # Check existing conversation strictly with exactly these two users
        existing = Conversation.objects.filter(
            type="private",
            conversationparticipant__user=user1
        ).filter(
            conversationparticipant__user=user2
        ).annotate(
            num_participants=Count("conversationparticipant")
        ).filter(
            num_participants=2
        ).distinct().first()

        if existing:
            return existing

        # Create conversation
        conversation = Conversation.objects.create(type="private")

        # Add participants
        ConversationParticipant.objects.bulk_create([
            ConversationParticipant(user=user1, conversation=conversation),
            ConversationParticipant(user=user2, conversation=conversation),
        ])

        return conversation


    # ==============================
    # 🔹 CREATE GROUP CHAT
    # ==============================
    @staticmethod
    @transaction.atomic
    def create_group_chat(creator, name, user_ids):
        users = User.objects.filter(id__in=user_ids)

        conversation = Conversation.objects.create(
            type="group",
            name=name
        )

        participants = []

        # Creator → admin
        participants.append(
            ConversationParticipant(
                user=creator,
                conversation=conversation,
                role="admin"
            )
        )

        # Add other users
        for user in users:
            if user.id != creator.id:
                participants.append(
                    ConversationParticipant(
                        user=user,
                        conversation=conversation,
                        role="member"
                    )
                )

        ConversationParticipant.objects.bulk_create(participants)

        return conversation


    # ==============================
    # 🔹 ADD MEMBERS
    # ==============================
    @staticmethod
    def add_members(request_user, conversation_id, user_ids):
        try:
            conversation = Conversation.objects.get(id=conversation_id)
        except Conversation.DoesNotExist:
            raise NotFound("Conversation not found")

        # Check admin
        try:
            participant = ConversationParticipant.objects.get(
                user=request_user,
                conversation=conversation
            )
        except ConversationParticipant.DoesNotExist:
            raise PermissionDenied("You are not part of this conversation")

        if participant.role != "admin":
            raise PermissionDenied("Only admin can add members")

        users = User.objects.filter(id__in=user_ids)

        existing_users = ConversationParticipant.objects.filter(
            conversation=conversation,
            user__in=users
        ).values_list("user_id", flat=True)

        new_participants = [
            ConversationParticipant(user=user, conversation=conversation)
            for user in users if user.id not in existing_users
        ]

        ConversationParticipant.objects.bulk_create(new_participants)

        return True


    # ==============================
    # 🔹 REMOVE MEMBER
    # ==============================
    @staticmethod
    def remove_member(request_user, conversation_id, user_id):
        try:
            conversation = Conversation.objects.get(id=conversation_id)
        except Conversation.DoesNotExist:
            raise NotFound("Conversation not found")

        # Check admin
        try:
            admin = ConversationParticipant.objects.get(
                user=request_user,
                conversation=conversation
            )
        except ConversationParticipant.DoesNotExist:
            raise PermissionDenied("You are not part of this conversation")

        if admin.role != "admin":
            raise PermissionDenied("Only admin can remove members")

        # Prevent removing self if last admin (optional logic)
        ConversationParticipant.objects.filter(
            conversation=conversation,
            user_id=user_id
        ).delete()

        return True


    # ==============================
    # 🔹 PROMOTE TO ADMIN
    # ==============================
    @staticmethod
    def promote_to_admin(request_user, conversation_id, user_id):
        try:
            conversation = Conversation.objects.get(id=conversation_id)
        except Conversation.DoesNotExist:
            raise NotFound("Conversation not found")

        try:
            admin = ConversationParticipant.objects.get(
                user=request_user,
                conversation=conversation
            )
        except ConversationParticipant.DoesNotExist:
            raise PermissionDenied("You are not part of this conversation")

        if admin.role != "admin":
            raise PermissionDenied("Only admin can promote members")

        try:
            target = ConversationParticipant.objects.get(
                conversation=conversation,
                user_id=user_id
            )
        except ConversationParticipant.DoesNotExist:
            raise NotFound("User not in conversation")

        target.role = "admin"
        target.save(update_fields=["role"])

        return True


    # ==============================
    # 🔹 SEND MESSAGE
    # ==============================
    @staticmethod
    def send_message(user, conversation_id, content=None, message_type="text", file=None):
        try:
            conversation = Conversation.objects.get(id=conversation_id)
        except Conversation.DoesNotExist:
            raise NotFound("Conversation not found")

        # Check membership
        if not ConversationParticipant.objects.filter(
            user=user,
            conversation=conversation
        ).exists():
            raise PermissionDenied("Not part of this conversation")

        message = Message.objects.create(
            conversation=conversation,
            sender=user,
            content=content,
            message_type=message_type,
            file=file
        )

        # Update last message
        conversation.last_message = message
        conversation.save(update_fields=["last_message"])

        return message


    # ==============================
    # 🔹 MARK AS READ
    # ==============================
    @staticmethod
    def mark_as_read(user, conversation_id):
        try:
            conversation = Conversation.objects.get(id=conversation_id)
        except Conversation.DoesNotExist:
            raise NotFound("Conversation not found")

        last_message = conversation.last_message

        if not last_message:
            return

        try:
            participant = ConversationParticipant.objects.get(
                user=user,
                conversation=conversation
            )
        except ConversationParticipant.DoesNotExist:
            raise PermissionDenied("Not part of this conversation")

        participant.last_read_message = last_message
        participant.save(update_fields=["last_read_message"])