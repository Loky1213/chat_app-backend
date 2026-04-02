from django.db.models.signals import post_save
from django.dispatch import receiver
from django.contrib.auth import get_user_model

from .models import UserReadReceipt

User = get_user_model()


@receiver(post_save, sender=User)
def create_read_receipt(sender, instance, created, **kwargs):
    """Auto-create a UserReadReceipt record for every new user."""
    if created:
        UserReadReceipt.objects.create(user=instance)
