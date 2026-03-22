import os
import asyncio
import logging
import time
import re
from datetime import datetime

from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, filters, ContextTypes
)
from telegram.constants import ParseMode

import yt_dlp
import requests
from PIL import Image
from io import BytesIO

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN", "8265938832:AAFReNH4L0jiNiEVWFDRgslyInhK31AOgn8")
DOWNLOAD_DIR = "/tmp/ytdl"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

AUTHORIZED_USERS = os.environ.get("AUTHORIZED_USERS", "")
AUTH_USERS = [int(u.strip()) for u in AUTHORIZED_USERS.split(",") if u.strip().isdigit()]

# Max videos allowed in one playlist batch
MAX_PLAYLIST_VIDEOS = int(os.environ.get("MAX_PLAYLIST_VIDEOS", 50))

# ─── Helpers ─────────────────────────────────────────────────────────────────

def is_authorized(user_id: int) -> bool:
    if not AUTH_USERS:
        return True
    return user_id in AUTH_USERS

def humanbytes(size: float) -> str:
    if not size:
        return "N/A"
    for unit in ["B", "KB", "MB", "GB"]:
        if size < 1024:
            return f"{size:.2f} {unit}"
        size /= 1024
    return f"{size:.2f} TB"

def progress_bar(percent: float, length: int = 15) -> str:
    filled = int(length * percent / 100)
    bar = "█" * filled + "░" * (length - filled)
    return f"[{bar}] {percent:.1f}%"

def seconds_to_hms(seconds: int) -> str:
    if not seconds:
        return "00:00"
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"

def extract_url(text: str) -> str | None:
    pattern = r'(https?://(?:www\.)?(?:youtube\.com|youtu\.be|instagram\.com|twitter\.com|x\.com|facebook\.com|tiktok\.com|dailymotion\.com|vimeo\.com)[^\s]+)'
    match = re.search(pattern, text)
    return match.group(0) if match else None

def is_playlist_url(url: str) -> bool:
    return "playlist?list=" in url or ("list=" in url and "youtube.com" in url)

def parse_index_selection(text: str, total: int) -> list[int] | None:
    """
    Parse user selection like '1,3,5' or '1-5' or '1-3,7,10-12'
    Returns 0-based indices or None if invalid.
    """
    indices = set()
    text = text.strip()
    parts = text.split(",")
    try:
        for part in parts:
            part = part.strip()
            if "-" in part:
                a, b = part.split("-", 1)
                a, b = int(a.strip()), int(b.strip())
                if a < 1 or b > total or a > b:
                    return None
                indices.update(range(a - 1, b))
            else:
                n = int(part)
                if n < 1 or n > total:
                    return None
                indices.add(n - 1)
        return sorted(indices)
    except:
        return None

# ─── In-memory Store ─────────────────────────────────────────────────────────

pending_downloads: dict[str, dict] = {}   # single video sessions
pending_playlists: dict[str, dict] = {}   # playlist sessions
# waiting_selection: user_id -> playlist_hash (for text reply parsing)
waiting_selection: dict[int, str] = {}

# ─── Video Info ──────────────────────────────────────────────────────────────

def get_video_info(url: str) -> dict | None:
    ydl_opts = {"quiet": True, "no_warnings": True, "extract_flat": False}
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            return ydl.extract_info(url, download=False)
    except Exception as e:
        logger.error(f"Info error: {e}")
        return None

def get_playlist_info(url: str) -> dict | None:
    """Fetch playlist metadata without downloading."""
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": True,   # fast — only metadata
        "playlistend": MAX_PLAYLIST_VIDEOS,
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            return ydl.extract_info(url, download=False)
    except Exception as e:
        logger.error(f"Playlist info error: {e}")
        return None

def get_single_video_formats(url: str) -> dict | None:
    """Full format info for one video."""
    ydl_opts = {"quiet": True, "no_warnings": True}
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            return ydl.extract_info(url, download=False)
    except Exception as e:
        logger.error(f"Format fetch error: {e}")
        return None

