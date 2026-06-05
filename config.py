"""
config.py — MongoDB connection + Cookies DB layer
──────────────────────────────────────────────────
Env vars used:
  MONGO_URL           : MongoDB connection URI  (required)
  DB_NAME             : database name           (default: ytbot)
  COOKIES_COLLECTION  : collection name         (default: cookies)
"""

import os
import logging
from motor.motor_asyncio import AsyncIOMotorClient

logger = logging.getLogger("YTBot.config")

# ── Connection settings ────────────────────────────────────────────────────────
MONGO_URL          = os.environ.get("MONGO_URL", "")
DB_NAME            = os.environ.get("DB_NAME", "ytbot")
COOKIES_COLLECTION = os.environ.get("COOKIES_COLLECTION", "cookies")  # ← configurable

# ── Globals (populated by init_mongodb) ───────────────────────────────────────
_client: AsyncIOMotorClient | None = None
_db     = None


async def init_mongodb():
    """Connect to MongoDB. Call once at bot startup."""
    global _client, _db
    if not MONGO_URL:
        logger.warning("MONGO_URL not set — cookies DB disabled.")
        return
    try:
        _client = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=8000)
        # Verify connection
        await _client.admin.command("ping")
        _db = _client[DB_NAME]
        logger.info(f"✅ MongoDB connected  db={DB_NAME!r}  collection={COOKIES_COLLECTION!r}")
    except Exception as e:
        logger.error(f"❌ MongoDB connection failed: {e}")
        _client = None
        _db     = None


async def close_mongodb():
    """Gracefully close the MongoDB connection."""
    global _client, _db
    if _client:
        _client.close()
        _client = None
        _db     = None
        logger.info("MongoDB connection closed.")


def get_db():
    """Return the database handle (or None if not connected)."""
    return _db


# ══════════════════════════════════════════════════════════════════════════════
#  Cookies CRUD
#  Document schema:  { _id: "cookies", data: "<file contents str>",
#                      updated_at: <datetime>, updated_by: <int user_id> }
# ══════════════════════════════════════════════════════════════════════════════

_COOKIES_DOC_ID = "cookies"


async def save_cookies(text: str, updated_by: int = 0) -> bool:
    """
    Upsert the cookies document in MongoDB.
    Returns True on success, False if DB is unavailable.
    """
    db = get_db()
    if db is None:
        return False
    from datetime import datetime, timezone
    try:
        await db[COOKIES_COLLECTION].update_one(
            {"_id": _COOKIES_DOC_ID},
            {"$set": {
                "data":       text,
                "updated_at": datetime.now(timezone.utc),
                "updated_by": updated_by,
            }},
            upsert=True,
        )
        logger.info(f"Cookies saved to DB by user {updated_by}  ({len(text)} chars)")
        return True
    except Exception as e:
        logger.error(f"save_cookies: {e}")
        return False


async def load_cookies() -> str | None:
    """
    Fetch cookie text from MongoDB.
    Returns the raw Netscape-format string, or None if not found / DB unavailable.
    """
    db = get_db()
    if db is None:
        return None
    try:
        doc = await db[COOKIES_COLLECTION].find_one({"_id": _COOKIES_DOC_ID})
        if doc and doc.get("data"):
            return doc["data"]
        return None
    except Exception as e:
        logger.error(f"load_cookies: {e}")
        return None


async def delete_cookies() -> bool:
    """Remove the cookies document. Returns True on success."""
    db = get_db()
    if db is None:
        return False
    try:
        await db[COOKIES_COLLECTION].delete_one({"_id": _COOKIES_DOC_ID})
        logger.info("Cookies deleted from DB.")
        return True
    except Exception as e:
        logger.error(f"delete_cookies: {e}")
        return False


async def get_cookies_meta() -> dict | None:
    """
    Return metadata (updated_at, updated_by, size) without the full cookie data.
    Returns None if no cookies document exists.
    """
    db = get_db()
    if db is None:
        return None
    try:
        doc = await db[COOKIES_COLLECTION].find_one(
            {"_id": _COOKIES_DOC_ID},
            {"data": 1, "updated_at": 1, "updated_by": 1},
        )
        if not doc:
            return None
        return {
            "updated_at": doc.get("updated_at"),
            "updated_by": doc.get("updated_by", 0),
            "size":       len(doc.get("data") or ""),
        }
    except Exception as e:
        logger.error(f"get_cookies_meta: {e}")
        return None
