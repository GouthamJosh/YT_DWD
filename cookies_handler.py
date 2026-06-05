"""
cookies_handler.py — Admin-only cookies management
────────────────────────────────────────────────────
Commands (ADMINS only):
  /setcookies   — reply to / attach a cookies.txt file → stored in MongoDB
  /getcookies   — download the current cookies.txt from MongoDB
  /delcookies   — delete cookies from MongoDB
  /cookiesstatus — show metadata (size, when updated, by whom)

Startup auto-detection:
  Call `auto_import_local_cookies(app)` from main() after init_mongodb().
  It will look for cookies.txt next to the script and upload it to DB
  (only if DB has no cookies yet, so it never overwrites an admin update).
"""

import os
import io
import logging
import asyncio
import tempfile
from contextlib import suppress
from datetime import timezone

from pyrogram import Client, filters
from pyrogram.types import Message, Document

import config

logger = logging.getLogger("YTBot.cookies")

# ── Admin list ─────────────────────────────────────────────────────────────────
# Comma-separated Telegram user IDs, e.g. "123456789,987654321"
_raw = os.environ.get("ADMINS", "")
ADMINS: list[int] = [int(x.strip()) for x in _raw.split(",") if x.strip().isdigit()]
logger.info(f"Admin IDs loaded: {ADMINS or '(none — all users have access)'}")


def is_admin(user_id: int) -> bool:
    """Return True if user_id is in ADMINS (or ADMINS is empty → no restriction)."""
    return not ADMINS or user_id in ADMINS


# ══════════════════════════════════════════════════════════════════════════════
#  /setcookies
# ══════════════════════════════════════════════════════════════════════════════
async def cmd_setcookies(client: Client, message: Message):
    if not is_admin(message.from_user.id):
        await message.reply_text("⛔ Admin only."); return

    # Accept the file from:
    #   • a direct document attached to the /setcookies command
    #   • a reply to a message that contains a document
    doc: Document | None = None
    if message.document:
        doc = message.document
    elif message.reply_to_message and message.reply_to_message.document:
        doc = message.reply_to_message.document

    if not doc:
        await message.reply_text(
            "📎 **How to update cookies:**\n\n"
            "Send your `cookies.txt` file and either:\n"
            "• **Attach it directly** with `/setcookies` as the caption, or\n"
            "• **Reply** to the file message with `/setcookies`\n\n"
            "ℹ️ The file must be a valid Netscape-format cookies file."
        )
        return

    # Validate filename loosely (allow any .txt or no extension)
    fname = doc.file_name or ""
    if doc.file_size and doc.file_size > 5 * 1024 * 1024:  # 5 MB sanity cap
        await message.reply_text("❌ File too large (max 5 MB)."); return

    status = await message.reply_text("⬆️ Reading cookies file...")

    try:
        # Download to memory
        bio = await client.download_media(doc, in_memory=True)
        if isinstance(bio, bytes):
            text = bio.decode("utf-8", errors="replace")
        else:
            bio.seek(0)
            text = bio.read().decode("utf-8", errors="replace")
    except Exception as e:
        await status.edit_text(f"❌ Could not download file: `{e}`"); return

    # Basic sanity check — Netscape cookies files start with a comment line
    lines = [l for l in text.splitlines() if l.strip()]
    if not lines:
        await status.edit_text("❌ File is empty."); return

    ok = await config.save_cookies(text, updated_by=message.from_user.id)
    if not ok:
        await status.edit_text(
            "❌ **DB save failed.**\n"
            "Check `MONGO_URL` environment variable and MongoDB connectivity."
        )
        return

    # Also write to local disk so yt-dlp can pick it up immediately
    _write_local_cookies(text)

    line_count = len([l for l in text.splitlines() if l.strip() and not l.startswith("#")])
    await status.edit_text(
        f"✅ **Cookies updated successfully!**\n\n"
        f"📄 File: `{fname or 'cookies.txt'}`\n"
        f"📦 Size: `{len(text):,}` chars\n"
        f"🍪 Cookie entries: `{line_count}`\n"
        f"💾 Saved to: MongoDB (`{config.COOKIES_COLLECTION}`) + local disk"
    )
    logger.info(f"Cookies updated by admin {message.from_user.id}  ({len(text)} chars)")


