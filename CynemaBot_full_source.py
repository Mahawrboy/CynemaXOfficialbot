#  python-telegram-bot v21+ | Configured for 24/7 VM Deployment
#
#  Architecture for reliability:
#  ┌─────────────────────────────────────────────────────────┐
#  │  main()  ←  outer retry loop (exponential backoff)      │
#  │    └─ _run_bot()  ←  single PTB Application lifecycle   │
#  │         └─ run_polling()  ←  PTB handles network retry  │
#  │                                                         │
#  │  _start_health_server()  ←  daemon thread (HTTP :PORT)  │
#  │    responds 200 OK to Replit's uptime / health probes   │
#  └─────────────────────────────────────────────────────────┘
#
#  IMPORTANT (PTB v21+):
#  run_polling() owns the asyncio event loop — never wrap it
#  in asyncio.run().  All async startup lives in post_init().
# ============================================================

# ======================================================================
# FILE: bot.py
# ======================================================================

# ============================================================
#  CynemaBot — Main Entry Point
#  python-telegram-bot v21+ | Configured for 24/7 VM Deployment
#
#  Architecture for reliability:
#  ┌─────────────────────────────────────────────────────────┐
#  │  main()  ←  outer retry loop (exponential backoff)      │
#  │    └─ _run_bot()  ←  single PTB Application lifecycle   │
#  │         └─ run_polling()  ←  PTB handles network retry  │
#  │                                                         │
#  │  _start_health_server()  ←  daemon thread (HTTP :PORT)  │
#  │    responds 200 OK to Replit's uptime / health probes   │
#  └─────────────────────────────────────────────────────────┘
#
#  IMPORTANT (PTB v21+):
#  run_polling() owns the asyncio event loop — never wrap it
#  in asyncio.run().  All async startup lives in post_init().
# ============================================================

import asyncio
import logging
import os
import sys
import time
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
)
from telegram.error import TelegramError, Conflict, NetworkError

import database as db
import tmdb as tmdb_api
from config import BOT_TOKEN, DB_SAVE_INTERVAL, LOG_LEVEL, ADMIN_ID
from handlers.start import start_handler, verify_callback
from handlers.search import (
    movies_handler, anime_handler, webseries_handler,
    search_text_handler, select_callback,
    cancel_search_callback, back_to_menu_callback, copy_ref_callback,
)
from handlers.stats import stats_handler, invite_handler
from handlers.request import request_handler, request_text_handler
from handlers.admin import adminpanel_handler, admin_callback, admin_text_handler

# ── Logging setup ─────────────────────────────────────────────
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("bot.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)

# Silence noisy libraries
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)
logging.getLogger("aiohttp").setLevel(logging.WARNING)


# ════════════════════════════════════════════════════════════════
#  Minimal HTTP health-check server (daemon thread)
#
#  Replit's deployment system probes the app via HTTP to verify
#  it is alive. This lightweight server satisfies those probes
#  without interfering with the bot's asyncio loop.
# ════════════════════════════════════════════════════════════════

# Shared mutable flag: set once post_init() confirms the bot is polling
# Telegram successfully, cleared while a crash/retry backoff is in progress.
# A plain bool is safe here — the GIL serializes the single read/write ops
# and the health thread only ever reads it.
_bot_healthy = {"ok": False}


class _HealthHandler(BaseHTTPRequestHandler):
    """
    Reflect actual bot health, not just "the process is alive".

    Returns 200 only while PTB has successfully completed post_init()
    (i.e. it reached Telegram and started polling). Returns 503 during
    startup or while the outer retry loop is backing off after a crash,
    so Replit's monitoring doesn't report false-positive uptime.
    """

    def do_GET(self) -> None:
        healthy = _bot_healthy["ok"]
        body = b"OK" if healthy else b"UNHEALTHY: bot not polling"
        self.send_response(200 if healthy else 503)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args) -> None:
        pass  # suppress per-request noise in logs


def _start_health_server() -> None:
    """Start the health-check HTTP server in a daemon thread."""
    port = int(os.environ.get("PORT", 8080))
    try:
        server = HTTPServer(("0.0.0.0", port), _HealthHandler)
        thread = threading.Thread(
            target=server.serve_forever,
            daemon=True,          # exits automatically when the main process exits
            name="health-server",
        )
        thread.start()
        logger.info("Health-check server listening on :%d.", port)
    except OSError as exc:
        # Non-fatal — the bot still works without HTTP health checks.
        logger.warning("Could not start health-check server on :%d: %s", port, exc)


# ════════════════════════════════════════════════════════════════
#  Combined text router
# ════════════════════════════════════════════════════════════════

async def text_router(update: Update, ctx) -> None:
    text = (update.message.text or "").strip()
    user_id = update.effective_user.id

    # ── Admin text states ─────────────────────────────────────
    if user_id == ADMIN_ID and ctx.user_data.get("adm_state"):
        await admin_text_handler(update, ctx)
        return

    # ── Search state ──────────────────────────────────────────
    if ctx.user_data.get("waiting_search_query"):
        await search_text_handler(update, ctx)
        return

    # ── Request state ─────────────────────────────────────────
    if ctx.user_data.get("waiting_request_text"):
        await request_text_handler(update, ctx)
        return

    # ── Reply-keyboard menu buttons ───────────────────────────
    if text == "🎬 Movies":
        await movies_handler(update, ctx)
    elif text == "🌸 Anime":
        await anime_handler(update, ctx)
    elif text == "📺 Web Series":
        await webseries_handler(update, ctx)
    elif text == "👥 Invite":
        await invite_handler(update, ctx)
    elif text == "📊 My Stats":
        await stats_handler(update, ctx)
    elif text == "📩 Movie Request":
        await request_handler(update, ctx)
    # Unknown text — silently ignore


# ════════════════════════════════════════════════════════════════
#  Error handler
# ════════════════════════════════════════════════════════════════

async def error_handler(update: object, ctx) -> None:
    error = ctx.error

    # ── Infrastructure-level errors — not user-caused ─────────
    if isinstance(error, Conflict):
        # Another bot instance is polling; PTB will retry automatically.
        logger.warning("Conflict — duplicate instance detected: %s", error)
        return
    if isinstance(error, NetworkError):
        # Transient connectivity blip — PTB's Updater retries automatically.
        logger.warning("Network error (transient, PTB will retry): %s", error)
        return

    # ── Unexpected errors ─────────────────────────────────────
    logger.error("Unhandled exception in handler:", exc_info=error)
    await db.log_error(str(error))

    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text(
                "⚠️ Something went wrong. Please try again later."
            )
        except TelegramError:
            pass


# ════════════════════════════════════════════════════════════════
#  Application lifecycle hooks
# ════════════════════════════════════════════════════════════════

async def post_init(app: Application) -> None:
    """Async startup: DB load, aiohttp session, background tasks."""
    await db.load_db()
    logger.info("Database loaded (%d users).", len(await db.get_all_users()))

    await tmdb_api.init_session()
    logger.info("TMDB aiohttp session ready.")

    asyncio.create_task(db.auto_save_loop(DB_SAVE_INTERVAL))
    logger.info("DB auto-save loop started (every %ds).", DB_SAVE_INTERVAL)

    me = await app.bot.get_me()
    logger.info("Bot online: @%s (id=%s)", me.username, me.id)

    _bot_healthy["ok"] = True


async def post_shutdown(app: Application) -> None:
    """Async teardown: flush DB to disk, close HTTP session."""
    _bot_healthy["ok"] = False
    logger.info("Shutting down — flushing database…")
    await db.save_db(force=True)
    await tmdb_api.close_session()
    logger.info("Shutdown complete.")


# ════════════════════════════════════════════════════════════════
#  Handler registration
# ════════════════════════════════════════════════════════════════

def register_handlers(app: Application) -> None:
    # Commands
    app.add_handler(CommandHandler("start",      start_handler))
    app.add_handler(CommandHandler("adminpanel", adminpanel_handler))

    # Inline callbacks
    app.add_handler(CallbackQueryHandler(verify_callback,        pattern="^verify$"))
    app.add_handler(CallbackQueryHandler(select_callback,        pattern=r"^select_(movie|tv)_\d+$"))
    app.add_handler(CallbackQueryHandler(cancel_search_callback, pattern="^cancel_search$"))
    app.add_handler(CallbackQueryHandler(back_to_menu_callback,  pattern="^back_to_menu$"))
    app.add_handler(CallbackQueryHandler(copy_ref_callback,      pattern="^copy_ref$"))
    app.add_handler(CallbackQueryHandler(admin_callback,         pattern=r"^(adm_|confirm_)"))

    # Plain text in private chats (menu buttons + conversation states)
    private_text = filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE
    app.add_handler(MessageHandler(private_text, text_router))

    # Global error handler
    app.add_error_handler(error_handler)

    logger.info("All handlers registered.")


# ════════════════════════════════════════════════════════════════
#  Bot runner (extracted so the outer retry loop can call it again
#  if it crashes without needing to restart the whole process)
# ════════════════════════════════════════════════════════════════

def _run_bot() -> None:
    """
    Build a fresh Application and run it until it stops.

    PTB v21+ owns the event loop inside run_polling(). Each call to
    _run_bot() creates a new Application + event loop so that a crash
    in one run doesn't leave stale asyncio state for the next.

    PTB already retries internally on NetworkError / TimedOut with
    exponential backoff. This function raises only for errors PTB
    cannot handle itself (e.g. bad token, unrecoverable crash).
    """
    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        # Connection timeouts — generous values keep long polls stable
        # on flaky networks without killing legitimate slow responses.
        .read_timeout(30)
        .write_timeout(30)
        .connect_timeout(15)
        .pool_timeout(30)
        .build()
    )

    register_handlers(app)

    logger.info("Starting long-poll loop…")
    app.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True,
        # Long-poll window: Telegram holds the connection open for up to
        # 30 s waiting for updates before returning an empty response.
        # Higher values reduce request churn; 30 s is the practical max.
        timeout=30,
        # PTB catches SIGINT / SIGTERM / SIGABRT and shuts down cleanly.
        # post_shutdown() is called automatically before run_polling() returns.
    )


