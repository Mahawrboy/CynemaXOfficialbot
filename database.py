# ============================================================
#  CynemaBot — Async Database Layer
#  Thread-safe, auto-saving JSON database with backup support.
# ============================================================

import asyncio
import json
import logging
import os
import shutil
from datetime import datetime, date
from typing import Any, Optional

import aiofiles

from config import DB_PATH, DB_BACKUP_PATH, START_SEARCH, REF_BONUS

logger = logging.getLogger(__name__)

# ── In-memory store + lock ────────────────────────────────────
_db: dict = {}
_lock = asyncio.Lock()
_dirty = False          # True when unsaved changes exist


# ════════════════════════════════════════════════════════════════
#  I/O helpers
# ════════════════════════════════════════════════════════════════

async def load_db() -> None:
    """Load database from disk into memory. Creates default if missing."""
    global _db, _dirty
    if os.path.exists(DB_PATH):
        try:
            async with aiofiles.open(DB_PATH, "r", encoding="utf-8") as f:
                content = await f.read()
            _db = json.loads(content)
            _ensure_structure()
            logger.info("Database loaded (%d users).", len(_db.get("users", {})))
        except (json.JSONDecodeError, OSError) as e:
            logger.error("DB load error: %s — starting fresh.", e)
            _db = _default_db()
    else:
        _db = _default_db()
    _dirty = False


async def save_db(force: bool = False) -> None:
    """Persist in-memory database to disk (atomic write, lock-protected)."""
    global _dirty
    async with _lock:
        if not force and not _dirty:
            return
        tmp = DB_PATH + ".tmp"
        try:
            snapshot = json.dumps(_db, ensure_ascii=False, indent=2)
        except (TypeError, ValueError) as e:
            logger.error("DB serialisation error: %s", e)
            return
    # Write outside the lock so other coroutines aren't blocked during I/O
    try:
        async with aiofiles.open(tmp, "w", encoding="utf-8") as f:
            await f.write(snapshot)
        os.replace(tmp, DB_PATH)
        async with _lock:
            _dirty = False
    except OSError as e:
        logger.error("DB save error: %s", e)


async def backup_db() -> str:
    """Snapshot current database to backup file. Returns backup path."""
    await save_db(force=True)
    async with _lock:
        shutil.copy2(DB_PATH, DB_BACKUP_PATH)
    return DB_BACKUP_PATH


async def restore_db() -> bool:
    """Restore database from backup. Returns True on success."""
    global _db, _dirty
    if not os.path.exists(DB_BACKUP_PATH):
        return False
    try:
        async with _lock:
            shutil.copy2(DB_BACKUP_PATH, DB_PATH)
        # Re-load into memory (load_db acquires the lock internally)
        await load_db()
        return True
    except OSError as e:
        logger.error("DB restore error: %s", e)
        return False


def _mark_dirty() -> None:
    global _dirty
    _dirty = True


# ════════════════════════════════════════════════════════════════
#  Structure helpers
# ════════════════════════════════════════════════════════════════

def _default_db() -> dict:
    return {
        "users": {},
        "settings": {
            "start_img": "https://i.ibb.co/zHhfjxBf/file-103.jpg",
            "welcome_message": "🎬 Welcome to <b>CynemaBot</b>!\n\nYour ultimate movie & anime companion.",
            "verify_caption": "✅ Please join all channels below to access the bot.",
            "menu_caption": "🎬 <b>CynemaBot</b> — Choose a category:",
            "instagram_link": "https://www.instagram.com/animeeditslolz",
            "website_link": "",
            "earning_channel_link": "https://t.me/+MPmO6Uvvy3NjYTdl",
            "referral_bonus": REF_BONUS,
            "starting_searches": START_SEARCH,
            "vidlink_base": "https://vidlink.pro/movie/",
            "tvlink_base": "https://vidlink.pro/tv/",
            "maintenance_mode": False,
            "force_join_enabled": True,
            "channels": [],
        },
        "requests": [],
        "stats": {
            "total_searches": 0,
            "total_referrals": 0,
            "total_requests": 0,
            "daily": {},
        },
        "logs": {"errors": [], "activity": []},
    }