def parse_formats(info: dict) -> dict:
    formats = info.get("formats", [])
    seen = {}
    for f in formats:
        vcodec = f.get("vcodec", "none")
        acodec = f.get("acodec", "none")
        height = f.get("height")
        ext = f.get("ext", "")
        filesize = f.get("filesize") or f.get("filesize_approx") or 0

        if vcodec != "none" and height:
            key = f"{height}p"
            if key not in seen or filesize > seen[key].get("filesize", 0):
                seen[key] = {
                    "format_id": f["format_id"],
                    "height": height, "ext": ext,
                    "filesize": filesize,
                    "label": f"{height}p",
                    "fps": f.get("fps", 30),
                }
        elif vcodec == "none" and acodec != "none":
            abr = f.get("abr", 0) or 0
            key = f"audio_{int(abr)}"
            if key not in seen or filesize > seen.get(key, {}).get("filesize", 0):
                seen[key] = {
                    "format_id": f["format_id"],
                    "abr": abr, "ext": ext,
                    "filesize": filesize,
                    "label": f"🎵 Audio {int(abr)}kbps" if abr else "🎵 Audio",
                    "is_audio": True,
                }

    video_list = sorted(
        [v for v in seen.values() if not v.get("is_audio")],
        key=lambda x: x["height"], reverse=True
    )
    audio_list = sorted(
        [v for v in seen.values() if v.get("is_audio")],
        key=lambda x: x.get("abr", 0), reverse=True
    )
    return {"video": video_list, "audio": audio_list}

# ─── Progress Tracker ────────────────────────────────────────────────────────

class ProgressTracker:
    def __init__(self, message, loop, prefix=""):
        self.message = message
        self.loop = loop
        self.prefix = prefix
        self.last_update = 0
        self.filename = ""

    def hook(self, d):
        now = time.time()
        if now - self.last_update < 2.5:
            return
        self.last_update = now

        if d["status"] == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate", 0)
            downloaded = d.get("downloaded_bytes", 0)
            speed = d.get("speed", 0) or 0
            eta = d.get("eta", 0) or 0
            fname = d.get("filename", "")
            if fname:
                self.filename = os.path.basename(fname)

            if total > 0:
                percent = (downloaded / total) * 100
                bar = progress_bar(percent)
                text = (
                    f"{self.prefix}"
                    f"⬇️ **Downloading...**\n\n"
                    f"{bar}\n\n"
                    f"📦 `{humanbytes(downloaded)}` / `{humanbytes(total)}`\n"
                    f"⚡ Speed: `{humanbytes(speed)}/s`\n"
                    f"⏳ ETA: `{seconds_to_hms(eta)}`"
                )
            else:
                text = (
                    f"{self.prefix}"
                    f"⬇️ **Downloading...**\n\n"
                    f"📦 `{humanbytes(downloaded)}`\n"
                    f"⚡ Speed: `{humanbytes(speed)}/s`"
                )

            asyncio.run_coroutine_threadsafe(
                self.message.edit_text(text, parse_mode=ParseMode.MARKDOWN),
                self.loop
            )

# ─── Keyboard Builders ───────────────────────────────────────────────────────

def build_quality_keyboard(formats: dict, url_hash: str) -> InlineKeyboardMarkup:
    buttons = []
    row = []
    for vf in formats["video"][:8]:
        size = f" ({humanbytes(vf['filesize'])})" if vf["filesize"] else ""
        row.append(InlineKeyboardButton(
            f"📹 {vf['label']}{size}",
            callback_data=f"dl|{url_hash}|v|{vf['format_id']}"
        ))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)

    for af in formats["audio"][:3]:
        size = f" ({humanbytes(af['filesize'])})" if af["filesize"] else ""
        buttons.append([InlineKeyboardButton(
            f"{af['label']}{size}",
            callback_data=f"dl|{url_hash}|a|{af['format_id']}"
        )])

    buttons.append([
        InlineKeyboardButton("🖼️ Thumbnail", callback_data=f"dl|{url_hash}|thumb|0"),
        InlineKeyboardButton("❌ Cancel", callback_data=f"cancel|{url_hash}"),
    ])
    return InlineKeyboardMarkup(buttons)