# ════════════════════════════════════════════════════════════════
#  Entry point — outer retry loop for 24/7 reliability
#
#  If _run_bot() crashes (token error, OS error, unhandled exception),
#  we wait and try again rather than letting the process die.
#  Replit's VM already restarts the process on exit, but having our
#  own retry loop avoids unnecessary cold-start latency.
# ════════════════════════════════════════════════════════════════

_MAX_BACKOFF = 60   # seconds — cap for exponential backoff


def main() -> None:
    # Start the health-check server once; it runs for the process lifetime.
    _start_health_server()

    attempt = 0
    while True:
        attempt += 1
        try:
            logger.info("Bot starting (attempt %d)…", attempt)
            _run_bot()
            # run_polling() returned without raising — this is a clean shutdown
            # (SIGTERM / SIGINT caught by PTB; post_shutdown already called).
            logger.info("Bot stopped cleanly.")
            break

        except (KeyboardInterrupt, SystemExit):
            # Ctrl-C in dev, or os._exit() — honour the signal.
            logger.info("Shutdown signal received — exiting.")
            break

        except Exception as exc:
            # Mark unhealthy immediately so the health endpoint reflects
            # the crash during the backoff window, not stale "OK" state.
            _bot_healthy["ok"] = False
            # Unexpected crash. Back off before retrying so we don't
            # hammer Telegram's API on a bad-token or network outage.
            # Schedule: 5 → 10 → 20 → 40 → 60 → 60 → … seconds.
            wait = min(_MAX_BACKOFF, 5 * 2 ** min(attempt - 1, 4))
            logger.error(
                "Bot crashed (attempt %d): %s — restarting in %ds…",
                attempt, exc, wait,
                exc_info=True,
            )
            time.sleep(wait)


if __name__ == "__main__":
    main()


# ======================================================================
# FILE: config.py
# ======================================================================

# ============================================================
#  CynemaBot — Configuration
#  All configurable values live here. Never hardcode elsewhere.
# ============================================================

# ── Bot credentials ──────────────────────────────────────────
BOT_TOKEN   = "8115145972:AAFN_N8Z-s3g0Z_PmZ0u6QihdgQl_GHDskk"
ADMIN_ID    = 6813806104          # <-- Set your Telegram user ID here

# ── TMDB ─────────────────────────────────────────────────────
TMDB_API_KEY    = "ce822ebcfcfc1f92264713bb4306fdbd"
TMDB_BASE_URL   = "https://api.themoviedb.org/3"
TMDB_IMG_BASE   = "https://image.tmdb.org/t/p/w500"

# ── Media / Streaming ─────────────────────────────────────────
START_IMG    = "https://i.ibb.co/zHhfjxBf/file-103.jpg"
VIDLINK_BASE = "https://vidlink.pro/movie/"
TVLINK_BASE  = "https://vidlink.pro/tv/"

# ── Credits ───────────────────────────────────────────────────
START_SEARCH = 3      # Free searches for new users
REF_BONUS    = 3      # Bonus searches per successful referral
MAX_RESULTS  = 5      # Max TMDB results shown

# ── Channels (used in Force Join + UI) ───────────────────────
#    id  : channel ID for membership check (int for private, "@handle" for public)
#    link: invite / public link shown to users
#    name: button label

CHANNELS = [
    {
        "key":  "channel1",
        "name": "📢 Channel 1",
        "id":   "@CynemaOfficial",        # public — use @handle
        "link": "https://t.me/CynemaOfficial",
        "force_join": True,
    },
    {
        "key":  "channel2",
        "name": "🎬 Channel 2",
        "id":   -1002327987959,           # private — use int ID
        "link": "https://t.me/+aRo7US0G11piZDhl",
        "force_join": True,
    },
    {
        "key":  "earning",
        "name": "💰 Earning Channel",
        "id":   -1002183231133,           # private — use int ID
        "link": "https://t.me/+MPmO6Uvvy3NjYTdl",
        "force_join": True,
    },
]

# Instagram — NOT part of Force Join; shown only as a UI button
INSTAGRAM_LINK = "https://www.instagram.com/animeeditslolz"

# ── Database ───────────────────────────────────────────────────
DB_PATH         = "db.json"
DB_BACKUP_PATH  = "db_backup.json"
DB_SAVE_INTERVAL = 30   # seconds between auto-saves

# ── aiohttp session timeouts ──────────────────────────────────
HTTP_TIMEOUT    = 10    # seconds
HTTP_RETRIES    = 2

# ── Misc ───────────────────────────────────────────────────────
BROADCAST_DELAY = 0.05  # seconds between broadcast messages
LOG_LEVEL       = "INFO"


# ======================================================================
# FILE: database.py
# ======================================================================

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


# ======================================================================
# FILE: tmdb.py
# ======================================================================

# ============================================================
#  CynemaBot — TMDB API Client
#  Reuses a single aiohttp session for performance.
# ============================================================

import asyncio
import json
import logging
from typing import Optional

import aiohttp

from config import TMDB_API_KEY, TMDB_BASE_URL, TMDB_IMG_BASE, HTTP_TIMEOUT, HTTP_RETRIES, MAX_RESULTS

logger = logging.getLogger(__name__)

# Shared session (initialised once in bot startup)
_session: Optional[aiohttp.ClientSession] = None


async def init_session() -> None:
    global _session
    # Close any existing session before creating a new one
    if _session and not _session.closed:
        await _session.close()
    timeout = aiohttp.ClientTimeout(total=HTTP_TIMEOUT)
    _session = aiohttp.ClientSession(
        timeout=timeout,
        connector=aiohttp.TCPConnector(limit=20, ttl_dns_cache=300),
    )
    logger.info("aiohttp session initialised.")


async def close_session() -> None:
    global _session
    if _session and not _session.closed:
        await _session.close()
    _session = None


def _session_guard() -> aiohttp.ClientSession:
    if _session is None or _session.closed:
        raise RuntimeError("aiohttp session not initialised.")
    return _session


async def _get(url: str, params: dict) -> Optional[dict]:
    """GET with retry logic. Returns parsed JSON or None."""
    params["api_key"] = TMDB_API_KEY

    # yarl (aiohttp's URL builder) rejects Python bools — convert them to strings.
    params = {k: (str(v).lower() if isinstance(v, bool) else v) for k, v in params.items()}

    # Ensure session is alive; attempt recovery if not.
    try:
        _session_guard()
    except RuntimeError:
        logger.warning("TMDB session not ready — attempting recovery.")
        try:
            await init_session()
        except Exception as exc:
            logger.error("TMDB session re-init failed: %s", exc)
            return None

    for attempt in range(HTTP_RETRIES + 1):
        try:
            async with _session_guard().get(url, params=params) as resp:
                if resp.status == 200:
                    # content_type=None bypasses strict content-type validation;
                    # JSONDecodeError is caught below so bad payloads don't crash.
                    return await resp.json(content_type=None)
                logger.warning("TMDB HTTP %s for %s", resp.status, url)
        except asyncio.TimeoutError:
            logger.warning("TMDB timeout (attempt %d): %s", attempt + 1, url)
        except (aiohttp.ClientError, json.JSONDecodeError, ValueError) as e:
            logger.warning("TMDB request error (attempt %d): %s — %s", attempt + 1, type(e).__name__, e)
        except RuntimeError as e:
            logger.error("TMDB session error: %s", e)
            return None
        if attempt < HTTP_RETRIES:
            await asyncio.sleep(0.5 * (attempt + 1))
    return None


# ════════════════════════════════════════════════════════════════
#  Search
# ════════════════════════════════════════════════════════════════

async def multi_search(query: str) -> list[dict]:
    """
    Search movies, TV shows, and anime by title.
    Returns up to MAX_RESULTS cleaned result dicts.
    """
    data = await _get(
        f"{TMDB_BASE_URL}/search/multi",
        {"query": query, "include_adult": False, "language": "en-US", "page": 1},
    )
    if not data or "results" not in data:
        return []

    results = []
    seen: set[str] = set()

    for item in data["results"]:
        media_type = item.get("media_type")
        if media_type not in ("movie", "tv"):
            continue

        title = item.get("title") or item.get("name") or "Unknown"
        tmdb_id = item.get("id")
        if not tmdb_id:
            continue
        year_raw = (item.get("release_date") or item.get("first_air_date") or "")[:4]
        year = year_raw if year_raw.isdigit() else "N/A"
        rating = round(item.get("vote_average") or 0, 1)
        overview = (item.get("overview") or "No description available.")[:300]
        poster = (
            f"{TMDB_IMG_BASE}{item['poster_path']}" if item.get("poster_path") else None
        )

        # Deduplicate by "title|year"
        dedup_key = f"{title.lower()}|{year}"
        if dedup_key in seen:
            continue
        seen.add(dedup_key)

        results.append(
            {
                "id": tmdb_id,
                "title": title,
                "year": year,
                "rating": rating,
                "overview": overview,
                "poster": poster,
                "media_type": media_type,
            }
        )
        if len(results) >= MAX_RESULTS:
            break

    return results


# ════════════════════════════════════════════════════════════════
#  Detail fetchers
# ════════════════════════════════════════════════════════════════

