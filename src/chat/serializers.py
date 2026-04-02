from rest_framework import serializers
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db.models import Count
from django.core.cache import cache
from .services import ChatService, get_user_presence
from .models import UserReadReceipt

from .models import (
    Conversation,
    ConversationParticipant,
    Message,
    MessageRead
)

from user.serializers import UserAccountSerializer  

User = get_user_model()

class ChatUserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ["id", "username"]

class MessageSerializer(serializers.ModelSerializer):
    sender = ChatUserSerializer(read_only=True)
    is_deleted = serializers.SerializerMethodField()
    read_by = serializers.SerializerMethodField()
    read_receipts_visible = serializers.SerializerMethodField()
    reply_to = serializers.SerializerMethodField()
    reactions = serializers.SerializerMethodField()

    class Meta:
        model = Message
        fields = [
            "id",
            "sender",
            "content",
            "message_type",
            "file",
            "created_at",
            "created_at",
            "is_deleted",
            "read_by",
            "read_receipts_visible",
            "is_forwarded",
            "reply_to",
            "reactions",
        ]

    def get_read_receipts_visible(self, obj):
        request = self.context.get("request")
        user = None

        if request and hasattr(request, "user"):
            user = request.user
        else:
            user = self.context.get("user")

        if not user:
            return True

        participants = ConversationParticipant.objects.filter(
            conversation=obj.conversation
        ).exclude(user=user).select_related("user__read_receipt")

        for p in participants:
            rr = getattr(p.user, "read_receipt", None)
            if rr and not rr.is_enabled:
                return False

        return True

    def get_read_by(self, obj):
        if not self.get_read_receipts_visible(obj):
            return []

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

    def get_reactions(self, obj):
        request = self.context.get("request")
        user_id = request.user.id if request and hasattr(request, "user") and request.user else None

        # Fast path: use prefetched cache (no extra DB hits)
        if hasattr(obj, "_prefetched_objects_cache") and "reactions" in obj._prefetched_objects_cache:
            reactions_list = list(obj.reactions.all())
            emoji_counts = {}
            user_emojis = set()
            for r in reactions_list:
                emoji_counts[r.emoji] = emoji_counts.get(r.emoji, 0) + 1
                if r.user_id == user_id:
                    user_emojis.add(r.emoji)

            return [
                {
                    "emoji": emoji,
                    "count": count,
                    "user_reacted": emoji in user_emojis
                }
                for emoji, count in emoji_counts.items()
            ]

        # Fallback: DB aggregation
       
        reactions_qs = list(obj.reactions.values("emoji").annotate(count=Count("id")))
        if user_id:
            user_emojis = set(
                obj.reactions.filter(user_id=user_id).values_list("emoji", flat=True)
            )
        else:
            user_emojis = set()

        for r in reactions_qs:
            r["user_reacted"] = r["emoji"] in user_emojis
        return reactions_qs

    def get_reply_to(self, obj):
        if obj.reply_to:
            return {
                "id": obj.reply_to.id,
                "content": "Message deleted" if obj.reply_to.is_deleted_for_everyone else obj.reply_to.content,
                "sender_id": str(obj.reply_to.sender_id),
                "sender_username": obj.reply_to.sender.username
            }
        return None

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
                "is_online": get_user_presence(p.user)
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
        request = self.context.get("request")
        if request and hasattr(request, "user"):
            redis_key = f"chat:unread:{request.user.id}:{obj.id}"
            count = cache.get(redis_key)
            if count is not None:
                return count

        # Fallback to annotation explicitly without triggering per-row DB queries
        if hasattr(obj, 'unread_count_annotated'):
            if request and hasattr(request, "user"):
                cache.set(f"chat:unread:{request.user.id}:{obj.id}", obj.unread_count_annotated, timeout=604800)
            return obj.unread_count_annotated

        # Lowest fallback just in case
        if request and hasattr(request, "user"):
            return ChatService.get_unread_count(request.user.id, obj.id)
            
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
        participants = obj.conversationparticipant_set.select_related("user", "user__presence")

        return [
            {
                "id": p.user.id,
                "username": p.user.username,
                "role": p.role,
                "joined_at": p.joined_at,
                "is_online": get_user_presence(p.user)
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

class ForwardMessageSerializer(serializers.Serializer):
    message_id = serializers.IntegerField()
    target_ids = serializers.ListField(
        child=serializers.IntegerField(),
        allow_empty=False
    )