def build_playlist_keyboard(pl_hash: str, total: int) -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(
            f"📥 Download All ({total} videos)",
            callback_data=f"pl|{pl_hash}|all"
        )],
        [InlineKeyboardButton(
            "🎯 Select Specific Videos",
            callback_data=f"pl|{pl_hash}|select"
        )],
        [
            InlineKeyboardButton("🖼️ All Thumbnails", callback_data=f"pl|{pl_hash}|thumbs"),
            InlineKeyboardButton("❌ Cancel", callback_data=f"pl|{pl_hash}|cancel"),
        ],
    ]
    return InlineKeyboardMarkup(buttons)

def build_playlist_quality_keyboard(pl_hash: str, formats: dict, scope: str) -> InlineKeyboardMarkup:
    """Quality selection for playlist (scope = 'all' or 'selected')"""
    buttons = []
    row = []
    for vf in formats["video"][:8]:
        row.append(InlineKeyboardButton(
            f"📹 {vf['label']}",
            callback_data=f"plq|{pl_hash}|{scope}|v|{vf['format_id']}|{vf['height']}"
        ))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)

    for af in formats["audio"][:3]:
        buttons.append([InlineKeyboardButton(
            af["label"],
            callback_data=f"plq|{pl_hash}|{scope}|a|{af['format_id']}|0"
        )])

    buttons.append([InlineKeyboardButton("❌ Cancel", callback_data=f"pl|{pl_hash}|cancel")])
    return InlineKeyboardMarkup(buttons)

# ─── Upload Helper ────────────────────────────────────────────────────────────

async def upload_file(bot, chat_id: int, filepath: str, info: dict,
                      dtype: str, status_msg, extra_caption: str = ""):
    file_size = os.path.getsize(filepath)
    if file_size > 2 * 1024 * 1024 * 1024:
        await status_msg.edit_text("❌ File >2GB — Telegram limit exceeded.")
        return False

    caption = (
        f"🎬 **{info.get('title', 'Video')[:50]}**\n"
        f"👤 `{info.get('uploader', 'Unknown')}`\n"
        f"⏱️ `{seconds_to_hms(info.get('duration', 0))}`\n"
        f"📦 `{humanbytes(file_size)}`"
        + (f"\n{extra_caption}" if extra_caption else "")
    )

    thumb_data = None
    thumb_url = info.get("thumbnail")
    if thumb_url:
        try:
            resp = requests.get(thumb_url, timeout=10)
            img = Image.open(BytesIO(resp.content)).convert("RGB")
            img.thumbnail((320, 320))
            tbuf = BytesIO()
            img.save(tbuf, format="JPEG")
            tbuf.seek(0)
            thumb_data = tbuf
        except:
            pass

    try:
        if dtype == "v":
            with open(filepath, "rb") as vf:
                await bot.send_video(
                    chat_id=chat_id, video=vf,
                    caption=caption, parse_mode=ParseMode.MARKDOWN,
                    duration=info.get("duration", 0),
                    width=info.get("width", 1280),
                    height=info.get("height", 720),
                    thumb=thumb_data,
                    supports_streaming=True,
                    write_timeout=300, read_timeout=300,
                )
        else:
            with open(filepath, "rb") as af:
                await bot.send_audio(
                    chat_id=chat_id, audio=af,
                    caption=caption, parse_mode=ParseMode.MARKDOWN,
                    duration=info.get("duration", 0),
                    title=info.get("title", "Audio")[:64],
                    performer=info.get("uploader", "Unknown"),
                    write_timeout=300, read_timeout=300,
                )
        return True
    except Exception as e:
        logger.error(f"Upload error: {e}")
        return False

# ─── Single Video Download ────────────────────────────────────────────────────