async def get_movie_details(tmdb_id: int) -> Optional[dict]:
    data = await _get(
        f"{TMDB_BASE_URL}/movie/{tmdb_id}",
        {"language": "en-US", "append_to_response": "credits,keywords"},
    )
    if not data:
        return None
    try:
        return _clean_movie(data)
    except (KeyError, TypeError, ValueError) as e:
        logger.warning("Failed to parse movie details for id=%s: %s", tmdb_id, e)
        return None


async def get_tv_details(tmdb_id: int) -> Optional[dict]:
    data = await _get(
        f"{TMDB_BASE_URL}/tv/{tmdb_id}",
        {"language": "en-US", "append_to_response": "credits,keywords"},
    )
    if not data:
        return None
    try:
        return _clean_tv(data)
    except (KeyError, TypeError, ValueError) as e:
        logger.warning("Failed to parse TV details for id=%s: %s", tmdb_id, e)
        return None


def _clean_movie(data: dict) -> dict:
    genres = ", ".join(g["name"] for g in data.get("genres", [])[:3]) or "N/A"
    runtime = data.get("runtime") or 0
    runtime_str = f"{runtime // 60}h {runtime % 60}m" if runtime else "N/A"
    return {
        "id": data.get("id"),
        "title": data.get("title", "Unknown"),
        "year": (data.get("release_date") or "")[:4] or "N/A",
        "rating": round(data.get("vote_average") or 0, 1),
        "overview": (data.get("overview") or "No description available.")[:500],
        "poster": f"{TMDB_IMG_BASE}{data['poster_path']}" if data.get("poster_path") else None,
        "genres": genres,
        "runtime": runtime_str,
        "media_type": "movie",
    }


def _clean_tv(data: dict) -> dict:
    genres = ", ".join(g["name"] for g in data.get("genres", [])[:3]) or "N/A"
    seasons = data.get("number_of_seasons", "N/A")
    episodes = data.get("number_of_episodes", "N/A")
    return {
        "id": data.get("id"),
        "title": data.get("name", "Unknown"),
        "year": (data.get("first_air_date") or "")[:4] or "N/A",
        "rating": round(data.get("vote_average") or 0, 1),
        "overview": (data.get("overview") or "No description available.")[:500],
        "poster": f"{TMDB_IMG_BASE}{data['poster_path']}" if data.get("poster_path") else None,
        "genres": genres,
        "seasons": seasons,
        "episodes": episodes,
        "media_type": "tv",
    }


# ======================================================================
# FILE: keyboards.py
# ======================================================================

# ============================================================
#  CynemaBot — Inline & Reply Keyboard Builders
# ============================================================

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup

from config import CHANNELS, INSTAGRAM_LINK


# ════════════════════════════════════════════════════════════════
#  Start / Verification
# ════════════════════════════════════════════════════════════════

def start_keyboard() -> InlineKeyboardMarkup:
    """Buttons shown with the welcome message."""
    rows = []
    for ch in CHANNELS:
        rows.append([InlineKeyboardButton(ch["name"], url=ch["link"])])
    rows.append([InlineKeyboardButton("📸 Instagram", url=INSTAGRAM_LINK)])
    rows.append([InlineKeyboardButton("✅ Verify", callback_data="verify")])
    return InlineKeyboardMarkup(rows)


def verify_keyboard() -> InlineKeyboardMarkup:
    """Force-join prompt keyboard."""
    rows = []
    for ch in CHANNELS:
        if ch.get("force_join"):
            rows.append([InlineKeyboardButton(ch["name"], url=ch["link"])])
    rows.append([InlineKeyboardButton("📸 Instagram", url=INSTAGRAM_LINK)])
    rows.append([InlineKeyboardButton("✅ I've Joined — Verify", callback_data="verify")])
    return InlineKeyboardMarkup(rows)


# ════════════════════════════════════════════════════════════════
#  Main Menu (Reply Keyboard)
# ════════════════════════════════════════════════════════════════

def main_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            ["🎬 Movies", "🌸 Anime"],
            ["📺 Web Series", "👥 Invite"],
            ["📊 My Stats", "📩 Movie Request"],
        ],
        resize_keyboard=True,
    )


# ════════════════════════════════════════════════════════════════
#  Search results
# ════════════════════════════════════════════════════════════════

def search_results_keyboard(results: list[dict]) -> InlineKeyboardMarkup:
    rows = []
    for r in results:
        label = f"{r['title']} ({r['year']}) — {'⭐' + str(r['rating']) if r['rating'] else ''}"
        cb = f"select_{r['media_type']}_{r['id']}"
        rows.append([InlineKeyboardButton(label, callback_data=cb)])
    rows.append([InlineKeyboardButton("❌ Cancel", callback_data="cancel_search")])
    return InlineKeyboardMarkup(rows)


def watch_keyboard(media_type: str, tmdb_id: int, vidlink_base: str, tvlink_base: str) -> InlineKeyboardMarkup:
    if media_type == "movie":
        watch_url = f"{vidlink_base}{tmdb_id}"
    else:
        watch_url = f"{tvlink_base}{tmdb_id}"
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("▶️ Watch Now", url=watch_url)],
         [InlineKeyboardButton("🔙 Back", callback_data="back_to_menu")]]
    )


# ════════════════════════════════════════════════════════════════
#  No credits left
# ════════════════════════════════════════════════════════════════

def no_credits_keyboard(ref_link: str) -> InlineKeyboardMarkup:
    share_text = f"🎬 Join CynemaBot and get free searches!\n{ref_link}"
    import urllib.parse
    encoded = urllib.parse.quote(share_text)
    share_url = f"https://t.me/share/url?url={encoded}"
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📋 Copy Link", callback_data=f"copy_ref")],
            [InlineKeyboardButton("📤 Share Link", url=share_url)],
        ]
    )


# ════════════════════════════════════════════════════════════════
#  Admin Panel
# ════════════════════════════════════════════════════════════════

def admin_main_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📊 Dashboard", callback_data="adm_dashboard")],
            [
                InlineKeyboardButton("👤 User Manager", callback_data="adm_users"),
                InlineKeyboardButton("📣 Broadcast", callback_data="adm_broadcast"),
            ],
            [
                InlineKeyboardButton("📺 Channels", callback_data="adm_channels"),
                InlineKeyboardButton("⚙️ Settings", callback_data="adm_settings"),
            ],
            [
                InlineKeyboardButton("🔗 Referrals", callback_data="adm_referrals"),
                InlineKeyboardButton("📩 Requests", callback_data="adm_requests"),
            ],
            [
                InlineKeyboardButton("📈 Statistics", callback_data="adm_stats"),
                InlineKeyboardButton("💾 Backup", callback_data="adm_backup"),
            ],
            [
                InlineKeyboardButton("📋 Logs", callback_data="adm_logs"),
                InlineKeyboardButton("🔒 Security", callback_data="adm_security"),
            ],
        ]
    )


def admin_back_keyboard(to: str = "adm_main") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data=to)]])


def admin_user_actions_keyboard(uid: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🚫 Ban", callback_data=f"adm_ban_{uid}"),
                InlineKeyboardButton("✅ Unban", callback_data=f"adm_unban_{uid}"),
            ],
            [
                InlineKeyboardButton("➕ Add Searches", callback_data=f"adm_add_s_{uid}"),
                InlineKeyboardButton("➖ Remove Searches", callback_data=f"adm_rem_s_{uid}"),
            ],
            [
                InlineKeyboardButton("🎁 Add Bonus", callback_data=f"adm_add_b_{uid}"),
                InlineKeyboardButton("🗑 Remove Bonus", callback_data=f"adm_rem_b_{uid}"),
            ],
            [
                InlineKeyboardButton("♻️ Reset Searches", callback_data=f"adm_rst_s_{uid}"),
                InlineKeyboardButton("♻️ Reset Referrals", callback_data=f"adm_rst_r_{uid}"),
            ],
            [InlineKeyboardButton("💬 Send Message", callback_data=f"adm_msg_{uid}")],
            [InlineKeyboardButton("🗑 Delete User", callback_data=f"adm_del_{uid}")],
            [InlineKeyboardButton("🔙 Back", callback_data="adm_users")],
        ]
    )


def admin_broadcast_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("📝 Text", callback_data="adm_bc_text"),
                InlineKeyboardButton("🖼 Photo", callback_data="adm_bc_photo"),
            ],
            [
                InlineKeyboardButton("🎥 Video", callback_data="adm_bc_video"),
                InlineKeyboardButton("📄 Document", callback_data="adm_bc_doc"),
            ],
            [
                InlineKeyboardButton("🎞 Animation", callback_data="adm_bc_anim"),
                InlineKeyboardButton("↪️ Forward", callback_data="adm_bc_fwd"),
            ],
            [InlineKeyboardButton("🔙 Back", callback_data="adm_main")],
        ]
    )


def admin_settings_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🖼 Start Image", callback_data="adm_set_img")],
            [InlineKeyboardButton("📝 Welcome Message", callback_data="adm_set_welcome")],
            [InlineKeyboardButton("✅ Verify Caption", callback_data="adm_set_verify")],
            [InlineKeyboardButton("📋 Menu Caption", callback_data="adm_set_menu_cap")],
            [InlineKeyboardButton("📸 Instagram Link", callback_data="adm_set_insta")],
            [InlineKeyboardButton("🔗 VidLink Base URL", callback_data="adm_set_vidlink")],
            [InlineKeyboardButton("🎁 Referral Bonus", callback_data="adm_set_refbonus")],
            [InlineKeyboardButton("🔢 Starting Searches", callback_data="adm_set_startsearch")],
            [
                InlineKeyboardButton("🔧 Maintenance ON", callback_data="adm_maint_on"),
                InlineKeyboardButton("✅ Maintenance OFF", callback_data="adm_maint_off"),
            ],
            [InlineKeyboardButton("🔙 Back", callback_data="adm_main")],
        ]
    )


