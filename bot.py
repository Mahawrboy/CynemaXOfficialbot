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