async def do_single_download(url: str, format_id: str, dtype: str,
                             info: dict, status_msg, context, chat_id: int, url_hash: str):
    loop = asyncio.get_event_loop()
    tracker = ProgressTracker(status_msg, loop)

    title_safe = re.sub(r'[^\w\s-]', '', info.get("title", "video"))[:50].strip()
    out_template = os.path.join(DOWNLOAD_DIR, f"{url_hash}_{title_safe}.%(ext)s")

    if dtype == "v":
        ydl_opts = {
            "format": f"{format_id}+bestaudio/best",
            "outtmpl": out_template,
            "merge_output_format": "mp4",
            "progress_hooks": [tracker.hook],
            "quiet": True, "no_warnings": True,
        }
    else:
        ydl_opts = {
            "format": format_id,
            "outtmpl": out_template,
            "progress_hooks": [tracker.hook],
            "quiet": True, "no_warnings": True,
            "postprocessors": [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "320",
            }],
        }

    def _dl():
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])

    await loop.run_in_executor(None, _dl)

    filepath = None
    for f in os.listdir(DOWNLOAD_DIR):
        if f.startswith(url_hash):
            filepath = os.path.join(DOWNLOAD_DIR, f)
            break

    if not filepath:
        await status_msg.edit_text("❌ Download failed — file not found.")
        return

    await status_msg.edit_text(
        f"📤 **Uploading...**\n📦 `{humanbytes(os.path.getsize(filepath))}`",
        parse_mode=ParseMode.MARKDOWN
    )

    ok = await upload_file(context.bot, chat_id, filepath, info, dtype, status_msg)
    try:
        os.remove(filepath)
    except:
        pass
    if ok:
        await status_msg.delete()

# ─── Playlist Download ────────────────────────────────────────────────────────

async def do_playlist_download(pl_hash: str, indices: list[int], dtype: str,
                               format_height: str, format_id_hint: str,
                               status_msg, context, chat_id: int):
    """Download selected videos from playlist one by one with per-video progress."""
    entry = pending_playlists.get(pl_hash)
    if not entry:
        await status_msg.edit_text("⚠️ Session expired.")
        return

    entries = entry["entries"]
    selected = [entries[i] for i in indices if i < len(entries)]
    total_sel = len(selected)
    loop = asyncio.get_event_loop()

    failed = []

    for idx, video_entry in enumerate(selected, 1):
        video_url = video_entry.get("url") or video_entry.get("webpage_url")
        video_title = video_entry.get("title", f"Video {idx}")[:50]

        prefix = f"📋 **Playlist** `{idx}/{total_sel}`\n📹 `{video_title}`\n\n"

        await status_msg.edit_text(
            f"{prefix}🔍 Fetching info...",
            parse_mode=ParseMode.MARKDOWN
        )

        # Fetch full info for this video
        video_info = await loop.run_in_executor(None, get_single_video_formats, video_url)
        if not video_info:
            failed.append(video_title)
            continue

        # Pick best format matching chosen height
        chosen_format_id = None
        if dtype == "v":
            for f in sorted(video_info.get("formats", []),
                            key=lambda x: x.get("height", 0) or 0, reverse=True):
                fh = f.get("height", 0) or 0
                if f.get("vcodec", "none") != "none" and fh <= int(format_height or 9999):
                    chosen_format_id = f["format_id"]
                    break
            if not chosen_format_id:
                chosen_format_id = "bestvideo+bestaudio/best"
        else:
            chosen_format_id = "bestaudio/best"

        v_hash = f"{pl_hash}_{idx}"
        title_safe = re.sub(r'[^\w\s-]', '', video_title)[:40].strip()
        out_template = os.path.join(DOWNLOAD_DIR, f"{v_hash}_{title_safe}.%(ext)s")

        tracker = ProgressTracker(status_msg, loop, prefix=prefix)

        if dtype == "v":
            ydl_opts = {
                "format": f"{chosen_format_id}+bestaudio/best",
                "outtmpl": out_template,
                "merge_output_format": "mp4",
                "progress_hooks": [tracker.hook],
                "quiet": True, "no_warnings": True,
            }
        else:
            ydl_opts = {
                "format": chosen_format_id,
                "outtmpl": out_template,
                "progress_hooks": [tracker.hook],
                "quiet": True, "no_warnings": True,
                "postprocessors": [{
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": "320",
                }],
            }

        try:
            def _dl():
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    ydl.download([video_url])
            await loop.run_in_executor(None, _dl)
        except Exception as e:
            logger.error(f"Playlist video dl error: {e}")
            failed.append(video_title)
            continue

        filepath = None
        for f in os.listdir(DOWNLOAD_DIR):
            if f.startswith(v_hash):
                filepath = os.path.join(DOWNLOAD_DIR, f)
                break

        if not filepath:
            failed.append(video_title)
            continue

        await status_msg.edit_text(
            f"{prefix}📤 **Uploading** `{humanbytes(os.path.getsize(filepath))}`...",
            parse_mode=ParseMode.MARKDOWN
        )

        ok = await upload_file(
            context.bot, chat_id, filepath, video_info, dtype, status_msg,
            extra_caption=f"📋 `{idx}/{total_sel}` from playlist"
        )
        try:
            os.remove(filepath)
        except:
            pass

        if not ok:
            failed.append(video_title)

    # Final summary
    if failed:
        fail_list = "\n".join(f"• `{t}`" for t in failed[:10])
        await status_msg.edit_text(
            f"✅ **Playlist done!** `{total_sel - len(failed)}/{total_sel}` uploaded.\n\n"
            f"❌ Failed ({len(failed)}):\n{fail_list}",
            parse_mode=ParseMode.MARKDOWN
        )
    else:
        await status_msg.edit_text(
            f"✅ **Playlist complete!** All `{total_sel}` videos uploaded successfully! 🎉",
            parse_mode=ParseMode.MARKDOWN
        )

    pending_playlists.pop(pl_hash, None)
    waiting_selection.pop(entry.get("user_id"), None)

