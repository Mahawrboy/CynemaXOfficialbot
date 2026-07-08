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
