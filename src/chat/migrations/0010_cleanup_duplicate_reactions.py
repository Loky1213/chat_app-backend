"""
Data migration: Clean up duplicate reactions before applying unique_together = ("message", "user").
For each (message, user) pair with multiple reactions, keeps only the most recent one.
"""
from django.db import migrations


def cleanup_duplicate_reactions(apps, schema_editor):
    MessageReaction = apps.get_model("chat", "MessageReaction")
    from django.db.models import Count, Max

    # Find (message, user) pairs with more than one reaction
    duplicates = (
        MessageReaction.objects.values("message_id", "user_id")
        .annotate(count=Count("id"), latest_id=Max("id"))
        .filter(count__gt=1)
    )

    ids_to_delete = []
    for dup in duplicates:
        # Keep the latest, delete the rest
        ids_to_delete.extend(
            MessageReaction.objects.filter(
                message_id=dup["message_id"],
                user_id=dup["user_id"]
            ).exclude(id=dup["latest_id"]).values_list("id", flat=True)
        )

    if ids_to_delete:
        MessageReaction.objects.filter(id__in=ids_to_delete).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("chat", "0009_messagereaction"),
    ]

    operations = [
        migrations.RunPython(
            cleanup_duplicate_reactions,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