# ─── Command Handlers ─────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "🎬 **YouTube Downloader Bot**\n\n"
        "Send any video or playlist link!\n\n"
        "**Supported:**\n"
        "• YouTube Videos & **Playlists** 🆕\n"
        "• YouTube Shorts\n"
        "• Instagram Reels/Posts\n"
        "• Twitter/X · TikTok · Facebook\n"
        "• Vimeo · Dailymotion\n\n"
        "**Playlist Features:**\n"
        "• 📋 Info card (total videos, duration)\n"
        "• 🎯 Select specific: `1,3,5` or `1-10` or `1-3,7,9-12`\n"
        "• 📥 Download all at once\n"
        "• 📊 Per-video progress bar\n"
        "• 🎨 Quality selection per video\n\n"
        "**Commands:** /start · /ping"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

async def ping(update: Update, context: ContextTypes.DEFAULT_TYPE):
    t = time.time()
    msg = await update.message.reply_text("🏓 Pong!")
    ms = (time.time() - t) * 1000
    await msg.edit_text(f"🏓 Pong! `{ms:.0f}ms`", parse_mode=ParseMode.MARKDOWN)

# ─── Message Handler ──────────────────────────────────────────────────────────

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = update.message.text or ""

    if not is_authorized(user.id):
        await update.message.reply_text("⛔ Not authorized.")
        return

    # ── Check if user is in selection mode ──
    if user.id in waiting_selection:
        pl_hash = waiting_selection[user.id]
        entry = pending_playlists.get(pl_hash)
        if entry:
            total = len(entry["entries"])
            indices = parse_index_selection(text, total)
            if indices is None:
                await update.message.reply_text(
                    f"❌ Invalid selection. Use format like:\n"
                    f"`1,3,5` or `1-10` or `1-3,7,10-12`\n\n"
                    f"Total videos: `{total}`",
                    parse_mode=ParseMode.MARKDOWN
                )
                return

            entry["selected_indices"] = indices
            waiting_selection.pop(user.id, None)

            # Now show quality selection
            status = await update.message.reply_text(
                f"✅ Selected `{len(indices)}` videos. Fetching quality options...",
                parse_mode=ParseMode.MARKDOWN
            )
            loop = asyncio.get_event_loop()
            # Use first selected video for quality options
            first_url = entry["entries"][indices[0]].get("url") or entry["entries"][indices[0]].get("webpage_url")
            first_info = await loop.run_in_executor(None, get_single_video_formats, first_url)

            if not first_info:
                await status.edit_text("❌ Could not fetch quality options.")
                return

            fmts = parse_formats(first_info)
            kb = build_playlist_quality_keyboard(pl_hash, fmts, "selected")
            sel_str = text.strip()
            await status.edit_text(
                f"🎨 **Choose Quality**\n\n"
                f"📋 Selected: `{len(indices)}` videos (`{sel_str}`)\n"
                f"Quality will apply to all selected videos:",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=kb
            )
            return

    # ── URL handling ──
    url = extract_url(text)
    if not url:
        await update.message.reply_text("❌ No valid URL found.")
        return

    status_msg = await update.message.reply_text(
        "🔍 **Fetching info...**", parse_mode=ParseMode.MARKDOWN
    )
    loop = asyncio.get_event_loop()

    # ── Playlist ──
    if is_playlist_url(url):
        info = await loop.run_in_executor(None, get_playlist_info, url)
        if not info or info.get("_type") != "playlist":
            # Try as single video
            pass
        else:
            entries = [e for e in (info.get("entries") or []) if e]
            if not entries:
                await status_msg.edit_text("❌ Playlist is empty or private.")
                return

            if len(entries) > MAX_PLAYLIST_VIDEOS:
                entries = entries[:MAX_PLAYLIST_VIDEOS]

            pl_hash = str(abs(hash(url + str(time.time()))))[:10]
            total_duration = sum(e.get("duration", 0) or 0 for e in entries)

            pending_playlists[pl_hash] = {
                "url": url,
                "entries": entries,
                "user_id": user.id,
                "info": info,
            }

            pl_title = info.get("title", "Unknown Playlist")[:60]
            uploader = info.get("uploader") or info.get("channel") or "Unknown"

            # Build video list preview (first 10)
            video_list = ""
            for i, e in enumerate(entries[:10], 1):
                dur = seconds_to_hms(e.get("duration", 0) or 0)
                t = (e.get("title") or f"Video {i}")[:35]
                video_list += f"`{i:02d}.` {t} `[{dur}]`\n"
            if len(entries) > 10:
                video_list += f"_...and {len(entries) - 10} more_\n"

            caption = (
                f"📋 **Playlist Found!**\n\n"
                f"📌 **{pl_title}**\n"
                f"👤 `{uploader}`\n"
                f"🎬 Videos: `{len(entries)}`"
                + (f" _(capped at {MAX_PLAYLIST_VIDEOS})_" if len(info.get("entries", [])) > MAX_PLAYLIST_VIDEOS else "")
                + f"\n⏱️ Total: `{seconds_to_hms(total_duration)}`\n\n"
                f"**Videos:**\n{video_list}\n"
                f"**What do you want to download?**"
            )

            thumb_url = info.get("thumbnails", [{}])[-1].get("url") if info.get("thumbnails") else None

            await status_msg.delete()
            try:
                if thumb_url:
                    await update.message.reply_photo(
                        photo=thumb_url, caption=caption,
                        parse_mode=ParseMode.MARKDOWN,
                        reply_markup=build_playlist_keyboard(pl_hash, len(entries))
                    )
                else:
                    await update.message.reply_text(
                        caption, parse_mode=ParseMode.MARKDOWN,
                        reply_markup=build_playlist_keyboard(pl_hash, len(entries))
                    )
            except Exception:
                await update.message.reply_text(
                    caption, parse_mode=ParseMode.MARKDOWN,
                    reply_markup=build_playlist_keyboard(pl_hash, len(entries))
                )
            return

    # ── Single Video ──
    info = await loop.run_in_executor(None, get_video_info, url)
    if not info:
        await status_msg.edit_text("❌ Could not fetch video info.")
        return

    formats = parse_formats(info)
    title = info.get("title", "Unknown")[:60]
    duration = seconds_to_hms(info.get("duration", 0))
    uploader = info.get("uploader", "Unknown")
    view_count = info.get("view_count", 0) or 0
    like_count = info.get("like_count", 0) or 0
    upload_date = info.get("upload_date", "")
    if upload_date:
        try:
            upload_date = datetime.strptime(upload_date, "%Y%m%d").strftime("%d %b %Y")
        except:
            pass

    url_hash = str(abs(hash(url)))[:10]
    pending_downloads[url_hash] = {
        "url": url, "info": info, "formats": formats, "user_id": user.id,
    }

    caption = (
        f"🎬 **{title}**\n\n"
        f"👤 `{uploader}`\n"
        f"⏱️ `{duration}`\n"
        f"📅 `{upload_date}`\n"
        f"👁️ `{view_count:,}` views\n"
        f"❤️ `{like_count:,}` likes\n\n"
        f"**Choose quality:**"
    )

    keyboard = build_quality_keyboard(formats, url_hash)
    await status_msg.delete()

    thumb_url = info.get("thumbnail")
    try:
        if thumb_url:
            await update.message.reply_photo(
                photo=thumb_url, caption=caption,
                parse_mode=ParseMode.MARKDOWN, reply_markup=keyboard
            )
        else:
            raise Exception("no thumb")
    except:
        await update.message.reply_text(
            caption, parse_mode=ParseMode.MARKDOWN, reply_markup=keyboard
        )

