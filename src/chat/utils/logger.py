import logging
from typing import Optional

logger = logging.getLogger("chat")


def log_chat_event(
    action: str,
    user_id: Optional[int] = None,
    conversation_id: Optional[int] = None,
    message_id: Optional[int] = None,
    extra: Optional[dict] = None
):
    try:
        log_data = {
            "action": action,
            "user": user_id,
            "conversation": conversation_id,
            "message": message_id,
            "extra": extra or {}
        }

        logger.info(f"[CHAT_EVENT] {log_data}")

    except Exception:
        pass

