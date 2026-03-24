from rest_framework import serializers
from django.contrib.auth import get_user_model
from django.core.cache import cache

from .models import (
    Conversation,
    ConversationParticipant,
    Message,
    MessageRead
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
    read_by = serializers.SerializerMethodField()

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
            "read_by",
        ]

    def get_read_by(self, obj):
        if hasattr(obj, 'messageread_set'):
            return [mr.user_id for mr in getattr(obj, 'messageread_set').all()]
        return []

    def get_is_deleted(self, obj):
        request = self.context.get("request")

        # Deleted for everyone
        if obj.is_deleted_for_everyone:
            return True

        # Deleted for me
        if request and request.user:
            return obj.deleted_for_users.filter(id=request.user.id).exists()

        return False

    def to_representation(self, instance):
        data = super().to_representation(instance)

        if data["is_deleted"]:
            data["content"] = "This message was deleted"
            data["file"] = None

        return data


class ConversationListSerializer(serializers.ModelSerializer):
    last_message = serializers.SerializerMethodField()
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
                "role": p.role,
                "is_online": bool(cache.get(f"online_user_{p.user.id}"))
            }
            for p in participants
        ]

    def get_last_message(self, obj):
        if obj.last_message:
            return {
                "content": obj.last_message.content,
                "created_at": obj.last_message.created_at
            }
        return None


    def get_unread_count(self, obj):
        if hasattr(obj, 'unread_count_annotated'):
            return obj.unread_count_annotated
        return 0


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
                "joined_at": p.joined_at,
                "is_online": bool(cache.get(f"online_user_{p.user.id}"))
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


class RemoveAdminSerializer(serializers.Serializer):
    user_id = serializers.IntegerField()