# ─── Callback Handler ─────────────────────────────────────────────────────────

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data.split("|")
    action = data[0]

    # ── Single video cancel ──
    if action == "cancel":
        pending_downloads.pop(data[1], None)
        await query.message.delete()
        return

    # ── Single video download ──
    if action == "dl":
        _, url_hash, dtype, format_id = data
        entry = pending_downloads.get(url_hash)
        if not entry:
            await query.message.reply_text("⚠️ Session expired. Send the link again.")
            return
        if query.from_user.id != entry["user_id"]:
            await query.answer("❌ Not your request!", show_alert=True)
            return

        url = entry["url"]
        info = entry["info"]

        if dtype == "thumb":
            thumb_url = info.get("thumbnail")
            if not thumb_url:
                await query.message.reply_text("❌ No thumbnail.")
                return
            status = await query.message.reply_text("🖼️ Downloading thumbnail...")
            try:
                resp = requests.get(thumb_url, timeout=15)
                img = Image.open(BytesIO(resp.content))
                buf = BytesIO()
                img.save(buf, format="JPEG")
                buf.seek(0)
                await query.message.reply_document(
                    document=buf,
                    filename=f"{info.get('title','thumb')[:40]}_thumbnail.jpg",
                    caption=f"🖼️ **{info.get('title','Thumbnail')[:50]}**",
                    parse_mode=ParseMode.MARKDOWN
                )
                await status.delete()
            except Exception as e:
                await status.edit_text(f"❌ Failed: {e}")
            return

        status = await query.message.reply_text(
            "⚙️ **Preparing...**", parse_mode=ParseMode.MARKDOWN
        )
        try:
            await do_single_download(
                url, format_id, dtype, info, status, context,
                query.message.chat_id, url_hash
            )
        except Exception as e:
            logger.error(f"Download error: {e}")
            await status.edit_text(f"❌ **Failed!**\n\n`{str(e)[:300]}`", parse_mode=ParseMode.MARKDOWN)
        finally:
            for f in os.listdir(DOWNLOAD_DIR):
                if f.startswith(url_hash):
                    try: os.remove(os.path.join(DOWNLOAD_DIR, f))
                    except: pass
            pending_downloads.pop(url_hash, None)
        return

    # ── Playlist actions ──
    if action == "pl":
        _, pl_hash, sub = data
        entry = pending_playlists.get(pl_hash)

        if sub == "cancel":
            pending_playlists.pop(pl_hash, None)
            waiting_selection.pop(query.from_user.id, None)
            await query.message.delete()
            return

        if not entry:
            await query.message.reply_text("⚠️ Session expired.")
            return
        if query.from_user.id != entry["user_id"]:
            await query.answer("❌ Not your request!", show_alert=True)
            return

        entries = entry["entries"]

        if sub == "thumbs":
            status = await query.message.reply_text(
                "🖼️ **Downloading all thumbnails...**", parse_mode=ParseMode.MARKDOWN
            )
            sent = 0
            for i, e in enumerate(entries, 1):
                thumb_url = e.get("thumbnail") or e.get("thumbnails", [{}])[-1].get("url") if e.get("thumbnails") else None
                if not thumb_url:
                    continue
                try:
                    resp = requests.get(thumb_url, timeout=10)
                    img = Image.open(BytesIO(resp.content))
                    buf = BytesIO()
                    img.save(buf, format="JPEG")
                    buf.seek(0)
                    t = (e.get("title") or f"Video {i}")[:40]
                    await context.bot.send_document(
                        chat_id=query.message.chat_id,
                        document=buf, filename=f"{i:02d}_{t}.jpg",
                        caption=f"🖼️ `{i}.` {t}", parse_mode=ParseMode.MARKDOWN
                    )
                    sent += 1
                    await asyncio.sleep(0.5)
                except:
                    pass
                if i % 5 == 0:
                    await status.edit_text(
                        f"🖼️ Thumbnails: `{i}/{len(entries)}`...",
                        parse_mode=ParseMode.MARKDOWN
                    )
            await status.edit_text(
                f"✅ Sent `{sent}` thumbnails!", parse_mode=ParseMode.MARKDOWN
            )
            return

        if sub == "select":
            waiting_selection[query.from_user.id] = pl_hash
            total = len(entries)
            # Build numbered list
            vid_list = ""
            for i, e in enumerate(entries[:20], 1):
                dur = seconds_to_hms(e.get("duration", 0) or 0)
                t = (e.get("title") or f"Video {i}")[:35]
                vid_list += f"`{i:02d}.` {t} `[{dur}]`\n"
            if total > 20:
                vid_list += f"_...{total - 20} more_"

            await query.message.reply_text(
                f"🎯 **Select Videos to Download**\n\n"
                f"Total: `{total}` videos\n\n"
                f"{vid_list}\n\n"
                f"**Reply with your selection:**\n"
                f"• Single: `5`\n"
                f"• Multiple: `1,3,5,8`\n"
                f"• Range: `1-10`\n"
                f"• Mixed: `1-3,7,10-12`",
                parse_mode=ParseMode.MARKDOWN
            )
            return

        if sub == "all":
            # Show quality selection using first video
            status = await query.message.reply_text(
                "🔍 Fetching quality options...", parse_mode=ParseMode.MARKDOWN
            )
            loop = asyncio.get_event_loop()
            first_url = entries[0].get("url") or entries[0].get("webpage_url")
            first_info = await loop.run_in_executor(None, get_single_video_formats, first_url)
            if not first_info:
                await status.edit_text("❌ Could not fetch quality options.")
                return
            fmts = parse_formats(first_info)
            kb = build_playlist_quality_keyboard(pl_hash, fmts, "all")
            await status.edit_text(
                f"🎨 **Choose Quality**\n\n"
                f"📋 Will apply to all `{len(entries)}` videos:",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=kb
            )
            return

    # ── Playlist quality chosen ──
    if action == "plq":
        # plq|pl_hash|scope|dtype|format_id|height
        _, pl_hash, scope, dtype, format_id, height = data
        entry = pending_playlists.get(pl_hash)
        if not entry:
            await query.message.reply_text("⚠️ Session expired.")
            return
        if query.from_user.id != entry["user_id"]:
            await query.answer("❌ Not your request!", show_alert=True)
            return

        if scope == "all":
            indices = list(range(len(entry["entries"])))
        else:
            indices = entry.get("selected_indices", [])
            if not indices:
                await query.message.reply_text("⚠️ No videos selected.")
                return

        status = await query.message.reply_text(
            f"🚀 **Starting playlist download...**\n"
            f"📋 `{len(indices)}` videos | Quality: `{'Audio' if dtype == 'a' else height + 'p'}`",
            parse_mode=ParseMode.MARKDOWN
        )

        asyncio.create_task(
            do_playlist_download(
                pl_hash, indices, dtype, height, format_id,
                status, context, query.message.chat_id
            )
        )
        return

# ─── Health Check HTTP Server (for Koyeb) ────────────────────────────────────

from aiohttp import web

async def health_handler(request):
    return web.Response(
        text='{"status":"ok","bot":"running"}',
        content_type="application/json"
    )

async def start_health_server():
    port = int(os.environ.get("PORT", 8000))
    app_web = web.Application()
    app_web.router.add_get("/", health_handler)
    app_web.router.add_get("/health", health_handler)
    runner = web.AppRunner(app_web)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info(f"Health check server on port {port}")

# ─── Main ────────────────────────────────────────────────────────────────────

async def run_bot():
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN environment variable not set!")

    await start_health_server()

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .read_timeout(60)
        .write_timeout(300)
        .connect_timeout(30)
        .pool_timeout(30)
        .build()
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", start))
    app.add_handler(CommandHandler("ping", ping))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(CallbackQueryHandler(handle_callback))

    logger.info("Bot started!")
    await app.initialize()
    await app.start()
    await app.updater.start_polling(drop_pending_updates=True)

    try:
        await asyncio.Event().wait()
    finally:
        await app.updater.stop()
        await app.stop()
        await app.shutdown()

if __name__ == "__main__":
    asyncio.run(run_bot())
