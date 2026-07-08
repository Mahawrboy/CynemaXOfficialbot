# CynemaBot

A production-ready Telegram Movie & Anime Bot built with python-telegram-bot v21+ and aiohttp. Users can search movies, TV shows, and anime via TMDB, stream via VidLink, earn bonus searches through referrals, and admins get a full management panel.

## Run & Operate

- **Dev**: `python3 bot.py` — start the bot (managed by the `CynemaBot` workflow)
- **Deploy**: Click **Publish** in Replit — configured as a `vm` (always-on) deployment

## Deployment Architecture

- **Type**: `vm` — the VM stays alive continuously, ideal for polling bots
- **Health server**: A lightweight HTTP server runs on `$PORT` in a daemon thread, answering `200 OK` so Replit's uptime monitoring and health probes succeed
- **Outer retry loop**: If `run_polling()` crashes, the process waits (5→10→20→40→60s backoff) and restarts itself — no manual intervention needed
- **Graceful shutdown**: SIGTERM is caught by PTB's `stop_signals`; `post_shutdown()` flushes the DB to disk before the process exits
- **Network reconnection**: PTB's internal `Updater` handles transient `NetworkError` and `TimedOut` with exponential backoff automatically; `Conflict` errors are suppressed (not user-facing)

## Stack

- Python 3.11
- python-telegram-bot v21.6 (async, PTB-native event loop)
- aiohttp v3.9.5 (shared session for TMDB requests)
- aiofiles v23.2.1 (async JSON database I/O)
- TMDB API (movies, TV shows, anime)
- VidLink (streaming links)

## File Structure

```
bot.py              — Entry point; PTB v21+ pattern (run_polling owns the loop)
config.py           — All configuration constants (tokens, channels, limits)
database.py         — Async JSON DB with asyncio.Lock, auto-save, backup
tmdb.py             — TMDB API client (shared aiohttp session, retry logic)
keyboards.py        — All InlineKeyboardMarkup + ReplyKeyboardMarkup builders
messages.py         — All message templates (HTML formatted)
force_join.py       — Channel membership verification (public + private)
handlers/
  start.py          — /start, referral parsing, force-join gate, welcome
  search.py         — Movie/Anime/WebSeries search, TMDB results, credit system
  stats.py          — My Stats, Invite/Referral panel
  request.py        — Movie request forwarding to admin
  admin.py          — Full admin panel (dashboard, broadcast, user mgmt, settings…)
db.json             — Live JSON database (auto-created/saved)
db_backup.json      — Backup created via /adminpanel → Backup → Download
```

## Configuration (config.py)

| Key | Description |
|-----|-------------|
| `BOT_TOKEN` | Telegram bot token |
| `ADMIN_ID` | Your Telegram user ID for admin access |
| `TMDB_API_KEY` | TMDB API key |
| `START_SEARCH` | Free searches for new users (default 3) |
| `REF_BONUS` | Bonus searches per referral (default 3) |
| `CHANNELS` | Force-join channels (id, link, name, force_join flag) |

## Key Design Decisions

- **PTB v21+ event loop**: `run_polling()` is called directly from a sync `main()` — never wrapped in `asyncio.run()` to avoid "loop already running" errors.
- **asyncio.Lock**: All DB reads/writes go through a single lock to prevent concurrent corruption.
- **Bonus before free**: Credit deduction always uses bonus_searches first, then free_searches.
- **Force join**: Uses `bot.get_chat_member()` — works for both `@public_handle` and integer private channel IDs.
- **Shared aiohttp session**: One session reused across all TMDB calls (initialised in `post_init`).
- **Auto-save**: A background `asyncio.create_task` saves the DB every 30 seconds if dirty.

## User Preferences

- Keep all credentials and links in config.py only — never hardcode elsewhere.
- Admin ID must be set in config.py before deployment.
