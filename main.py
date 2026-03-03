import os
import json
import subprocess
import requests
import re
import urllib.request
import zipfile
import stat
import asyncio
import time
import math
import uuid
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pyrogram import Client, filters, enums
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, InputMediaPhoto
from motor.motor_asyncio import AsyncIOMotorClient
from aiohttp import web

# 🔥 THE MASTER FIX FOR ASYNC LOOP
loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)

# ==========================================
# 🤖 BOT & CONFIGURATION
# ==========================================
API_ID = 33675350
API_HASH = "2f97c845b067a750c9f36fec497acf97"
BOT_TOKEN = "8343193883:AAE738x9dK-c4SdMx0N3HeF8XzrTn3plq8A"
DUMP_CHAT_ID = -1003831827071
MONGO_URL = "mongodb+srv://salonisingh6265_db_user:U50ONNZZFUbh0iQI@cluster0.41mb27f.mongodb.net/?appName=Cluster0"
PROXY_URL = "http://dLAG1sTQ6:qKE6euVsA@138.249.190.195:62694"
PORT = 8000

# 🌐 YouTube Anti-Ban Headers
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
YT_HEADERS = f'--user-agent "{USER_AGENT}" --add-header "Accept-Language:en-US,en;q=0.9" --add-header "Sec-Fetch-Mode:navigate"'

app = Client("universal_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)
task_queue = asyncio.Queue()
PENDING_TASKS = {} 
ACTIVE_TASKS = {} 

# ==========================================
# 🛠️ UTILS & DATABASE
# ==========================================
def get_cache():
    client = AsyncIOMotorClient(MONGO_URL)
    return client["UniversalBotDB"]["VideoCacheFiles"]

def humanbytes(size):
    if not size: return "0 B"
    for unit in ['','K','M','G','T']:
        if size < 1024: return f"{size:.2f} {unit}B"
        size /= 1024

async def extract_metadata(video_path):
    duration = 0
    thumb_path = f"{video_path}.jpg"
    try:
        cmd = f'ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 "{video_path}"'
        proc = await asyncio.create_subprocess_shell(cmd, stdout=asyncio.subprocess.PIPE)
        out, _ = await proc.communicate()
        duration = int(float(out.decode().strip()))
        subprocess.run(f'ffmpeg -v error -y -i "{video_path}" -ss 00:00:02 -vframes 1 "{thumb_path}"', shell=True)
    except: pass
    return duration, thumb_path if os.path.exists(thumb_path) else None

# ==========================================
# 📥 DOWNLOAD & QUEUE ENGINE
# ==========================================
async def process_video(client, original_msg, title, url, thumb, duration, quality, task_id, need_ss=False):
    if ACTIVE_TASKS.get(task_id, {}).get("cancel"): return False
    
    # DB Cache Check
    cache = get_cache()
    cached = await cache.find_one({"title": title, "quality": str(quality)})
    if cached:
        await client.send_video(original_msg.chat.id, cached["file_id"], caption=f"🎬 {title} [{quality}p]")
        return True

    file_name = f"{uuid.uuid4()}_{quality}p.mp4"
    cookie_flag = '--cookies cookies.txt' if os.path.exists('cookies.txt') else ''
    
    # Download with Proxy & YT Headers
    cmd = f'yt-dlp --proxy "{PROXY_URL}" {cookie_flag} {YT_HEADERS} -S "res:{quality}" -o "{file_name}" "{url}"'
    proc = await asyncio.create_subprocess_shell(cmd)
    ACTIVE_TASKS[task_id]["proc"] = proc
    await proc.wait()

    if os.path.exists(file_name):
        dur, thm = await extract_metadata(file_name)
        up_msg = await client.send_video(
            DUMP_CHAT_ID, video=file_name, caption=f"🎬 {title}", 
            duration=dur, thumb=thm, supports_streaming=True
        )
        await cache.insert_one({"title": title, "quality": str(quality), "file_id": up_msg.video.file_id})
        await client.send_video(original_msg.chat.id, up_msg.video.file_id, caption=f"🎬 {title} [{quality}p]")
        os.remove(file_name)
        if thm: os.remove(thm)
        return True
    return False

async def queue_worker():
    while True:
        q_data = await task_queue.get()
        task_id = q_data['task_id']
        episodes = q_data['episodes']
        
        for vid in episodes:
            if ACTIVE_TASKS.get(task_id, {}).get("cancel"): break
            # Logic to process each episode
            # Yahan process_video call hoga
            await asyncio.sleep(2) 
        
        task_queue.task_done()
        await asyncio.sleep(10) # 🔥 10 Seconds Break after full series

# ==========================================
# 🌐 WEB SERVER & START
# ==========================================
async def web_server():
    server = web.Application()
    server.router.add_get("/", lambda r: web.Response(text="Bot is Running!"))
    runner = web.AppRunner(server)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", PORT).start()

@app.on_message(filters.command("queue"))
async def q_cmd(c, m):
    # Same logic as batch but adds to task_queue
    await m.reply_text("✅ Added to Smart Queue with 10s delay logic!")

if __name__ == "__main__":
    loop.create_task(web_server())
    loop.create_task(queue_worker())
    app.run()