# ══════════════════════════════════════════════════════════════════════════════
#  /getcookies
# ══════════════════════════════════════════════════════════════════════════════
async def cmd_getcookies(client: Client, message: Message):
    if not is_admin(message.from_user.id):
        await message.reply_text("⛔ Admin only."); return

    status = await message.reply_text("📥 Fetching cookies from DB...")
    text = await config.load_cookies()
    if not text:
        await status.edit_text(
            "❌ No cookies found in the database.\n\n"
            "Use `/setcookies` (reply to / attach a `cookies.txt`) to add one."
        )
        return

    try:
        bio = io.BytesIO(text.encode("utf-8"))
        bio.name = "cookies.txt"
        await client.send_document(
            message.chat.id,
            document=bio,
            caption=f"🍪 Current `cookies.txt`\n📦 `{len(text):,}` chars",
            file_name="cookies.txt",
        )
        with suppress(Exception): await status.delete()
    except Exception as e:
        await status.edit_text(f"❌ Could not send file: `{e}`")


# ══════════════════════════════════════════════════════════════════════════════
#  /delcookies
# ══════════════════════════════════════════════════════════════════════════════
async def cmd_delcookies(client: Client, message: Message):
    if not is_admin(message.from_user.id):
        await message.reply_text("⛔ Admin only."); return

    ok = await config.delete_cookies()
    # Also remove local file
    _remove_local_cookies()
    if ok:
        await message.reply_text("🗑️ **Cookies deleted** from MongoDB and local disk.")
    else:
        await message.reply_text("❌ DB delete failed (check MongoDB connection) — local file removed if it existed.")


# ══════════════════════════════════════════════════════════════════════════════
#  /cookiesstatus
# ══════════════════════════════════════════════════════════════════════════════
async def cmd_cookiesstatus(client: Client, message: Message):
    if not is_admin(message.from_user.id):
        await message.reply_text("⛔ Admin only."); return

    meta = await config.get_cookies_meta()
    db_status = "✅ Connected" if config.get_db() is not None else "❌ Not connected"

    if not meta:
        await message.reply_text(
            f"🍪 **Cookies Status**\n\n"
            f"💾 MongoDB: {db_status}\n"
            f"📦 Collection: `{config.COOKIES_COLLECTION}`\n\n"
            f"❌ No cookies stored in DB.\n\n"
            f"Use `/setcookies` to upload a `cookies.txt`."
        )
        return

    # Format timestamp
    ts_str = "—"
    if meta.get("updated_at"):
        try:
            ts = meta["updated_at"]
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            ts_str = ts.strftime("%Y-%m-%d %H:%M:%S UTC")
        except Exception:
            ts_str = str(meta["updated_at"])

    # Local file check
    local_path = _local_cookies_path()
    local_exists = os.path.exists(local_path)
    local_info = f"✅ `{local_path}`" if local_exists else "❌ Not on disk"

    await message.reply_text(
        f"🍪 **Cookies Status**\n\n"
        f"💾 MongoDB: {db_status}\n"
        f"📦 Collection: `{config.COOKIES_COLLECTION}`\n\n"
        f"📄 **DB Record:**\n"
        f"  • Size: `{meta['size']:,}` chars\n"
        f"  • Last updated: `{ts_str}`\n"
        f"  • Updated by: `{meta['updated_by'] or 'auto-import'}`\n\n"
        f"📁 **Local file:** {local_info}\n\n"
        f"**Commands:**\n"
        f"`/getcookies` — download current cookies\n"
        f"`/setcookies` — upload new cookies (reply to file)\n"
        f"`/delcookies` — remove cookies"
    )


# ══════════════════════════════════════════════════════════════════════════════
#  Helpers — local disk read/write
# ══════════════════════════════════════════════════════════════════════════════

def _local_cookies_path() -> str:
    """Return the canonical local cookies.txt path (same dir as this file)."""
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "cookies.txt")


