from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Count
from django.utils import timezone
from django.db.models import Q
from django.core.cache import cache
from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync
from .models import MessageRead
from django.db.models import Count
from .models import MessageReaction
        



from rest_framework.exceptions import PermissionDenied, NotFound

from .models import (
    Conversation,
    ConversationParticipant,
    Message,
)
import logging

from .utils.logger import log_chat_event
from .constants import (
    MESSAGE_SENT, MESSAGE_DELETED, MESSAGE_FORWARDED,
    REACTION_ADDED, REACTION_REMOVED, MESSAGE_READ
)

logger = logging.getLogger(__name__)

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

        logger.info(f"Private chat created: {conversation.id} between users {user1.id} and {user2.id}")
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
                role="admin",
                is_creator=True
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

        logger.info(f"Group chat '{name}' created: {conversation.id} by user {creator.id}")
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

        try:
            target = ConversationParticipant.objects.get(
                conversation=conversation,
                user_id=user_id
            )
        except ConversationParticipant.DoesNotExist:
            raise NotFound("User not in conversation")

        if target.is_creator:
            raise PermissionDenied("Cannot remove the group creator")

        target.delete()

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
    @transaction.atomic
    def send_message(user, conversation_id, content=None, message_type="text", file=None, reply_to_id=None):
        try:
            conversation = Conversation.objects.select_for_update().get(id=conversation_id)
        except Conversation.DoesNotExist:
            raise NotFound("Conversation not found")

        # Check membership
        if not ConversationParticipant.objects.filter(
            user=user,
            conversation=conversation
        ).exists():
            raise PermissionDenied("Not part of this conversation")

        reply_to = None
        if reply_to_id:
            try:
                reply_to = Message.objects.get(id=reply_to_id, conversation_id=conversation_id)
            except Message.DoesNotExist:
                pass

        message = Message.objects.create(
            conversation=conversation,
            sender=user,
            content=content,
            message_type=message_type,
            file=file,
            reply_to=reply_to
        )

        # Update last message safely with timestamp guard
        Conversation.objects.filter(
            id=conversation_id
        ).filter(
            Q(last_message__isnull=True) | Q(last_message__created_at__lt=message.created_at)
        ).update(last_message=message)

        logger.info(f"Message sent in {conversation_id} by {user.id}")

        log_chat_event(
            action=MESSAGE_SENT,
            user_id=user.id,
            conversation_id=conversation.id,
            message_id=message.id,
            extra={"length": len(message.content) if message.content else 0}
        )

        return message

    # ==============================
    # 🔹 FORWARD MESSAGE
    # ==============================
    @staticmethod
    @transaction.atomic
    def forward_message(user, message_id, target_conversation_ids, reply_to_id=None):
        try:
            original_message = Message.objects.get(id=message_id)
        except Message.DoesNotExist:
            raise NotFound("Message not found")

        targets = list(Conversation.objects.filter(
            id__in=target_conversation_ids,
            conversationparticipant__user=user
        ).distinct().select_for_update())

        if not targets:
            raise NotFound("Conversations not found or not allowed")

        reply_to = None
        if reply_to_id:
            try:
                # Issue 6: ensure reply_to is isolated per same conversation logically. Will only apply if forwarding target exactly matches
                reply_to_msg = Message.objects.get(id=reply_to_id)
            except Message.DoesNotExist:
                reply_to_msg = None
        else:
            reply_to_msg = None

        new_messages = []
        for conv in targets:
            filtered_reply_to = reply_to_msg if (reply_to_msg and reply_to_msg.conversation_id == conv.id) else None

            new_messages.append(
                Message(
                    conversation=conv,
                    sender=user,
                    content=original_message.content,
                    message_type=original_message.message_type,
                    file=original_message.file,
                    is_forwarded=True,
                    original_message_id=original_message.id,
                    reply_to=filtered_reply_to
                )
            )

        # Issue 1: bulk_create returns incomplete data
        created_messages = Message.objects.bulk_create(new_messages)
        message_ids = [msg.id for msg in created_messages]

        # Re-fetch from DB to get reliable IDs and created_at timestamps
        refetched_messages = list(Message.objects.filter(
            id__in=message_ids
        ).select_related("sender"))
        
        # Issue 2: Safe per-conversation update (Race Condition Protected)
       
        for msg in refetched_messages:
            Conversation.objects.filter(
                id=msg.conversation_id
            ).filter(
                Q(last_message__isnull=True) | Q(last_message__created_at__lt=msg.created_at)
            ).update(last_message=msg)

        for msg in refetched_messages:
            log_chat_event(
                action=MESSAGE_FORWARDED,
                user_id=user.id,
                message_id=msg.id,
                extra={"targets": target_conversation_ids}
            )

        return refetched_messages

    # ==============================
    # 🔹 UNREAD COUNTERS
    # ==============================
    @staticmethod
    def get_unread_count_fallback(user_id, conversation_id):
        # Fallback to pure DB count tracking read states safely
        return Message.objects.filter(
            conversation_id=conversation_id
        ).exclude(
            sender_id=user_id
        ).exclude(
            messageread__user_id=user_id
        ).count()

    @staticmethod
    def get_unread_count(user_id, conversation_id):
        
        redis_key = f"chat:unread:{user_id}:{conversation_id}"
        count = cache.get(redis_key)
        
        # Issue 2: REDIS / DB DRIFT
        db_count = ChatService.get_unread_count_fallback(user_id, conversation_id)
        if count != db_count:
            cache.set(redis_key, db_count, timeout=604800)
            return db_count
            
        return count

    @staticmethod
    def increment_unread_count(user_id, conversation_id):
        redis_key = f"chat:unread:{user_id}:{conversation_id}"
        
        # Issue 1: ATOMIC SAFE INITIALIZATION
        if cache.add(redis_key, 1, timeout=604800):
            return 1
        return cache.incr(redis_key)

    @staticmethod
    def reset_unread_count(user_id, conversation_id):
        redis_key = f"chat:unread:{user_id}:{conversation_id}"
        
        # Issue 4: SAFE RESET
        cache.set(redis_key, 0, timeout=604800)
        
        # Issue 3: MULTI-DEVICE SYNC
        channel_layer = get_channel_layer()
        async_to_sync(channel_layer.group_send)(
            f"user_{user_id}",
            {
                "type": "unread_reset",
                "conversation_id": conversation_id
            }
        )

    # ==============================
    # 🔹 MARK AS READ (CONVERSATION)
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

        # Bulks create MessageReads for legacy fallback
        unread_messages = conversation.messages.exclude(
            sender=user
        ).exclude(
            messageread__user=user
        )

        message_reads = [
            MessageRead(message=msg, user=user) 
            for msg in unread_messages
        ]
        if message_reads:
            MessageRead.objects.bulk_create(message_reads, ignore_conflicts=True)

        participant.last_read_message = last_message
        participant.save(update_fields=["last_read_message"])

        log_chat_event(
            action=MESSAGE_READ,
            user_id=user.id,
            conversation_id=conversation.id
        )


    # ==============================
    # 🔹 DELETE MESSAGE
    # ==============================
    @staticmethod
    def delete_message(request_user, message_id, mode):
        try:
            message = Message.objects.get(id=message_id)
        except Message.DoesNotExist:
            raise NotFound("Message not found")

        # Check membership
        if not ConversationParticipant.objects.filter(
            user=request_user,
            conversation=message.conversation
        ).exists():
            raise PermissionDenied("Not part of this conversation")

        if mode == "everyone":
            if message.sender != request_user:
                raise PermissionDenied("Only sender can delete for everyone")
            message.is_deleted_for_everyone = True
            message.deleted_at = timezone.now()
            message.save(update_fields=["is_deleted_for_everyone", "deleted_at"])
        elif mode == "me":
            message.deleted_for_users.add(request_user)
        else:
            raise ValueError("Invalid mode")

        log_chat_event(
            action=MESSAGE_DELETED,
            user_id=request_user.id,
            message_id=message.id
        )

        return message

    # ==============================
    # 🔹 REMOVE ADMIN
    # ==============================
    @staticmethod
    def remove_admin(request_user, conversation_id, user_id):
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
            raise PermissionDenied("Only admin can demote members")

        try:
            target = ConversationParticipant.objects.get(
                conversation=conversation,
                user_id=user_id
            )
        except ConversationParticipant.DoesNotExist:
            raise NotFound("User not in conversation")

        if target.is_creator:
            raise PermissionDenied("Cannot demote the group creator")

        if target.role == "admin":
            target.role = "member"
            target.save(update_fields=["role"])

        return True

    # ==============================
    # 🔹 MARK MESSAGE AS SEEN
    # ==============================
    @staticmethod
    def mark_message_seen(user, message_id):
        try:
            message = Message.objects.get(id=message_id)
        except Message.DoesNotExist:
            raise NotFound("Message not found")

        try:
            participant = ConversationParticipant.objects.get(
                user=user,
                conversation=message.conversation
            )
        except ConversationParticipant.DoesNotExist:
            raise PermissionDenied("Not part of this conversation")

        # Create MessageRead
        MessageRead.objects.get_or_create(
            message=message,
            user=user
        )

        participant.save(update_fields=["last_read_message"])

        return True


    # ==============================
    # 🔹 TOGGLE REACTION
    # ==============================
    @staticmethod
    def toggle_reaction(user, message_id, emoji):

        if not emoji or not isinstance(emoji, str):
            raise PermissionDenied("Invalid emoji")

        emoji = emoji.strip()

        if not emoji or len(emoji) > 10:
            raise PermissionDenied("Invalid emoji")

        try:
            message = Message.objects.get(id=message_id)
        except Message.DoesNotExist:
            raise NotFound("Message not found")

        if not ConversationParticipant.objects.filter(
            user=user,
            conversation=message.conversation
        ).exists():
            raise PermissionDenied("Not part of this conversation")

        if message.is_deleted_for_everyone:
            raise PermissionDenied("Cannot react to deleted message")

        with transaction.atomic():
            existing = MessageReaction.objects.filter(
                message=message, user=user
            ).select_for_update().first()

            if existing:
                if existing.emoji == emoji:
                    # Same emoji → toggle off
                    existing.delete()
                    log_chat_event(
                        action=REACTION_REMOVED,
                        user_id=user.id,
                        message_id=message.id,
                        extra={"emoji": emoji}
                    )
                else:
                    # Different emoji → replace
                    existing.emoji = emoji
                    existing.save(update_fields=["emoji"])
                    log_chat_event(
                        action=REACTION_ADDED,
                        user_id=user.id,
                        message_id=message.id,
                        extra={"emoji": emoji}
                    )
            else:
                # No reaction → create
                MessageReaction.objects.create(
                    message=message, user=user, emoji=emoji
                )
                log_chat_event(
                    action=REACTION_ADDED,
                    user_id=user.id,
                    message_id=message.id,
                    extra={"emoji": emoji}
                )

        # DB-level aggregation (no in-memory counting)
        reactions_qs = list(
            MessageReaction.objects.filter(message_id=message_id)
            .values("emoji")
            .annotate(count=Count("id"))
        )

        # Single query for user's current reaction
        user_emoji = MessageReaction.objects.filter(
            message_id=message_id, user=user
        ).values_list("emoji", flat=True).first()

        return [
            {
                "emoji": r["emoji"],
                "count": r["count"],
                "user_reacted": r["emoji"] == user_emoji
            }
            for r in reactions_qs
        ]