def admin_stats_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("📅 Daily", callback_data="adm_stat_daily"),
                InlineKeyboardButton("📆 Weekly", callback_data="adm_stat_weekly"),
            ],
            [
                InlineKeyboardButton("🗓 Monthly", callback_data="adm_stat_monthly"),
                InlineKeyboardButton("🔍 Search Stats", callback_data="adm_stat_search"),
            ],
            [InlineKeyboardButton("📤 Export Users", callback_data="adm_export_users")],
            [InlineKeyboardButton("🔙 Back", callback_data="adm_main")],
        ]
    )


def admin_requests_keyboard(req_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("↩️ Reply", callback_data=f"adm_req_reply_{req_id}"),
                InlineKeyboardButton("✅ Done", callback_data=f"adm_req_done_{req_id}"),
            ],
            [InlineKeyboardButton("🗑 Delete", callback_data=f"adm_req_del_{req_id}")],
            [InlineKeyboardButton("🔙 Back", callback_data="adm_requests")],
        ]
    )


def confirm_keyboard(action: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ Confirm", callback_data=f"confirm_{action}"),
                InlineKeyboardButton("❌ Cancel", callback_data="adm_main"),
            ]
        ]
    )


# ======================================================================
# FILE: messages.py
# ======================================================================

# ============================================================
#  CynemaBot — Message Templates
# ============================================================

from datetime import datetime


def welcome_caption(name: str, searches: int) -> str:
    return (
        f"╔══════════════════════╗\n"
        f"   🎬 <b>Welcome to CynemaBot</b>\n"
        f"╚══════════════════════╝\n\n"
        f"Hey <b>{name}</b>! 👋\n\n"
        f"🎥 Your ultimate <b>Movie & Anime</b> streaming companion.\n"
        f"Search, discover, and watch instantly!\n\n"
        f"🎁 <b>Free Searches:</b> <code>{searches}</code>\n\n"
        f"📌 <i>Join all channels below to get started.</i>"
    )


def verify_caption() -> str:
    return (
        "╔══════════════════════╗\n"
        "   🔒 <b>Channel Verification</b>\n"
        "╚══════════════════════╝\n\n"
        "⚠️ <b>You must join all channels to use the bot.</b>\n\n"
        "👇 Click each button to join, then press <b>Verify</b>."
    )


def menu_caption() -> str:
    return (
        "╔══════════════════════╗\n"
        "   🎬 <b>CynemaBot</b> — Main Menu\n"
        "╚══════════════════════╝\n\n"
        "Choose a category below 👇"
    )


def search_prompt(category: str) -> str:
    icons = {"movies": "🎬", "anime": "🌸", "webseries": "📺"}
    icon = icons.get(category, "🔍")
    return (
        f"{icon} <b>Search {category.replace('webseries','Web Series').title()}</b>\n\n"
        "📝 Send me the <b>title</b> you're looking for:"
    )


def results_caption(results: list[dict]) -> str:
    return (
        f"╔══════════════════════╗\n"
        f"   🔍 <b>Search Results</b>\n"
        f"╚══════════════════════╝\n\n"
        f"Found <b>{len(results)}</b> result(s). Tap to select:"
    )


def movie_detail_caption(data: dict) -> str:
    media = "🎬 Movie" if data["media_type"] == "movie" else "📺 TV Show"
    extras = ""
    if data["media_type"] == "movie":
        extras = f"⏱ <b>Runtime:</b> {data.get('runtime', 'N/A')}\n"
    else:
        extras = (
            f"📦 <b>Seasons:</b> {data.get('seasons', 'N/A')} | "
            f"📺 <b>Episodes:</b> {data.get('episodes', 'N/A')}\n"
        )

    return (
        f"╔══════════════════════╗\n"
        f"   {media}\n"
        f"╚══════════════════════╝\n\n"
        f"🎬 <b>{data['title']}</b> ({data['year']})\n\n"
        f"⭐ <b>Rating:</b> {data['rating']} / 10\n"
        f"🎭 <b>Genre:</b> {data.get('genres', 'N/A')}\n"
        f"{extras}\n"
        f"📖 <b>Overview:</b>\n<i>{data['overview']}</i>"
    )


def no_credits_caption(ref_link: str) -> str:
    return (
        "╔══════════════════════╗\n"
        "   ❌ <b>Out of Searches</b>\n"
        "╚══════════════════════╝\n\n"
        "😔 You've used all your search credits.\n\n"
        "💡 <b>Get more for free by inviting friends!</b>\n"
        "Each referral earns you bonus searches.\n\n"
        f"🔗 <b>Your Referral Link:</b>\n<code>{ref_link}</code>\n\n"
        "👆 Share the link above to earn!"
    )


def stats_caption(user: dict) -> str:
    total = user.get("free_searches", 0) + user.get("bonus_searches", 0)
    return (
        "╔══════════════════════╗\n"
        "   📊 <b>My Statistics</b>\n"
        "╚══════════════════════╝\n\n"
        f"👤 <b>Name:</b> {user['name']}\n"
        f"🆔 <b>User ID:</b> <code>{user['id']}</code>\n"
        f"📅 <b>Joined:</b> {user.get('join_date', 'N/A')}\n\n"
        f"🔍 <b>Free Searches:</b> {user.get('free_searches', 0)}\n"
        f"🎁 <b>Bonus Searches:</b> {user.get('bonus_searches', 0)}\n"
        f"✅ <b>Total Remaining:</b> {total}\n\n"
        f"👥 <b>Referrals:</b> {user.get('referrals', 0)}\n"
        f"📊 <b>Total Searches Done:</b> {user.get('total_searches', 0)}"
    )


def referral_caption(ref_link: str, user: dict) -> str:
    return (
        "╔══════════════════════╗\n"
        "   👥 <b>Referral Program</b>\n"
        "╚══════════════════════╝\n\n"
        "🎁 Invite friends and earn <b>bonus searches</b>!\n"
        "Each successful referral = <b>3 bonus searches</b>.\n\n"
        f"🔗 <b>Your Link:</b>\n<code>{ref_link}</code>\n\n"
        f"👥 <b>Total Referrals:</b> {user.get('referrals', 0)}\n"
        f"🎁 <b>Bonus Searches:</b> {user.get('bonus_searches', 0)}\n\n"
        "📌 Share your link to start earning!"
    )


def request_prompt() -> str:
    return (
        "╔══════════════════════╗\n"
        "   📩 <b>Movie Request</b>\n"
        "╚══════════════════════╝\n\n"
        "🎬 Send the name of the movie or show you want:\n\n"
        "<i>We'll try to add it as soon as possible!</i>"
    )


def request_sent_caption() -> str:
    return (
        "✅ <b>Request Sent!</b>\n\n"
        "Your request has been forwarded to the admin.\n"
        "Thank you for your suggestion! 🙏"
    )


def maintenance_caption() -> str:
    return (
        "🔧 <b>Maintenance Mode</b>\n\n"
        "CynemaBot is currently undergoing maintenance.\n"
        "Please check back soon! 🙏"
    )


def banned_caption() -> str:
    return (
        "🚫 <b>Access Denied</b>\n\n"
        "Your account has been banned.\n"
        "Contact support if you believe this is a mistake."
    )


def admin_dashboard_caption(stats: dict) -> str:
    return (
        "╔══════════════════════╗\n"
        "   📊 <b>Admin Dashboard</b>\n"
        "╚══════════════════════╝\n\n"
        f"👥 <b>Total Users:</b> {stats['total_users']}\n"
        f"🟢 <b>Active Today:</b> {stats['active_today']}\n"
        f"🆕 <b>Joined Today:</b> {stats['today_new']}\n\n"
        f"🔍 <b>Total Searches:</b> {stats['total_searches']}\n"
        f"👥 <b>Total Referrals:</b> {stats['total_referrals']}\n"
        f"📩 <b>Total Requests:</b> {stats['total_requests']}\n\n"
        f"🤖 <b>Bot Status:</b> ✅ Online\n"
        f"💾 <b>Database:</b> ✅ Healthy"
    )


# ======================================================================
# FILE: force_join.py
# ======================================================================

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


# ======================================================================
# FILE: handlers/__init__.py
# ======================================================================

# handlers package


# ======================================================================
# FILE: handlers/start.py
# ======================================================================

# ============================================================
#  Handler — /start & Force Join Verify
# ============================================================

import logging
from telegram import Update
from telegram.ext import ContextTypes
from telegram.error import TelegramError

import database as db
from force_join import check_force_join
from keyboards import start_keyboard, verify_keyboard, main_menu
from messages import welcome_caption, verify_caption, menu_caption, maintenance_caption, banned_caption
from config import ADMIN_ID

logger = logging.getLogger(__name__)


async def start_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not user:
        return

    # ── Parse referral ────────────────────────────────────────
    ref_id: int | None = None
    if ctx.args:
        arg = ctx.args[0]
        if arg.startswith("ref_"):
            try:
                ref_id = int(arg[4:])
                if ref_id == user.id:
                    ref_id = None
            except ValueError:
                pass

    # ── Register / load user ──────────────────────────────────
    usr = await db.get_user(user.id)
    if not usr:
        usr = await db.register_user(
            user_id=user.id,
            name=user.full_name,
            username=user.username,
            referred_by=ref_id,
        )

    # ── Update name/username ──────────────────────────────────
    await db.update_user(user.id, name=user.full_name, username=user.username or "")

    # ── Reload after potential registration ───────────────────
    usr = await db.get_user(user.id)

    # ── Banned? ───────────────────────────────────────────────
    if usr.get("is_banned"):
        await update.message.reply_text(banned_caption(), parse_mode="HTML")
        return

    # ── Maintenance (bypass for admin) ────────────────────────
    if db.get_setting("maintenance_mode") and user.id != ADMIN_ID:
        await update.message.reply_text(maintenance_caption(), parse_mode="HTML")
        return

    # ── Force Join check ──────────────────────────────────────
    all_joined, missing = await check_force_join(ctx.bot, user.id)
    if not all_joined:
        await _send_verify_prompt(update)
        return

    # ── Welcome ───────────────────────────────────────────────
    await _send_welcome(update, usr)