def _write_local_cookies(text: str):
    """Write cookie text to local cookies.txt so yt-dlp picks it up instantly."""
    try:
        path = _local_cookies_path()
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        logger.info(f"Local cookies.txt written ({len(text)} chars)")
    except Exception as e:
        logger.warning(f"Could not write local cookies.txt: {e}")


def _remove_local_cookies():
    """Delete local cookies.txt if it exists."""
    path = _local_cookies_path()
    with suppress(FileNotFoundError):
        os.remove(path)
        logger.info("Local cookies.txt removed.")


# ══════════════════════════════════════════════════════════════════════════════
#  Startup: auto-import local cookies.txt → DB  (non-destructive)
# ══════════════════════════════════════════════════════════════════════════════

async def auto_import_local_cookies(client: Client | None = None):
    """
    Called once at startup (after init_mongodb).

    Priority order:
      1. If DB already has cookies → restore them to local disk (in case of redeploy)
      2. Else if a local cookies.txt exists → upload it to DB
      3. Otherwise → log a warning

    This is intentionally non-destructive: an admin's /setcookies always wins.
    """
    local_path = _local_cookies_path()

    # Step 1 — DB → local (restore after redeploy / ephemeral FS)
    db_text = await config.load_cookies()
    if db_text:
        logger.info("Startup: cookies found in DB → restoring to local disk.")
        _write_local_cookies(db_text)
        return

    # Step 2 — local → DB  (first-time import)
    if os.path.exists(local_path):
        try:
            with open(local_path, "r", encoding="utf-8", errors="replace") as f:
                text = f.read()
            if text.strip():
                ok = await config.save_cookies(text, updated_by=0)
                if ok:
                    logger.info(f"Startup: auto-imported local cookies.txt → DB ({len(text)} chars)")
                else:
                    logger.warning("Startup: found local cookies.txt but DB save failed.")
            else:
                logger.warning("Startup: local cookies.txt is empty, skipping import.")
        except Exception as e:
            logger.warning(f"Startup: could not read local cookies.txt: {e}")
        return

    # Step 3 — no cookies anywhere
    logger.warning(
        "Startup: No cookies found (DB or local).\n"
        "  → Age-restricted / login-required downloads may fail.\n"
        f"  → Upload via /setcookies or place cookies.txt at: {local_path}"
    )


# ══════════════════════════════════════════════════════════════════════════════
#  Runtime helper: get fresh cookies path for yt-dlp
# ══════════════════════════════════════════════════════════════════════════════

async def get_cookies_path() -> str | None:
    """
    Returns the local cookies.txt path if the file exists and is non-empty.
    If local file is missing (e.g. ephemeral FS restart) but DB has data,
    re-writes local file and returns the path.

    Use this instead of a module-level COOKIES_FILE constant so each download
    always uses the freshest cookies.
    """
    local_path = _local_cookies_path()

    # Fast path — local file present
    if os.path.exists(local_path) and os.path.getsize(local_path) > 0:
        return local_path

    # Fallback — restore from DB
    db_text = await config.load_cookies()
    if db_text:
        _write_local_cookies(db_text)
        return local_path

    return None


# ══════════════════════════════════════════════════════════════════════════════
#  Register handlers
# ══════════════════════════════════════════════════════════════════════════════

async def setup_cookies_handlers(client: Client):
    """Call this from main() to register all /cookies* commands."""

    @client.on_message(filters.command("setcookies") & filters.private | filters.command("setcookies") & filters.group)
    async def _setcookies(c, m): await cmd_setcookies(c, m)

    @client.on_message(filters.command("getcookies") & filters.private | filters.command("getcookies") & filters.group)
    async def _getcookies(c, m): await cmd_getcookies(c, m)

    @client.on_message(filters.command("delcookies") & filters.private | filters.command("delcookies") & filters.group)
    async def _delcookies(c, m): await cmd_delcookies(c, m)

    @client.on_message(filters.command("cookiesstatus") & filters.private | filters.command("cookiesstatus") & filters.group)
    async def _cookiesstatus(c, m): await cmd_cookiesstatus(c, m)

    logger.info("✅ Cookies admin handlers registered.")
