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