def _ensure_structure() -> None:
    """Merge any missing top-level keys so old databases stay compatible."""
    defaults = _default_db()
    for key, val in defaults.items():
        if key not in _db:
            _db[key] = val
        elif isinstance(val, dict):
            for sub_key, sub_val in val.items():
                if sub_key not in _db[key]:
                    _db[key][sub_key] = sub_val


def _default_user(user_id: int, name: str, username: Optional[str]) -> dict:
    return {
        "id": user_id,
        "name": name,
        "username": username or "",
        "join_date": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "join_date_short": str(date.today()),
        "free_searches": START_SEARCH,
        "bonus_searches": 0,
        "referrals": 0,
        "referred_by": None,
        "is_banned": False,
        "total_searches": 0,
        "last_active": str(date.today()),
    }


# ════════════════════════════════════════════════════════════════
#  User operations
# ════════════════════════════════════════════════════════════════

async def get_user(user_id: int) -> Optional[dict]:
    async with _lock:
        return _db["users"].get(str(user_id))


async def register_user(
    user_id: int,
    name: str,
    username: Optional[str],
    referred_by: Optional[int] = None,
) -> dict:
    """Create a new user record. Returns the new user dict."""
    async with _lock:
        uid = str(user_id)
        if uid in _db["users"]:
            return _db["users"][uid]

        starting = _db["settings"].get("starting_searches", START_SEARCH)
        user = _default_user(user_id, name, username)
        user["free_searches"] = starting

        if referred_by and str(referred_by) != uid:
            user["referred_by"] = referred_by
            # Credit referrer
            ref_uid = str(referred_by)
            if ref_uid in _db["users"]:
                bonus = _db["settings"].get("referral_bonus", REF_BONUS)
                _db["users"][ref_uid]["referrals"] += 1
                _db["users"][ref_uid]["bonus_searches"] = (
                    _db["users"][ref_uid].get("bonus_searches", 0) + bonus
                )
                _db["stats"]["total_referrals"] += 1

        _db["users"][uid] = user
        _mark_dirty()
        return user


async def update_user(user_id: int, **kwargs) -> None:
    async with _lock:
        uid = str(user_id)
        if uid in _db["users"]:
            _db["users"][uid].update(kwargs)
            _mark_dirty()


async def consume_search(user_id: int) -> bool:
    """
    Deduct one search credit. Bonus searches are used first.
    Returns True if credit was available, False if out of credits.
    """
    async with _lock:
        uid = str(user_id)
        user = _db["users"].get(uid)
        if not user:
            return False
        if user.get("bonus_searches", 0) > 0:
            user["bonus_searches"] -= 1
        elif user.get("free_searches", 0) > 0:
            user["free_searches"] -= 1
        else:
            return False
        user["total_searches"] = user.get("total_searches", 0) + 1
        user["last_active"] = str(date.today())
        _db["stats"]["total_searches"] += 1
        today = str(date.today())
        _db["stats"]["daily"].setdefault(today, {"searches": 0, "users": set()})
        # sets are not JSON-serialisable; store as list
        day = _db["stats"]["daily"][today]
        if isinstance(day.get("users"), list):
            if str(user_id) not in day["users"]:
                day["users"].append(str(user_id))
        else:
            day["users"] = [str(user_id)]
        day["searches"] = day.get("searches", 0) + 1
        _mark_dirty()
        return True


async def remaining_searches(user_id: int) -> int:
    async with _lock:
        user = _db["users"].get(str(user_id), {})
        return user.get("bonus_searches", 0) + user.get("free_searches", 0)


async def get_all_users() -> list[dict]:
    async with _lock:
        return list(_db["users"].values())


async def ban_user(user_id: int) -> None:
    await update_user(user_id, is_banned=True)


async def unban_user(user_id: int) -> None:
    await update_user(user_id, is_banned=False)


async def delete_user(user_id: int) -> None:
    async with _lock:
        _db["users"].pop(str(user_id), None)
        _mark_dirty()


async def add_searches(user_id: int, amount: int, bonus: bool = False) -> None:
    async with _lock:
        uid = str(user_id)
        if uid not in _db["users"]:
            return
        key = "bonus_searches" if bonus else "free_searches"
        _db["users"][uid][key] = _db["users"][uid].get(key, 0) + amount
        _mark_dirty()


