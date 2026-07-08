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
