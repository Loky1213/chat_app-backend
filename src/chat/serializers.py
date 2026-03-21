from rest_framework import serializers
from django.contrib.auth import get_user_model

from .models import (
    Conversation,
    ConversationParticipant,
    Message,
    MessageDeletion
)

from user.serializers import UserAccountSerializer  # 👈 reuse

User = get_user_model()

class ChatUserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ["id", "username"]

class MessageSerializer(serializers.ModelSerializer):
    sender = ChatUserSerializer(read_only=True)
    is_deleted = serializers.SerializerMethodField()

    class Meta:
        model = Message
        fields = [
            "id",
            "sender",
            "content",
            "message_type",
            "file",
            "created_at",
            "is_deleted",
        ]

    def get_is_deleted(self, obj):
        request = self.context.get("request")

        # Deleted for everyone
        if obj.is_deleted_for_everyone:
            return True

        # Deleted for me
        if request and request.user:
            return MessageDeletion.objects.filter(
                message=obj,
                user=request.user
            ).exists()

        return False

    def to_representation(self, instance):
        data = super().to_representation(instance)

        if data["is_deleted"]:
            data["content"] = "This message was deleted"
            data["file"] = None

        return data


class ConversationListSerializer(serializers.ModelSerializer):
    last_message = MessageSerializer(read_only=True)
    participants = serializers.SerializerMethodField()
    unread_count = serializers.SerializerMethodField()

    class Meta:
        model = Conversation
        fields = [
            "id",
            "type",
            "name",
            "image",
            "last_message",
            "participants",
            "unread_count",
            "created_at",
        ]

    def get_participants(self, obj):
        # Iterate over .all() to use prefetched data instead of hitting the DB
        participants = obj.conversationparticipant_set.all()

        return [
            {
                "id": p.user.id,
                "username": p.user.username,
                "role": p.role
            }
            for p in participants
        ]


    def get_unread_count(self, obj):
        request = self.context.get("request")

        if not request or not request.user:
            return 0

        # Iterate prefetched data instead of triggering a DB lookup
        participant = next(
            (p for p in obj.conversationparticipant_set.all() if p.user_id == request.user.id),
            None
        )
        if not participant:
            return 0

        last_read = participant.last_read_message

        if not last_read:
            return obj.messages.count()

        return obj.messages.filter(
            created_at__gt=last_read.created_at
        ).count()


class ConversationDetailSerializer(serializers.ModelSerializer):
    participants = serializers.SerializerMethodField()

    class Meta:
        model = Conversation
        fields = [
            "id",
            "type",
            "name",
            "image",
            "participants",
            "created_at",
        ]


    def get_participants(self, obj):
        participants = obj.conversationparticipant_set.select_related("user")

        return [
            {
                "id": p.user.id,
                "username": p.user.username,
                "role": p.role,
                "joined_at": p.joined_at
            }
            for p in participants
        ]

class CreatePrivateChatSerializer(serializers.Serializer):
    user_id = serializers.IntegerField()


class CreateGroupSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=255)
    user_ids = serializers.ListField(
        child=serializers.IntegerField(),
        allow_empty=False
    )


class AddMembersSerializer(serializers.Serializer):
    user_ids = serializers.ListField(
        child=serializers.IntegerField()
    )


class PromoteAdminSerializer(serializers.Serializer):
    user_id = serializers.IntegerField()