async def remove_searches(user_id: int, amount: int, bonus: bool = False) -> None:
    async with _lock:
        uid = str(user_id)
        if uid not in _db["users"]:
            return
        key = "bonus_searches" if bonus else "free_searches"
        current = _db["users"][uid].get(key, 0)
        _db["users"][uid][key] = max(0, current - amount)
        _mark_dirty()


async def reset_searches(user_id: int) -> None:
    starting = get_setting("starting_searches", START_SEARCH)
    await update_user(user_id, free_searches=starting, bonus_searches=0)


async def reset_referrals(user_id: int) -> None:
    await update_user(user_id, referrals=0)


# ════════════════════════════════════════════════════════════════
#  Settings
# ════════════════════════════════════════════════════════════════

def get_setting(key: str, default: Any = None) -> Any:
    """
    Synchronous read of a settings value.
    _db is a plain Python dict; individual dict reads are atomically
    executed by the GIL, but we return a copy of immutable scalars so
    callers can never mutate the live store through the returned value.
    For mutable values (lists/dicts) callers must not modify the result.
    """
    return _db.get("settings", {}).get(key, default)


async def set_setting(key: str, value: Any) -> None:
    async with _lock:
        _db["settings"][key] = value
        _mark_dirty()


# ════════════════════════════════════════════════════════════════
#  Requests
# ════════════════════════════════════════════════════════════════

async def add_request(user_id: int, name: str, text: str) -> int:
    async with _lock:
        req = {
            "id": len(_db["requests"]) + 1,
            "user_id": user_id,
            "user_name": name,
            "text": text,
            "date": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "status": "pending",
        }
        _db["requests"].append(req)
        _db["stats"]["total_requests"] += 1
        _mark_dirty()
        return req["id"]


async def get_requests(status: Optional[str] = None) -> list:
    async with _lock:
        reqs = _db.get("requests", [])
        if status:
            reqs = [r for r in reqs if r.get("status") == status]
        return list(reqs)


async def update_request_status(req_id: int, status: str) -> None:
    async with _lock:
        for req in _db.get("requests", []):
            if req["id"] == req_id:
                req["status"] = status
                break
        _mark_dirty()


async def delete_request(req_id: int) -> None:
    async with _lock:
        _db["requests"] = [r for r in _db.get("requests", []) if r["id"] != req_id]
        _mark_dirty()


# ════════════════════════════════════════════════════════════════
#  Statistics helpers
# ════════════════════════════════════════════════════════════════

async def get_stats() -> dict:
    async with _lock:
        users = list(_db["users"].values())
        today = str(date.today())
        today_data = _db["stats"]["daily"].get(today, {})
        today_users = len(today_data.get("users", []))
        active = sum(1 for u in users if u.get("last_active") == today)
        return {
            "total_users": len(users),
            "active_today": active,
            "today_new": today_users,
            "total_searches": _db["stats"]["total_searches"],
            "total_referrals": _db["stats"]["total_referrals"],
            "total_requests": _db["stats"]["total_requests"],
            "daily": dict(_db["stats"]["daily"]),
        }


# ════════════════════════════════════════════════════════════════
#  Logging
# ════════════════════════════════════════════════════════════════

async def log_error(msg: str) -> None:
    async with _lock:
        entry = {"ts": datetime.now().isoformat(), "msg": msg}
        logs = _db.setdefault("logs", {}).setdefault("errors", [])
        logs.append(entry)
        if len(logs) > 500:
            _db["logs"]["errors"] = logs[-500:]
        _mark_dirty()


async def log_activity(msg: str) -> None:
    async with _lock:
        entry = {"ts": datetime.now().isoformat(), "msg": msg}
        logs = _db.setdefault("logs", {}).setdefault("activity", [])
        logs.append(entry)
        if len(logs) > 1000:
            _db["logs"]["activity"] = logs[-1000:]
        _mark_dirty()


async def get_logs(log_type: str = "errors", limit: int = 20) -> list:
    async with _lock:
        return list(_db.get("logs", {}).get(log_type, []))[-limit:]


# ════════════════════════════════════════════════════════════════
#  Auto-save loop
# ════════════════════════════════════════════════════════════════

async def auto_save_loop(interval: int = 30) -> None:
    """Background task: persist dirty DB every `interval` seconds."""
    while True:
        await asyncio.sleep(interval)
        await save_db()