async def verify_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handles the Verify button press.

    A Telegram callback query can only be answered ONCE.
    We defer query.answer() until we know whether to show an alert or not,
    so the user always sees the right feedback.
    """
    query = update.callback_query
    user = update.effective_user

    usr = await db.get_user(user.id)
    if not usr:
        usr = await db.register_user(user.id, user.full_name, user.username)

    # ── Banned ────────────────────────────────────────────────
    if usr.get("is_banned"):
        await query.answer("🚫 Your account has been banned.", show_alert=True)
        return

    # ── Force join check ──────────────────────────────────────
    all_joined, missing = await check_force_join(ctx.bot, user.id)
    if not all_joined:
        names = ", ".join(ch["name"] for ch in missing)
        await query.answer(
            f"❌ Please join all required channels first.\nMissing: {names}",
            show_alert=True,
        )
        return

    # ── Success: acknowledge, clean up the verify message, show menu ──
    await query.answer("✅ Verified! Welcome!")

    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except TelegramError:
        pass

    await ctx.bot.send_message(
        chat_id=user.id,
        text=menu_caption(),
        parse_mode="HTML",
        reply_markup=main_menu(),
    )


# ── Private helpers ───────────────────────────────────────────

async def _send_verify_prompt(update: Update) -> None:
    start_img = db.get_setting("start_img")
    caption = verify_caption()
    kb = verify_keyboard()
    try:
        await update.message.reply_photo(
            photo=start_img,
            caption=caption,
            parse_mode="HTML",
            reply_markup=kb,
        )
    except TelegramError:
        await update.message.reply_text(caption, parse_mode="HTML", reply_markup=kb)


async def _send_welcome(update: Update, usr: dict) -> None:
    start_img = db.get_setting("start_img")
    caption = welcome_caption(usr["name"], usr.get("free_searches", 0))
    kb = start_keyboard()
    try:
        await update.message.reply_photo(
            photo=start_img,
            caption=caption,
            parse_mode="HTML",
            reply_markup=kb,
        )
    except TelegramError:
        await update.message.reply_text(caption, parse_mode="HTML", reply_markup=kb)

    from messages import menu_caption as mc
    await update.message.reply_text(mc(), parse_mode="HTML", reply_markup=main_menu())


# ======================================================================
# FILE: handlers/search.py
# ======================================================================

# ============================================================
#  Handler — Movie / Anime / Web Series Search
# ============================================================

import logging
from telegram import Update
from telegram.ext import ContextTypes
from telegram.error import TelegramError

import database as db
import tmdb as tmdb_api
from force_join import check_force_join
from keyboards import search_results_keyboard, watch_keyboard, no_credits_keyboard, main_menu
from messages import (
    search_prompt, results_caption, movie_detail_caption,
    no_credits_caption, menu_caption,
)
from config import ADMIN_ID

logger = logging.getLogger(__name__)

# Conversation states (stored in user_data)
STATE_WAITING_QUERY = "waiting_search_query"
STATE_CATEGORY = "search_category"


async def _guard(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> bool:
    """Check ban, maintenance, force-join. Returns True if OK to proceed."""
    user = update.effective_user
    usr = await db.get_user(user.id)
    if not usr:
        await update.message.reply_text("Please send /start first.")
        return False
    if usr.get("is_banned"):
        from messages import banned_caption
        await update.message.reply_text(banned_caption(), parse_mode="HTML")
        return False
    if db.get_setting("maintenance_mode") and user.id != ADMIN_ID:
        from messages import maintenance_caption
        await update.message.reply_text(maintenance_caption(), parse_mode="HTML")
        return False
    all_joined, missing = await check_force_join(ctx.bot, user.id)
    if not all_joined:
        from keyboards import verify_keyboard
        from messages import verify_caption
        await update.message.reply_text(
            verify_caption(), parse_mode="HTML", reply_markup=verify_keyboard()
        )
        return False
    return True


async def movies_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard(update, ctx):
        return
    ctx.user_data[STATE_CATEGORY] = "movies"
    ctx.user_data[STATE_WAITING_QUERY] = True
    await update.message.reply_text(search_prompt("movies"), parse_mode="HTML")


async def anime_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard(update, ctx):
        return
    ctx.user_data[STATE_CATEGORY] = "anime"
    ctx.user_data[STATE_WAITING_QUERY] = True
    await update.message.reply_text(search_prompt("anime"), parse_mode="HTML")


async def webseries_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard(update, ctx):
        return
    ctx.user_data[STATE_CATEGORY] = "webseries"
    ctx.user_data[STATE_WAITING_QUERY] = True
    await update.message.reply_text(search_prompt("webseries"), parse_mode="HTML")


async def search_text_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Receives the search query from the user."""
    if not ctx.user_data.get(STATE_WAITING_QUERY):
        return  # Not in search mode; ignore

    user = update.effective_user
    query = (update.message.text or "").strip()
    if not query:
        return

    # Clear state
    ctx.user_data.pop(STATE_WAITING_QUERY, None)

    # Show "searching..." indicator first so the UX feels snappy
    thinking = await update.message.reply_text("🔍 <b>Searching...</b>", parse_mode="HTML")

    # TMDB search — errors are handled inside multi_search; returns [] on failure
    results = await tmdb_api.multi_search(query)

    # Filter by category
    category = ctx.user_data.get(STATE_CATEGORY, "movies")
    if category == "movies":
        results = [r for r in results if r["media_type"] == "movie"]
    elif category == "anime":
        pass  # keep all — anime spans TV + movies
    elif category == "webseries":
        results = [r for r in results if r["media_type"] == "tv"]

    # Delete thinking message
    try:
        await thinking.delete()
    except TelegramError:
        pass

    if not results:
        await update.message.reply_text(
            "😔 <b>No results found.</b>\n\nTry a different title.",
            parse_mode="HTML",
            reply_markup=main_menu(),
        )
        return

    # Atomically deduct one credit — consume_search() does the check+deduct
    # under a single lock so concurrent requests can't race past the balance check.
    credited = await db.consume_search(user.id)
    if not credited:
        # Credits ran out between when the user sent the query and now.
        # Use ctx.bot.username (sync property, no network call) to build ref link.
        bot_username = ctx.bot.username or "bot"
        ref_link = f"https://t.me/{bot_username}?start=ref_{user.id}"
        await update.message.reply_text(
            no_credits_caption(ref_link),
            parse_mode="HTML",
            reply_markup=no_credits_keyboard(ref_link),
        )
        return

    # Store results for callback lookup
    ctx.user_data["search_results"] = {str(r["id"]): r for r in results}

    await update.message.reply_text(
        results_caption(results),
        parse_mode="HTML",
        reply_markup=search_results_keyboard(results),
    )


