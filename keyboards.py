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
