# ============================================================
#  CynemaBot — Force Join Verification
#  Checks membership in both public and private channels.
# ============================================================

import logging
from typing import Optional

from telegram import Bot
from telegram.error import TelegramError, ChatMigrated

from config import CHANNELS
import database as db

logger = logging.getLogger(__name__)


async def is_member(bot: Bot, user_id: int, channel_id) -> bool:
    """
    Return True if user_id is a member/admin/owner/restricted of the channel.
    Works for both public (@handle) and private (-100...) channels.

    Fail-closed: any API error returns False so force-join cannot be bypassed
    by triggering error conditions (misconfigured bot permissions, invalid ID, etc).
    The user will see the join prompt and can press Verify again once the issue
    is resolved on the admin side.
    """
    try:
        member = await bot.get_chat_member(chat_id=channel_id, user_id=user_id)
        return member.status in ("member", "administrator", "creator", "restricted")
    except TelegramError as e:
        logger.warning(
            "Membership check failed (fail-closed) — channel=%s user=%s: %s",
            channel_id, user_id, e,
        )
        return False  # fail-closed: treat unknown as not-joined


async def check_force_join(bot: Bot, user_id: int) -> tuple[bool, list[dict]]:
    """
    Check if user has joined all required channels.

    Returns:
        (all_joined: bool, missing_channels: list[dict])
        missing_channels contains the channels the user hasn't joined yet.
    """
    if not db.get_setting("force_join_enabled", True):
        return True, []

    force_channels = [ch for ch in CHANNELS if ch.get("force_join")]
    missing = []

    for ch in force_channels:
        joined = await is_member(bot, user_id, ch["id"])
        if not joined:
            missing.append(ch)

    return len(missing) == 0, missing