async def select_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Called when user taps a search result button.

    A callback query can only be answered once. We defer query.answer() until
    we know the outcome so we can show an alert on failure.
    """
    query = update.callback_query

    data = query.data  # format: select_<media_type>_<tmdb_id>
    parts = data.split("_", 2)
    if len(parts) != 3:
        await query.answer()
        return

    _, media_type, tmdb_id_str = parts
    try:
        tmdb_id = int(tmdb_id_str)
    except ValueError:
        await query.answer()
        return

    # Fetch full details — errors handled inside get_*_details; returns None on failure
    if media_type == "movie":
        details = await tmdb_api.get_movie_details(tmdb_id)
    else:
        details = await tmdb_api.get_tv_details(tmdb_id)

    if not details:
        await query.answer("❌ Could not fetch details. Try again.", show_alert=True)
        return

    # Acknowledge silently now that we have the data
    await query.answer()

    vidlink_base = db.get_setting("vidlink_base", "https://vidlink.pro/movie/")
    tvlink_base = db.get_setting("tvlink_base", "https://vidlink.pro/tv/")
    caption = movie_detail_caption(details)
    kb = watch_keyboard(media_type, tmdb_id, vidlink_base, tvlink_base)

    # Try to show poster alongside the detail text
    if details.get("poster"):
        try:
            await ctx.bot.send_photo(
                chat_id=update.effective_chat.id,
                photo=details["poster"],
                caption=caption,
                parse_mode="HTML",
                reply_markup=kb,
            )
            # Edit the original results message to remove its keyboard
            try:
                await query.edit_message_reply_markup(reply_markup=None)
            except TelegramError:
                pass
            return
        except TelegramError:
            pass

    # Fallback: edit the existing message in place
    try:
        await query.edit_message_text(caption, parse_mode="HTML", reply_markup=kb)
    except TelegramError:
        await ctx.bot.send_message(
            chat_id=update.effective_chat.id,
            text=caption,
            parse_mode="HTML",
            reply_markup=kb,
        )


async def cancel_search_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer("Search cancelled.")
    ctx.user_data.pop(STATE_WAITING_QUERY, None)
    ctx.user_data.pop(STATE_CATEGORY, None)
    ctx.user_data.pop("search_results", None)
    try:
        await query.edit_message_text(menu_caption(), parse_mode="HTML")
    except TelegramError:
        pass


async def back_to_menu_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    try:
        await query.edit_message_text(menu_caption(), parse_mode="HTML")
    except TelegramError:
        pass


async def copy_ref_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user = update.effective_user
    bot_username = ctx.bot.username or "bot"
    ref_link = f"https://t.me/{bot_username}?start=ref_{user.id}"
    await query.answer(f"🔗 {ref_link}", show_alert=True)


# ======================================================================
# FILE: handlers/request.py
# ======================================================================

# ============================================================
#  Handler — Movie Request
# ============================================================

import logging
from telegram import Update
from telegram.ext import ContextTypes
from telegram.error import TelegramError

import database as db
from messages import request_prompt, request_sent_caption
from config import ADMIN_ID

logger = logging.getLogger(__name__)

STATE_WAITING_REQUEST = "waiting_request_text"


async def request_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    usr = await db.get_user(user.id)
    if not usr:
        await update.message.reply_text("Please send /start first.")
        return

    ctx.user_data[STATE_WAITING_REQUEST] = True
    await update.message.reply_text(request_prompt(), parse_mode="HTML")


async def request_text_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not ctx.user_data.get(STATE_WAITING_REQUEST):
        return

    user = update.effective_user
    text = (update.message.text or "").strip()
    if not text:
        return

    ctx.user_data.pop(STATE_WAITING_REQUEST, None)

    # Save to DB
    req_id = await db.add_request(user.id, user.full_name, text)

    # Forward to admin
    admin_text = (
        f"📩 <b>New Movie Request</b>\n\n"
        f"👤 <b>User:</b> {user.full_name} (<code>{user.id}</code>)\n"
        f"🆔 <b>Request ID:</b> #{req_id}\n\n"
        f"🎬 <b>Request:</b>\n{text}"
    )
    try:
        await ctx.bot.send_message(
            chat_id=ADMIN_ID,
            text=admin_text,
            parse_mode="HTML",
        )
    except TelegramError as e:
        logger.warning("Could not forward request to admin: %s", e)

    await update.message.reply_text(request_sent_caption(), parse_mode="HTML")


# ======================================================================
# FILE: handlers/admin.py
# ======================================================================

# ============================================================
#  Handler — Admin Panel (full-featured)
# ============================================================

import asyncio
import io
import json
import logging
from datetime import date, timedelta
from typing import Optional

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputFile
from telegram.ext import ContextTypes
from telegram.error import TelegramError, RetryAfter

import database as db
from keyboards import (
    admin_main_keyboard, admin_back_keyboard, admin_user_actions_keyboard,
    admin_broadcast_keyboard, admin_settings_keyboard, admin_stats_keyboard,
    admin_requests_keyboard, confirm_keyboard,
)
from messages import admin_dashboard_caption
from config import ADMIN_ID, BROADCAST_DELAY

logger = logging.getLogger(__name__)

# Admin conversation state keys
ADM_STATE = "adm_state"
ADM_TARGET_USER = "adm_target_user"
ADM_BC_TYPE = "adm_bc_type"
ADM_PENDING = "adm_pending"
ADM_CANCEL = "adm_cancel"


def _is_admin(user_id: int) -> bool:
    return user_id == ADMIN_ID


def _access_denied() -> str:
    return "🚫 <b>Access Denied.</b>\n\nThis command is for admins only."


# ════════════════════════════════════════════════════════════════
#  Entry
# ════════════════════════════════════════════════════════════════

async def adminpanel_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_admin(update.effective_user.id):
        await update.message.reply_text(_access_denied(), parse_mode="HTML")
        return
    await update.message.reply_text(
        "╔══════════════════════╗\n"
        "   🔐 <b>Admin Panel</b>\n"
        "╚══════════════════════╝\n\n"
        "Welcome back, Admin! Choose an option:",
        parse_mode="HTML",
        reply_markup=admin_main_keyboard(),
    )


# ════════════════════════════════════════════════════════════════
#  Callback router
# ════════════════════════════════════════════════════════════════

async def admin_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not _is_admin(update.effective_user.id):
        await query.answer("Access denied.", show_alert=True)
        return

    await query.answer()
    data = query.data

    # Main menu
    if data == "adm_main":
        await _edit(query, "🔐 <b>Admin Panel</b>\n\nChoose an option:", admin_main_keyboard())

    # Dashboard
    elif data == "adm_dashboard":
        stats = await db.get_stats()
        await _edit(query, admin_dashboard_caption(stats), admin_back_keyboard("adm_main"))

    # User manager
    elif data == "adm_users":
        await _edit(
            query,
            "👤 <b>User Manager</b>\n\nSend a user ID or @username to look up:",
            admin_back_keyboard("adm_main"),
        )
        ctx.user_data[ADM_STATE] = "search_user"

    # Broadcast menu
    elif data == "adm_broadcast":
        await _edit(query, "📣 <b>Broadcast</b>\n\nChoose media type:", admin_broadcast_keyboard())

    elif data.startswith("adm_bc_"):
        bc_type = data.replace("adm_bc_", "")
        ctx.user_data[ADM_BC_TYPE] = bc_type
        ctx.user_data[ADM_STATE] = "bc_waiting"
        prompts = {
            "text": "📝 Send the text message to broadcast:",
            "photo": "🖼 Send the photo to broadcast (with optional caption):",
            "video": "🎥 Send the video to broadcast (with optional caption):",
            "doc": "📄 Send the document to broadcast:",
            "anim": "🎞 Send the animation/GIF to broadcast:",
            "fwd": "↪️ Forward a message to broadcast it:",
        }
        await _edit(
            query,
            prompts.get(bc_type, "Send the content to broadcast:"),
            InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="adm_broadcast")]]),
        )

    # Channel manager
    elif data == "adm_channels":
        from config import CHANNELS
        lines = [f"• {ch['name']} — {'🟢' if ch.get('force_join') else '🔴'} Force Join" for ch in CHANNELS]
        text = "📺 <b>Channel Manager</b>\n\n" + "\n".join(lines) + "\n\n<i>To add/edit channels, modify config.py.</i>"
        await _edit(query, text, admin_back_keyboard("adm_main"))

    # Settings
    elif data == "adm_settings":
        await _edit(query, "⚙️ <b>Settings</b>\n\nChoose what to change:", admin_settings_keyboard())

    elif data.startswith("adm_set_"):
        key = data.replace("adm_set_", "")
        labels = {
            "img": ("start_img", "🖼 Send the new start image URL:"),
            "welcome": ("welcome_message", "📝 Send the new welcome message (HTML):"),
            "verify": ("verify_caption", "✅ Send the new verify caption (HTML):"),
            "menu_cap": ("menu_caption", "📋 Send the new menu caption (HTML):"),
            "insta": ("instagram_link", "📸 Send the new Instagram link:"),
            "vidlink": ("vidlink_base", "🔗 Send the new VidLink base URL:"),
            "refbonus": ("referral_bonus", "🎁 Send the new referral bonus (number):"),
            "startsearch": ("starting_searches", "🔢 Send the new starting searches (number):"),
        }
        if key in labels:
            db_key, prompt = labels[key]
            ctx.user_data[ADM_STATE] = f"set_{db_key}"
            await _edit(
                query,
                f"⚙️ <b>Setting: {db_key}</b>\n\n{prompt}",
                InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="adm_settings")]]),
            )

    elif data == "adm_maint_on":
        await db.set_setting("maintenance_mode", True)
        await _edit(query, "🔧 <b>Maintenance mode ENABLED.</b>", admin_back_keyboard("adm_settings"))

    elif data == "adm_maint_off":
        await db.set_setting("maintenance_mode", False)
        await _edit(query, "✅ <b>Maintenance mode DISABLED.</b>", admin_back_keyboard("adm_settings"))

    # User actions
    elif data.startswith("adm_ban_"):
        uid = int(data.split("_")[2])
        await db.ban_user(uid)
        await _edit(query, f"🚫 User <code>{uid}</code> banned.", admin_user_actions_keyboard(uid))

    elif data.startswith("adm_unban_"):
        uid = int(data.split("_")[2])
        await db.unban_user(uid)
        await _edit(query, f"✅ User <code>{uid}</code> unbanned.", admin_user_actions_keyboard(uid))

    elif data.startswith("adm_del_"):
        uid = int(data.split("_")[2])
        ctx.user_data[ADM_PENDING] = ("del_user", uid)
        await _edit(query, f"⚠️ Delete user <code>{uid}</code>? This cannot be undone.", confirm_keyboard(f"del_{uid}"))

    elif data.startswith("confirm_del_"):
        uid = int(data.replace("confirm_del_", ""))
        await db.delete_user(uid)
        await _edit(query, f"🗑 User <code>{uid}</code> deleted.", admin_back_keyboard("adm_users"))

    elif data.startswith("adm_add_s_"):
        uid = int(data.split("_")[3])
        ctx.user_data[ADM_STATE] = f"add_searches_{uid}"
        ctx.user_data[ADM_TARGET_USER] = uid
        await _edit(query, f"➕ Send the number of free searches to add to <code>{uid}</code>:",
                    InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data=f"adm_view_{uid}")]]))

    elif data.startswith("adm_rem_s_"):
        uid = int(data.split("_")[3])
        ctx.user_data[ADM_STATE] = f"rem_searches_{uid}"
        ctx.user_data[ADM_TARGET_USER] = uid
        await _edit(query, f"➖ Send the number of free searches to remove from <code>{uid}</code>:",
                    InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data=f"adm_view_{uid}")]]))

    elif data.startswith("adm_add_b_"):
        uid = int(data.split("_")[3])
        ctx.user_data[ADM_STATE] = f"add_bonus_{uid}"
        ctx.user_data[ADM_TARGET_USER] = uid
        await _edit(query, f"🎁 Send the number of bonus searches to add to <code>{uid}</code>:",
                    InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data=f"adm_view_{uid}")]]))

    elif data.startswith("adm_rem_b_"):
        uid = int(data.split("_")[3])
        ctx.user_data[ADM_STATE] = f"rem_bonus_{uid}"
        ctx.user_data[ADM_TARGET_USER] = uid
        await _edit(query, f"🗑 Send the number of bonus searches to remove from <code>{uid}</code>:",
                    InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data=f"adm_view_{uid}")]]))

    elif data.startswith("adm_rst_s_"):
        uid = int(data.split("_")[3])
        await db.reset_searches(uid)
        await _edit(query, f"♻️ Searches reset for <code>{uid}</code>.", admin_user_actions_keyboard(uid))

    elif data.startswith("adm_rst_r_"):
        uid = int(data.split("_")[3])
        await db.reset_referrals(uid)
        await _edit(query, f"♻️ Referrals reset for <code>{uid}</code>.", admin_user_actions_keyboard(uid))

    elif data.startswith("adm_msg_"):
        uid = int(data.split("_")[2])
        ctx.user_data[ADM_STATE] = f"send_msg_{uid}"
        ctx.user_data[ADM_TARGET_USER] = uid
        await _edit(query, f"💬 Send message to user <code>{uid}</code>:",
                    InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data=f"adm_view_{uid}")]]))

    elif data.startswith("adm_view_"):
        uid = int(data.split("_")[2])
        await _show_user(query, uid)

    # Referrals
    elif data == "adm_referrals":
        stats = await db.get_stats()
        text = (
            "🔗 <b>Referral Manager</b>\n\n"
            f"Total Referrals: <b>{stats['total_referrals']}</b>\n"
            f"Referral Bonus: <b>{db.get_setting('referral_bonus', 3)}</b> searches\n\n"
            "<i>To reset referral data, use the User Manager.</i>"
        )
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔢 Change Bonus", callback_data="adm_set_refbonus")],
            [InlineKeyboardButton("🔙 Back", callback_data="adm_main")],
        ])
        await _edit(query, text, kb)

    # Requests
    elif data == "adm_requests":
        reqs = await db.get_requests(status="pending")
        if not reqs:
            await _edit(query, "📩 <b>No pending requests.</b>", admin_back_keyboard("adm_main"))
            return
        req = reqs[0]
        text = (
            f"📩 <b>Request #{req['id']}</b>\n\n"
            f"👤 {req['user_name']} (<code>{req['user_id']}</code>)\n"
            f"📅 {req['date']}\n\n"
            f"🎬 {req['text']}\n\n"
            f"<i>({len(reqs)} pending total)</i>"
        )
        await _edit(query, text, admin_requests_keyboard(req["id"]))

    elif data.startswith("adm_req_reply_"):
        req_id = int(data.replace("adm_req_reply_", ""))
        ctx.user_data[ADM_STATE] = f"req_reply_{req_id}"
        await _edit(query, "↩️ Send your reply message:", admin_back_keyboard("adm_requests"))

    elif data.startswith("adm_req_done_"):
        req_id = int(data.replace("adm_req_done_", ""))
        await db.update_request_status(req_id, "completed")
        await _edit(query, f"✅ Request #{req_id} marked as completed.", admin_back_keyboard("adm_requests"))

    elif data.startswith("adm_req_del_"):
        req_id = int(data.replace("adm_req_del_", ""))
        await db.delete_request(req_id)
        await _edit(query, f"🗑 Request #{req_id} deleted.", admin_back_keyboard("adm_requests"))

    # Stats
    elif data == "adm_stats":
        await _edit(query, "📈 <b>Statistics</b>\n\nChoose a report:", admin_stats_keyboard())

    elif data == "adm_stat_daily":
        await _send_daily_stats(query)

    elif data == "adm_stat_weekly":
        await _send_weekly_stats(query)

    elif data == "adm_stat_monthly":
        stats = await db.get_stats()
        await _edit(query, f"📅 Total searches this session: {stats['total_searches']}", admin_back_keyboard("adm_stats"))

    elif data == "adm_stat_search":
        stats = await db.get_stats()
        await _edit(query, f"🔍 Total searches: {stats['total_searches']}\n👥 Total referrals: {stats['total_referrals']}", admin_back_keyboard("adm_stats"))

    elif data == "adm_export_users":
        await _export_users(query, ctx)

    # Backup
    elif data == "adm_backup":
        await _edit(
            query,
            "💾 <b>Backup</b>",
            InlineKeyboardMarkup([
                [InlineKeyboardButton("📥 Download Backup", callback_data="adm_dl_backup")],
                [InlineKeyboardButton("♻️ Restore Backup", callback_data="adm_restore_backup")],
                [InlineKeyboardButton("🔙 Back", callback_data="adm_main")],
            ]),
        )

    elif data == "adm_dl_backup":
        await db.backup_db()
        try:
            with open("db_backup.json", "rb") as f:
                await ctx.bot.send_document(
                    chat_id=update.effective_chat.id,
                    document=InputFile(f, filename="cynemabot_backup.json"),
                    caption="💾 Database backup",
                )
        except Exception as e:
            await ctx.bot.send_message(update.effective_chat.id, f"Backup error: {e}")

    elif data == "adm_restore_backup":
        ok = await db.restore_db()
        await _edit(query, "✅ Restored." if ok else "❌ No backup found.", admin_back_keyboard("adm_backup"))

    # Logs
    elif data == "adm_logs":
        await _edit(
            query,
            "📋 <b>Logs</b>",
            InlineKeyboardMarkup([
                [InlineKeyboardButton("❗ Error Logs", callback_data="adm_log_err"),
                 InlineKeyboardButton("📝 Activity Logs", callback_data="adm_log_act")],
                [InlineKeyboardButton("🔙 Back", callback_data="adm_main")],
            ]),
        )

    elif data == "adm_log_err":
        logs = await db.get_logs("errors", 15)
        lines = [f"• {l['ts'][:16]} — {l['msg']}" for l in logs[-10:]] or ["No errors."]
        await _edit(query, "❗ <b>Error Logs</b>\n\n" + "\n".join(lines), admin_back_keyboard("adm_logs"))

    elif data == "adm_log_act":
        logs = await db.get_logs("activity", 15)
        lines = [f"• {l['ts'][:16]} — {l['msg']}" for l in logs[-10:]] or ["No activity."]
        await _edit(query, "📝 <b>Activity Logs</b>\n\n" + "\n".join(lines), admin_back_keyboard("adm_logs"))

    # Security
    elif data == "adm_security":
        await _edit(
            query,
            "🔒 <b>Security</b>\n\n"
            "• Admin-only access: ✅\n"
            "• Database backup: ✅\n"
            "• Anti-spam: ✅\n"
            "• Confirmation dialogs: ✅",
            admin_back_keyboard("adm_main"),
        )


# ════════════════════════════════════════════════════════════════
#  Admin text handler (handles pending states)
# ════════════════════════════════════════════════════════════════

async def admin_text_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_admin(update.effective_user.id):
        return

    state = ctx.user_data.get(ADM_STATE)
    if not state:
        return

    text = (update.message.text or "").strip()

    # ── User search ───────────────────────────────────────────
    if state == "search_user":
        await _lookup_user_by_text(update, ctx, text)
        ctx.user_data.pop(ADM_STATE, None)

    # ── Setting change ────────────────────────────────────────
    elif state.startswith("set_"):
        key = state[4:]
        if key in ("referral_bonus", "starting_searches"):
            try:
                value = int(text)
            except ValueError:
                await update.message.reply_text("❌ Please send a valid number.")
                return
        else:
            value = text
        await db.set_setting(key, value)
        await update.message.reply_text(f"✅ <b>{key}</b> updated to: <code>{value}</code>", parse_mode="HTML")
        ctx.user_data.pop(ADM_STATE, None)

    # ── Add/remove searches ───────────────────────────────────
    elif state.startswith("add_searches_") or state.startswith("rem_searches_"):
        uid = int(state.split("_")[-1])
        try:
            amount = int(text)
        except ValueError:
            await update.message.reply_text("❌ Send a valid number.")
            return
        if "add" in state:
            await db.add_searches(uid, amount, bonus=False)
            await update.message.reply_text(f"✅ Added {amount} free searches to <code>{uid}</code>.", parse_mode="HTML")
        else:
            await db.remove_searches(uid, amount, bonus=False)
            await update.message.reply_text(f"✅ Removed {amount} free searches from <code>{uid}</code>.", parse_mode="HTML")
        ctx.user_data.pop(ADM_STATE, None)

    # ── Add/remove bonus ──────────────────────────────────────
    elif state.startswith("add_bonus_") or state.startswith("rem_bonus_"):
        uid = int(state.split("_")[-1])
        try:
            amount = int(text)
        except ValueError:
            await update.message.reply_text("❌ Send a valid number.")
            return
        if "add" in state:
            await db.add_searches(uid, amount, bonus=True)
            await update.message.reply_text(f"✅ Added {amount} bonus searches to <code>{uid}</code>.", parse_mode="HTML")
        else:
            await db.remove_searches(uid, amount, bonus=True)
            await update.message.reply_text(f"✅ Removed {amount} bonus searches from <code>{uid}</code>.", parse_mode="HTML")
        ctx.user_data.pop(ADM_STATE, None)

    # ── Send message to user ──────────────────────────────────
    elif state.startswith("send_msg_"):
        uid = int(state.split("_")[-1])
        try:
            await ctx.bot.send_message(uid, f"📩 <b>Message from Admin:</b>\n\n{text}", parse_mode="HTML")
            await update.message.reply_text(f"✅ Message sent to <code>{uid}</code>.", parse_mode="HTML")
        except TelegramError as e:
            await update.message.reply_text(f"❌ Failed: {e}")
        ctx.user_data.pop(ADM_STATE, None)

    # ── Request reply ─────────────────────────────────────────
    elif state.startswith("req_reply_"):
        req_id = int(state.split("_")[-1])
        reqs = await db.get_requests()
        req = next((r for r in reqs if r["id"] == req_id), None)
        if req:
            try:
                await ctx.bot.send_message(
                    req["user_id"],
                    f"📩 <b>Reply to your request #{req_id}:</b>\n\n{text}",
                    parse_mode="HTML",
                )
                await db.update_request_status(req_id, "replied")
                await update.message.reply_text("✅ Reply sent.")
            except TelegramError as e:
                await update.message.reply_text(f"❌ Failed: {e}")
        ctx.user_data.pop(ADM_STATE, None)

    # ── Broadcast content ─────────────────────────────────────
    elif state == "bc_waiting":
        await _do_broadcast(update, ctx)
        ctx.user_data.pop(ADM_STATE, None)
        ctx.user_data.pop(ADM_BC_TYPE, None)


# ════════════════════════════════════════════════════════════════
#  Broadcast
# ════════════════════════════════════════════════════════════════

async def _do_broadcast(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    users = await db.get_all_users()
    bc_type = ctx.user_data.get(ADM_BC_TYPE, "text")
    msg = update.message

    sent = 0
    failed = 0
    blocked = 0
    progress_msg = await update.message.reply_text("📣 Broadcasting... 0%")

    for i, user in enumerate(users):
        uid = user["id"]
        delivered = False
        for attempt in range(3):          # up to 3 attempts per user
            try:
                if bc_type == "text":
                    await ctx.bot.send_message(uid, msg.text or "", parse_mode="HTML")
                elif bc_type == "photo" and msg.photo:
                    await ctx.bot.send_photo(uid, msg.photo[-1].file_id, caption=msg.caption or "", parse_mode="HTML")
                elif bc_type == "video" and msg.video:
                    await ctx.bot.send_video(uid, msg.video.file_id, caption=msg.caption or "", parse_mode="HTML")
                elif bc_type == "doc" and msg.document:
                    await ctx.bot.send_document(uid, msg.document.file_id, caption=msg.caption or "", parse_mode="HTML")
                elif bc_type == "anim" and msg.animation:
                    await ctx.bot.send_animation(uid, msg.animation.file_id, caption=msg.caption or "", parse_mode="HTML")
                elif bc_type == "fwd":
                    await ctx.bot.forward_message(uid, msg.chat_id, msg.message_id)
                delivered = True
                break
            except RetryAfter as e:
                # Respect Telegram's flood-wait and retry the same user
                await asyncio.sleep(e.retry_after + 1)
            except TelegramError as e:
                err_str = str(e).lower()
                if any(kw in err_str for kw in ("blocked", "deactivated", "not found", "chat not found")):
                    blocked += 1
                else:
                    logger.warning("Broadcast skip uid=%s: %s", uid, e)
                break   # non-retryable error — skip this user

        if delivered:
            sent += 1
        else:
            failed += 1

        await asyncio.sleep(BROADCAST_DELAY)

        # Update progress every 20 users
        if i % 20 == 0:
            pct = int((i / max(len(users), 1)) * 100)
            try:
                await progress_msg.edit_text(f"📣 Broadcasting... {pct}%")
            except TelegramError:
                pass

    await progress_msg.edit_text(
        f"✅ <b>Broadcast Complete!</b>\n\n"
        f"✅ Sent: {sent}\n❌ Failed: {failed}\n🚫 Blocked: {blocked}",
        parse_mode="HTML",
    )


# ════════════════════════════════════════════════════════════════
#  Helpers
# ════════════════════════════════════════════════════════════════

async def _edit(query, text: str, kb=None) -> None:
    try:
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=kb)
    except TelegramError:
        pass


async def _show_user(query, uid: int) -> None:
    user = await db.get_user(uid)
    if not user:
        await _edit(query, f"❌ User <code>{uid}</code> not found.", admin_back_keyboard("adm_users"))
        return
    remaining = user.get("free_searches", 0) + user.get("bonus_searches", 0)
    text = (
        f"👤 <b>User Details</b>\n\n"
        f"🆔 ID: <code>{user['id']}</code>\n"
        f"📛 Name: {user['name']}\n"
        f"🔖 Username: @{user.get('username') or 'N/A'}\n"
        f"📅 Joined: {user.get('join_date', 'N/A')}\n\n"
        f"🔍 Free Searches: {user.get('free_searches', 0)}\n"
        f"🎁 Bonus Searches: {user.get('bonus_searches', 0)}\n"
        f"✅ Remaining: {remaining}\n"
        f"📊 Total Searches: {user.get('total_searches', 0)}\n"
        f"👥 Referrals: {user.get('referrals', 0)}\n"
        f"🚫 Banned: {'Yes' if user.get('is_banned') else 'No'}\n"
        f"📆 Last Active: {user.get('last_active', 'N/A')}"
    )
    await _edit(query, text, admin_user_actions_keyboard(uid))


async def _lookup_user_by_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    uid: Optional[int] = None
    try:
        uid = int(text.lstrip("@"))
    except ValueError:
        # Try by username
        users = await db.get_all_users()
        handle = text.lstrip("@").lower()
        for u in users:
            if (u.get("username") or "").lower() == handle:
                uid = u["id"]
                break

    if uid is None:
        await update.message.reply_text("❌ User not found.")
        return

    user = await db.get_user(uid)
    if not user:
        await update.message.reply_text(f"❌ User <code>{uid}</code> not in database.", parse_mode="HTML")
        return

    remaining = user.get("free_searches", 0) + user.get("bonus_searches", 0)
    text_out = (
        f"👤 <b>User Details</b>\n\n"
        f"🆔 ID: <code>{user['id']}</code>\n"
        f"📛 Name: {user['name']}\n"
        f"🔖 Username: @{user.get('username') or 'N/A'}\n"
        f"📅 Joined: {user.get('join_date', 'N/A')}\n\n"
        f"🔍 Free Searches: {user.get('free_searches', 0)}\n"
        f"🎁 Bonus Searches: {user.get('bonus_searches', 0)}\n"
        f"✅ Remaining: {remaining}\n"
        f"👥 Referrals: {user.get('referrals', 0)}\n"
        f"🚫 Banned: {'Yes' if user.get('is_banned') else 'No'}"
    )
    await update.message.reply_text(text_out, parse_mode="HTML", reply_markup=admin_user_actions_keyboard(uid))


async def _export_users(query, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    users = await db.get_all_users()
    content = json.dumps(users, ensure_ascii=False, indent=2)
    buf = io.BytesIO(content.encode("utf-8"))
    buf.name = "users_export.json"
    try:
        await ctx.bot.send_document(
            chat_id=query.message.chat_id,
            document=InputFile(buf, filename="users_export.json"),
            caption=f"📤 Users export — {len(users)} users",
        )
    except TelegramError as e:
        await query.message.reply_text(f"Export error: {e}")


async def _send_daily_stats(query) -> None:
    stats = await db.get_stats()
    today = str(date.today())
    day_data = stats["daily"].get(today, {})
    text = (
        f"📅 <b>Daily Stats — {today}</b>\n\n"
        f"🔍 Searches: {day_data.get('searches', 0)}\n"
        f"👥 Active Users: {len(day_data.get('users', []))}\n\n"
        f"📊 All-time Total:\n"
        f"• Users: {stats['total_users']}\n"
        f"• Searches: {stats['total_searches']}\n"
        f"• Referrals: {stats['total_referrals']}"
    )
    await _edit(query, text, admin_back_keyboard("adm_stats"))


async def _send_weekly_stats(query) -> None:
    stats = await db.get_stats()
    lines = []
    total_s = 0
    for i in range(7):
        day = str(date.today() - timedelta(days=i))
        d = stats["daily"].get(day, {})
        s = d.get("searches", 0)
        total_s += s
        lines.append(f"• {day}: {s} searches")
    text = "📆 <b>Weekly Stats</b>\n\n" + "\n".join(lines) + f"\n\n<b>Total: {total_s}</b>"
    await _edit(query, text, admin_back_keyboard("adm_stats"))


# ======================================================================
# FILE: handlers/stats.py
# ======================================================================

# ============================================================
#  Handler — My Stats & Referral
# ============================================================

import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

import database as db
from messages import stats_caption, referral_caption

logger = logging.getLogger(__name__)


async def stats_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    usr = await db.get_user(user.id)
    if not usr:
        await update.message.reply_text("Please send /start first.")
        return

    await update.message.reply_text(
        stats_caption(usr),
        parse_mode="HTML",
    )


async def invite_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    usr = await db.get_user(user.id)
    if not usr:
        await update.message.reply_text("Please send /start first.")
        return

    bot_me = await ctx.bot.get_me()
    ref_link = f"https://t.me/{bot_me.username}?start=ref_{user.id}"

    import urllib.parse
    share_text = f"🎬 Watch movies & anime for free! Join CynemaBot:\n{ref_link}"
    encoded = urllib.parse.quote(share_text)
    share_url = f"https://t.me/share/url?url={encoded}"

    kb = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📋 Copy Link", callback_data="copy_ref")],
            [InlineKeyboardButton("📤 Share", url=share_url)],
        ]
    )

    await update.message.reply_text(
        referral_caption(ref_link, usr),
        parse_mode="HTML",
        reply_markup=kb,
    )

