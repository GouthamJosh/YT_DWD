"""
╔══════════════════════════════════════╗
║     All-In-One Downloader Bot        ║
║  YouTube • Instagram • TikTok        ║
║  Facebook • Twitter • Terabox        ║
║  Vimeo • Spotify • SoundCloud        ║
╚══════════════════════════════════════╝
"""

import os, re, time, asyncio, logging, requests
from aiohttp import web
from io import BytesIO
from PIL import Image

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, filters, ContextTypes
)
from telegram.constants import ParseMode
from telegram.request import HTTPXRequest
import yt_dlp

logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# ╔══════════════════════════════════════╗
# ║         HARDCODED CONFIG             ║
# ╚══════════════════════════════════════╝

BOT_TOKEN        = "8489527645:AAGokjooAXkg2L6qXhr0ThG1rPahxjEUQ5Q"  # ← NAYA TOKEN DAALO @BotFather se
PROXY_URL        = "http://dLAG1sTQ6:qKE6euVsA@138.249.190.195:62694"
YT_API_KEY       = "TD_Io4XmlQvyLYAlLNBnhUz6KW1URlTsdJL"
YT_API_BASE      = "https://yt.teamdev.sbs/api/v1/"
TERABOX_API_KEY  = "teamdev_jirvspco3y"
TERABOX_API_BASE = "https://api.teamdev.sbs/v2/download"
MULTI_API_BASE   = "https://downr.org/.netlify/functions"
AUTH_USERS       = []   # khali = sabko allow | e.g. [123456789]
MAX_PLAYLIST     = 50

COOKIES_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cookies.txt")
DOWNLOAD_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "downloads")
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

PROXIES = {"http": PROXY_URL, "https": PROXY_URL} if PROXY_URL else {}
MULTI_HDR = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
    "Origin": "https://downr.org", "Referer": "https://downr.org/", "Content-Type": "application/json",
}

# ══════════════════════════════════════════
#              HELPERS
# ══════════════════════════════════════════
def humanbytes(s):
    if not s: return "N/A"
    for u in ["B","KB","MB","GB"]:
        if s < 1024: return f"{s:.1f} {u}"
        s /= 1024
    return f"{s:.1f} TB"

def hms(sec):
    if not sec: return "00:00"
    sec=int(sec); h,r=divmod(sec,3600); m,s=divmod(r,60)
    return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"

def pbar(p,n=14):
    f=int(n*p/100); return f"[{'█'*f}{'░'*(n-f)}] {p:.1f}%"

def is_auth(uid): return not AUTH_USERS or uid in AUTH_USERS
def safe_name(s,n=50): return re.sub(r"[^\w\s-]","",s or "file")[:n].strip() or "file"
def _url(t): m=re.search(r"(https?://[^\s]+)",t); return m.group(0) if m else None

PLAT = {
    "youtube":    r"(youtube\.com/(watch|shorts|live)|youtu\.be/|music\.youtube\.com/)",
    "instagram":  r"instagram\.com/",
    "facebook":   r"(facebook\.com|fb\.watch)/",
    "twitter":    r"(twitter\.com|x\.com)/",
    "tiktok":     r"tiktok\.com/",
    "soundcloud": r"soundcloud\.com/",
    "terabox":    r"(terabox\.com|1024terabox\.com|teraboxapp\.com|terasharefile\.com|4funbox\.com|mirrobox\.com|nephobox\.com|freeterabox\.com|terabox\.fun|terabox\.club|terabox\.link|terabox\.ws)/",
    "vimeo":      r"(vimeo\.com|player\.vimeo\.com)/",
    "spotify":    r"open\.spotify\.com/",
    "pinterest":  r"(pinterest\.|pin\.it/)",
}
PLAT_LABEL = {
    "youtube":"▶️ YouTube","instagram":"📸 Instagram","facebook":"📘 Facebook",
    "twitter":"𝕏 Twitter/X","tiktok":"🎵 TikTok","soundcloud":"☁️ SoundCloud",
    "terabox":"📦 Terabox","vimeo":"🎬 Vimeo","spotify":"🎧 Spotify","pinterest":"📌 Pinterest",
}

def detect(url):
    for p,pat in PLAT.items():
        if re.search(pat,url,re.I): return p
    return None

# ══════════════════════════════════════════
#              API FETCHERS
# ══════════════════════════════════════════
def api_youtube(url,fmt="mp4"):
    url=re.sub(r"[?&]si=[^&]+","",url.strip())
    m=re.match(r"https?://youtu\.be/([\w\-]+)",url)
    if m: url=f"https://www.youtube.com/watch?v={m.group(1)}"
    try:
        r=requests.get(YT_API_BASE,params={"url":url,"key":YT_API_KEY,"fmt":fmt},timeout=30,proxies=PROXIES)
        r.raise_for_status(); d=r.json()
        if d.get("status")!="success": return None
        return {"title":d.get("title","Unknown"),"thumbnail":d.get("thumbnail",""),
                "duration":d.get("duration",0),"filesize_mb":d.get("filesize_mb",0),
                "download_url":d.get("download_url",""),"quality":d.get("format",fmt)}
    except Exception as e: logger.warning(f"YT API: {e}"); return None

def api_terabox(url):
    try:
        r=requests.get(TERABOX_API_BASE,params={"url":url,"api":TERABOX_API_KEY,"json":"1"},timeout=30,proxies=PROXIES)
        r.raise_for_status(); d=r.json()
        if not d.get("success"): return None
        fi=d.get("file",{}); th=""
        if isinstance(fi.get("thumbnails"),dict): th=fi["thumbnails"].get("url","")
        return {"title":fi.get("name","Unknown"),"thumbnail":th,
                "filesize_mb":fi.get("size_mb",0),"filesize_str":fi.get("size_str",""),
                "download_url":fi.get("link",""),"format":fi.get("name","").rsplit(".",1)[-1].upper() if "." in fi.get("name","") else ""}
    except Exception as e: logger.warning(f"Terabox API: {e}"); return None

def api_multi(url):
    try:
        s=requests.Session()
        try: s.get(f"{MULTI_API_BASE}/analytics",headers=MULTI_HDR,timeout=8,proxies=PROXIES)
        except: pass
        r=s.post(f"{MULTI_API_BASE}/nyt",json={"url":url},headers=MULTI_HDR,timeout=30,proxies=PROXIES)
        if r.status_code!=200: return None
        d=r.json()
        if d.get("error"): return None
        medias=d.get("medias",[])
        if not medias: return None
        def sc(m):
            t=(m.get("type","")or"").lower(); q=(m.get("quality","")or"").lower()
            s={"mp4":10,"video":9,"image":5,"audio":3}.get(t,0)
            rs=re.search(r"(\d+)x(\d+)",q);
            if rs: s+=int(rs.group(1))
            return s
        best=max(medias,key=sc)
        return {"title":d.get("title","")or d.get("author",""),"author":d.get("author",""),
                "thumbnail":"","duration":0,"format":best.get("type","mp4"),
                "quality":best.get("quality",""),"filesize_mb":0,
                "download_url":best.get("url",""),"all_medias":medias}
    except Exception as e: logger.warning(f"Multi API: {e}"); return None

def api_spotify(url):
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            br=p.chromium.launch(headless=True)
            try:
                pg=br.new_page(); pg.goto("https://spotidown.app/",timeout=30000)
                pg.fill('input[name="url"]',url); pg.click("#send")
                pg.wait_for_selector('form[name="submitspurl"]',timeout=30000)
                title=author=""
                try: title=(pg.text_content(".music-title",timeout=3000)or"").strip()
                except: pass
                try: author=(pg.text_content(".music-artist",timeout=3000)or"").strip()
                except: pass
                pg.click('form[name="submitspurl"] button')
                pg.wait_for_selector('a[href*="rapid.spotidown"]',timeout=30000)
                dl=pg.get_attribute('a[href*="rapid.spotidown"]','href')or""
                return {"title":title or "Track","author":author,"download_url":dl,"format":"mp3"}
            finally: br.close()
    except ImportError: logger.warning("playwright not installed"); return None
    except Exception as e: logger.warning(f"Spotify: {e}"); return None

# ══════════════════════════════════════════
#              yt-dlp
# ══════════════════════════════════════════
def ydl_opts():
    o={"quiet":True,"no_warnings":True,"http_headers":{"User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36"}}
    if os.path.exists(COOKIES_FILE): o["cookiefile"]=COOKIES_FILE
    if PROXY_URL: o["proxy"]=PROXY_URL
    return o

def ydl_info(url):
    try:
        with yt_dlp.YoutubeDL(ydl_opts()) as y: return y.extract_info(url,download=False)
    except Exception as e: logger.error(f"ydl info: {e}"); return None

def parse_fmts(info):
    seen={}
    for f in info.get("formats",[]):
        vc=f.get("vcodec","none"); ac=f.get("acodec","none")
        h=f.get("height"); sz=f.get("filesize")or f.get("filesize_approx")or 0
        if vc!="none" and h:
            k=f"{h}p"
            if k not in seen or sz>seen[k].get("sz",0):
                seen[k]={"fid":f["format_id"],"h":h,"sz":sz,"label":f"{h}p"}
        elif vc=="none" and ac!="none":
            abr=int(f.get("abr",0)or 0); k=f"a{abr}"
            if k not in seen or sz>seen.get(k,{}).get("sz",0):
                seen[k]={"fid":f["format_id"],"abr":abr,"sz":sz,"label":f"🎵 {abr}kbps" if abr else "🎵 Audio","is_audio":True}
    vids=sorted([v for v in seen.values() if not v.get("is_audio")],key=lambda x:x["h"],reverse=True)
    auds=sorted([v for v in seen.values() if v.get("is_audio")],key=lambda x:x.get("abr",0),reverse=True)
    return {"video":vids,"audio":auds}

# ══════════════════════════════════════════
#              PROGRESS
# ══════════════════════════════════════════
class Trk:
    def __init__(self,msg,loop,pre=""):
        self.msg=msg; self.loop=loop; self.pre=pre; self.last=0
    def hook(self,d):
        now=time.time()
        if now-self.last<3: return
        self.last=now
        if d["status"]!="downloading": return
        tot=d.get("total_bytes")or d.get("total_bytes_estimate",0)
        done=d.get("downloaded_bytes",0); spd=d.get("speed",0)or 0; eta=d.get("eta",0)or 0
        if tot: text=f"{self.pre}⬇️ **Downloading...**\n\n{pbar(done/tot*100)}\n\n📦 `{humanbytes(done)}`/`{humanbytes(tot)}`\n⚡ `{humanbytes(spd)}/s`\n⏳ `{hms(eta)}`"
        else:   text=f"{self.pre}⬇️ **Downloading...**\n\n📦 `{humanbytes(done)}`\n⚡ `{humanbytes(spd)}/s`"
        asyncio.run_coroutine_threadsafe(self.msg.edit_text(text,parse_mode=ParseMode.MARKDOWN),self.loop)

# ══════════════════════════════════════════
#              STORE
# ══════════════════════════════════════════
pending={}; playlists={}; waiting={}

# ══════════════════════════════════════════
#              KEYBOARDS
# ══════════════════════════════════════════
def kb_q(fmts,uh):
    btns=[]; row=[]
    for v in fmts["video"][:8]:
        sz=f" ({humanbytes(v['sz'])})" if v["sz"] else ""
        row.append(InlineKeyboardButton(f"📹 {v['label']}{sz}",callback_data=f"dl|{uh}|v|{v['fid']}"))
        if len(row)==2: btns.append(row); row=[]
    if row: btns.append(row)
    for a in fmts["audio"][:3]:
        sz=f" ({humanbytes(a['sz'])})" if a["sz"] else ""
        btns.append([InlineKeyboardButton(f"{a['label']}{sz}",callback_data=f"dl|{uh}|a|{a['fid']}")])
    btns.append([InlineKeyboardButton("🖼️ Thumbnail",callback_data=f"dl|{uh}|thumb|0"),
                 InlineKeyboardButton("❌ Cancel",callback_data=f"cancel|{uh}")])
    return InlineKeyboardMarkup(btns)

def kb_pl(ph,tot):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"📥 Sab Download ({tot})",callback_data=f"pl|{ph}|all")],
        [InlineKeyboardButton("🎯 Select Karo",callback_data=f"pl|{ph}|select")],
        [InlineKeyboardButton("🖼️ Thumbnails",callback_data=f"pl|{ph}|thumbs"),
         InlineKeyboardButton("❌ Cancel",callback_data=f"pl|{ph}|cancel")]])

def kb_plq(ph,fmts,scope):
    btns=[]; row=[]
    for v in fmts["video"][:8]:
        row.append(InlineKeyboardButton(f"📹 {v['label']}",callback_data=f"plq|{ph}|{scope}|v|{v['fid']}|{v['h']}"))
        if len(row)==2: btns.append(row); row=[]
    if row: btns.append(row)
    for a in fmts["audio"][:3]:
        btns.append([InlineKeyboardButton(a["label"],callback_data=f"plq|{ph}|{scope}|a|{a['fid']}|0")])
    btns.append([InlineKeyboardButton("❌ Cancel",callback_data=f"pl|{ph}|cancel")])
    return InlineKeyboardMarkup(btns)

# ══════════════════════════════════════════
#              UPLOAD
# ══════════════════════════════════════════
async def upload(bot,chat_id,path,info,dtype,smsg,extra=""):
    sz=os.path.getsize(path)
    if sz>2*1024**3: await smsg.edit_text("❌ File >2GB!"); return False
    cap=(f"🎬 **{(info.get('title')or'Media')[:50]}**\n"
         f"👤 `{info.get('uploader')or info.get('author','')}`\n"
         f"⏱️ `{hms(info.get('duration',0))}`\n📦 `{humanbytes(sz)}`"
         +(f"\n{extra}" if extra else ""))
    thumb=None; tu=info.get("thumbnail")
    if tu:
        try:
            rr=requests.get(tu,timeout=10,proxies=PROXIES)
            img=Image.open(BytesIO(rr.content)).convert("RGB"); img.thumbnail((320,320))
            buf=BytesIO(); img.save(buf,"JPEG"); buf.seek(0); thumb=buf
        except: pass
    try:
        if dtype=="v":
            with open(path,"rb") as f:
                await bot.send_video(chat_id=chat_id,video=f,caption=cap,parse_mode=ParseMode.MARKDOWN,
                    duration=info.get("duration",0),width=info.get("width",1280),height=info.get("height",720),
                    thumb=thumb,supports_streaming=True,write_timeout=600,read_timeout=600)
        else:
            with open(path,"rb") as f:
                await bot.send_audio(chat_id=chat_id,audio=f,caption=cap,parse_mode=ParseMode.MARKDOWN,
                    duration=info.get("duration",0),title=(info.get("title")or"Audio")[:64],
                    performer=info.get("uploader")or info.get("author",""),write_timeout=600,read_timeout=600)
        return True
    except Exception as e: logger.error(f"Upload: {e}"); return False

# ══════════════════════════════════════════
#              DOWNLOAD
# ══════════════════════════════════════════
async def do_dl(url,fid,dtype,info,smsg,ctx,chat_id,uh):
    loop=asyncio.get_event_loop(); trk=Trk(smsg,loop)
    otempl=os.path.join(DOWNLOAD_DIR,f"{uh}_{safe_name(info.get('title',''))}.%(ext)s")
    if dtype=="v": opts={**ydl_opts(),"format":f"{fid}+bestaudio/best","outtmpl":otempl,"merge_output_format":"mp4","progress_hooks":[trk.hook]}
    else:          opts={**ydl_opts(),"format":fid,"outtmpl":otempl,"progress_hooks":[trk.hook],"postprocessors":[{"key":"FFmpegExtractAudio","preferredcodec":"mp3","preferredquality":"320"}]}
    await loop.run_in_executor(None,lambda: yt_dlp.YoutubeDL(opts).download([url]))
    path=next((os.path.join(DOWNLOAD_DIR,f) for f in os.listdir(DOWNLOAD_DIR) if f.startswith(uh)),None)
    if not path: await smsg.edit_text("❌ File nahi mili."); return
    await smsg.edit_text(f"📤 **Upload ho raha hai...**\n📦 `{humanbytes(os.path.getsize(path))}`",parse_mode=ParseMode.MARKDOWN)
    ok=await upload(ctx.bot,chat_id,path,info,dtype,smsg)
    try: os.remove(path)
    except: pass
    if ok:
        try: await smsg.delete()
        except: pass

async def do_playlist(ph,indices,dtype,height,smsg,ctx,chat_id):
    e=playlists.get(ph)
    if not e: await smsg.edit_text("⚠️ Session expire."); return
    sel=[e["entries"][i] for i in indices if i<len(e["entries"])]
    total=len(sel); loop=asyncio.get_event_loop(); failed=[]
    for idx,en in enumerate(sel,1):
        vurl=en.get("url")or en.get("webpage_url")
        vtitle=(en.get("title")or f"Video {idx}")[:50]
        pre=f"📋 **{idx}/{total}**\n📹 `{vtitle}`\n\n"
        await smsg.edit_text(f"{pre}🔍 Info...",parse_mode=ParseMode.MARKDOWN)
        vi=await loop.run_in_executor(None,ydl_info,vurl)
        if not vi: failed.append(vtitle); continue
        cfid=None
        if dtype=="v":
            for f in sorted(vi.get("formats",[]),key=lambda x:x.get("height",0)or 0,reverse=True):
                if f.get("vcodec","none")!="none" and (f.get("height",0)or 0)<=int(height or 9999):
                    cfid=f["format_id"]; break
            cfid=cfid or "bestvideo+bestaudio/best"
        else: cfid="bestaudio/best"
        vh=f"{ph}_{idx}"; otempl=os.path.join(DOWNLOAD_DIR,f"{vh}_{safe_name(vtitle)}.%(ext)s")
        trk=Trk(smsg,loop,pre)
        if dtype=="v": opts={**ydl_opts(),"format":f"{cfid}+bestaudio/best","outtmpl":otempl,"merge_output_format":"mp4","progress_hooks":[trk.hook]}
        else:          opts={**ydl_opts(),"format":cfid,"outtmpl":otempl,"progress_hooks":[trk.hook],"postprocessors":[{"key":"FFmpegExtractAudio","preferredcodec":"mp3","preferredquality":"320"}]}
        try: await loop.run_in_executor(None,lambda: yt_dlp.YoutubeDL(opts).download([vurl]))
        except Exception as ex: logger.error(f"PL dl: {ex}"); failed.append(vtitle); continue
        path=next((os.path.join(DOWNLOAD_DIR,f) for f in os.listdir(DOWNLOAD_DIR) if f.startswith(vh)),None)
        if not path: failed.append(vtitle); continue
        await smsg.edit_text(f"{pre}📤 `{humanbytes(os.path.getsize(path))}`...",parse_mode=ParseMode.MARKDOWN)
        ok=await upload(ctx.bot,chat_id,path,vi,dtype,smsg,extra=f"📋 `{idx}/{total}`")
        try: os.remove(path)
        except: pass
        if not ok: failed.append(vtitle)
    if failed: await smsg.edit_text(f"✅ `{total-len(failed)}/{total}` uploaded.\n❌ Failed:\n"+"\n".join(f"• `{t}`" for t in failed[:10]),parse_mode=ParseMode.MARKDOWN)
    else:       await smsg.edit_text(f"✅ **Playlist complete!** Sabhi `{total}` videos! 🎉",parse_mode=ParseMode.MARKDOWN)
    playlists.pop(ph,None)

# ══════════════════════════════════════════
#              PLATFORM ROUTER
# ══════════════════════════════════════════
async def route(url,platform,update,ctx,smsg):
    chat_id=update.effective_chat.id; loop=asyncio.get_event_loop()

    # YOUTUBE
    if platform=="youtube":
        if "list=" in url and "playlist" in url:
            await _pl(url,update,ctx,smsg); return
        await smsg.edit_text("🔍 **TeamDev API...**",parse_mode=ParseMode.MARKDOWN)
        res=await loop.run_in_executor(None,api_youtube,url)
        if res and res.get("download_url"):
            cap=(f"🎬 **{res['title'][:55]}**\n⏱️ `{hms(res['duration'])}`\n"
                 f"📦 `{res['filesize_mb']:.1f} MB`\n🎞️ `{res['quality']}`\n\n_Powered by TeamDev API_")
            kb=InlineKeyboardMarkup([[InlineKeyboardButton("⬇️ Download",url=res["download_url"])]])
            await smsg.delete()
            try:
                if res.get("thumbnail"): await update.message.reply_photo(photo=res["thumbnail"],caption=cap,parse_mode=ParseMode.MARKDOWN,reply_markup=kb)
                else: raise Exception()
            except: await update.message.reply_text(cap,parse_mode=ParseMode.MARKDOWN,reply_markup=kb)
            return
        # fallback yt-dlp
        await smsg.edit_text("⚙️ **yt-dlp quality picker...**",parse_mode=ParseMode.MARKDOWN)
        info=await loop.run_in_executor(None,ydl_info,url)
        if not info: await smsg.edit_text("❌ Video info nahi mili.\n• Private video?\n• cookies.txt check karo"); return
        await _qpick(url,info,update,ctx,smsg)

    # TERABOX
    elif platform=="terabox":
        await smsg.edit_text("📦 **Terabox API...**",parse_mode=ParseMode.MARKDOWN)
        res=await loop.run_in_executor(None,api_terabox,url)
        if not res or not res.get("download_url"): await smsg.edit_text("❌ Terabox link kaam nahi kiya."); return
        cap=(f"📦 **{res['title'][:55]}**\n📁 `{res.get('format','')}`\n"
             f"📦 `{res.get('filesize_str') or str(res.get('filesize_mb',''))+' MB'}`\n\n_Powered by TeamDev API_")
        kb=InlineKeyboardMarkup([[InlineKeyboardButton("⬇️ Download",url=res["download_url"])]])
        await smsg.delete()
        try:
            if res.get("thumbnail"): await update.message.reply_photo(photo=res["thumbnail"],caption=cap,parse_mode=ParseMode.MARKDOWN,reply_markup=kb)
            else: raise Exception()
        except: await update.message.reply_text(cap,parse_mode=ParseMode.MARKDOWN,reply_markup=kb)

    # SPOTIFY
    elif platform=="spotify":
        if "/album/" in url or "/playlist/" in url:
            await smsg.edit_text("❌ Sirf **tracks** support hain."); return
        await smsg.edit_text("🎧 **Spotify track dhundh raha hoon...**",parse_mode=ParseMode.MARKDOWN)
        res=await loop.run_in_executor(None,api_spotify,url)
        if not res or not res.get("download_url"):
            await smsg.edit_text("❌ Spotify nahi chala.\n`pip install playwright && playwright install chromium`"); return
        await smsg.edit_text("⬇️ **MP3 download ho raha hai...**",parse_mode=ParseMode.MARKDOWN)
        fpath=os.path.join(DOWNLOAD_DIR,f"sp_{safe_name(res['title'])}.mp3")
        try:
            r=requests.get(res["download_url"],stream=True,timeout=60,proxies=PROXIES); r.raise_for_status()
            with open(fpath,"wb") as f:
                for chunk in r.iter_content(4096):
                    if chunk: f.write(chunk)
        except Exception as ex: await smsg.edit_text(f"❌ Download failed: {ex}"); return
        await smsg.edit_text("📤 **Upload ho raha hai...**",parse_mode=ParseMode.MARKDOWN)
        cap=f"🎧 **{res['title'][:55]}**\n👤 `{res.get('author','')}`\n🎵 `MP3 · 320kbps`"
        with open(fpath,"rb") as f:
            await ctx.bot.send_audio(chat_id=chat_id,audio=f,caption=cap,parse_mode=ParseMode.MARKDOWN,
                title=res["title"][:64],performer=res.get("author",""),write_timeout=300,read_timeout=300)
        try: os.remove(fpath); await smsg.delete()
        except: pass

    # INSTAGRAM/TIKTOK/TWITTER/FACEBOOK etc.
    else:
        label=PLAT_LABEL.get(platform,platform.title())
        await smsg.edit_text(f"🔍 **{label}...**",parse_mode=ParseMode.MARKDOWN)
        res=await loop.run_in_executor(None,api_multi,url)
        if not res or not res.get("download_url"):
            # yt-dlp fallback
            await smsg.edit_text("⚙️ **yt-dlp fallback...**",parse_mode=ParseMode.MARKDOWN)
            info=await loop.run_in_executor(None,ydl_info,url)
            if not info: await smsg.edit_text(f"❌ {label} se media nahi mila."); return
            await _qpick(url,info,update,ctx,smsg); return
        all_m=res.get("all_medias",[])
        cap=(f"{PLAT_LABEL.get(platform,'📥')} **{(res.get('title')or res.get('author')or'Media')[:50]}**\n"
             +(f"👤 `{res['author']}`\n" if res.get("author") else "")
             +(f"🎞️ `{res['quality']}`\n" if res.get("quality") else ""))
        if len(all_m)>1:
            uh=str(abs(hash(url)))[:10]; pending[uh]={"url":url,"info":res,"medias":all_m,"user_id":update.effective_user.id}
            btns=[]
            for i,m in enumerate(all_m[:8]):
                q=m.get("quality","")or m.get("type",""); t=m.get("type","")or""
                em="📹" if "video" in t else "🖼️" if "image" in t else "🎵"
                btns.append([InlineKeyboardButton(f"{em} {q}".strip(),callback_data=f"med|{uh}|{i}")])
            btns.append([InlineKeyboardButton("⬇️ Best Quality",url=res["download_url"])])
            kb=InlineKeyboardMarkup(btns)
        else: kb=InlineKeyboardMarkup([[InlineKeyboardButton("⬇️ Download",url=res["download_url"])]])
        await smsg.delete()
        try: await update.message.reply_text(cap,parse_mode=ParseMode.MARKDOWN,reply_markup=kb)
        except: await update.message.reply_text("Media ready!",reply_markup=kb)

async def _qpick(url,info,update,ctx,smsg):
    fmts=parse_fmts(info); uh=str(abs(hash(url)))[:10]
    pending[uh]={"url":url,"info":info,"user_id":update.effective_user.id}
    title=(info.get("title",""))[:60]; upl=info.get("uploader",""); dur=hms(info.get("duration",0))
    views=info.get("view_count",0)or 0; likes=info.get("like_count",0)or 0
    udate=info.get("upload_date","")
    if udate:
        from datetime import datetime
        try: udate=datetime.strptime(udate,"%Y%m%d").strftime("%d %b %Y")
        except: pass
    cap=(f"🎬 **{title}**\n\n👤 `{upl}`\n⏱️ `{dur}`  📅 `{udate}`\n"
         f"👁️ `{views:,}`  ❤️ `{likes:,}`\n\n**Quality choose karo:**")
    kb=kb_q(fmts,uh); th=info.get("thumbnail")
    await smsg.delete()
    try:
        if th: await update.message.reply_photo(photo=th,caption=cap,parse_mode=ParseMode.MARKDOWN,reply_markup=kb)
        else: raise Exception()
    except: await update.message.reply_text(cap,parse_mode=ParseMode.MARKDOWN,reply_markup=kb)

async def _pl(url,update,ctx,smsg):
    loop=asyncio.get_event_loop()
    info=await loop.run_in_executor(None,
        lambda: yt_dlp.YoutubeDL({**ydl_opts(),"extract_flat":True,"playlistend":MAX_PLAYLIST}).extract_info(url,download=False))
    if not info or info.get("_type")!="playlist":
        info2=await loop.run_in_executor(None,ydl_info,url)
        if info2: await _qpick(url,info2,update,ctx,smsg)
        else: await smsg.edit_text("❌ Playlist info nahi mili.")
        return
    entries=[e for e in (info.get("entries")or[]) if e][:MAX_PLAYLIST]
    ph=str(abs(hash(url+str(time.time()))))[:10]; ts=sum(e.get("duration",0)or 0 for e in entries)
    playlists[ph]={"url":url,"entries":entries,"user_id":update.effective_user.id}
    vl=""
    for i,e in enumerate(entries[:10],1):
        vl+=f"`{i:02d}.` {(e.get('title')or f'Video {i}')[:35]} `[{hms(e.get('duration',0))}]`\n"
    if len(entries)>10: vl+=f"_...aur {len(entries)-10} videos_"
    cap=(f"📋 **Playlist Mili!**\n\n📌 **{(info.get('title','Playlist'))[:60]}**\n"
         f"👤 `{info.get('uploader')or info.get('channel','')}`\n"
         f"🎬 `{len(entries)}` videos  ⏱️ `{hms(ts)}`\n\n{vl}\n**Kya download karna hai?**")
    await smsg.delete()
    try:
        th=(info.get("thumbnails")or[{}])[-1].get("url","")
        if th: await update.message.reply_photo(photo=th,caption=cap,parse_mode=ParseMode.MARKDOWN,reply_markup=kb_pl(ph,len(entries)))
        else: raise Exception()
    except: await update.message.reply_text(cap,parse_mode=ParseMode.MARKDOWN,reply_markup=kb_pl(ph,len(entries)))

# ══════════════════════════════════════════
#              BOT HANDLERS
# ══════════════════════════════════════════
async def cmd_start(update:Update,context:ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎬 **All-In-One Downloader Bot**\n\n"
        "Koi bhi link bhejo!\n\n"
        "▶️ YouTube & Playlists\n📸 Instagram\n🎵 TikTok\n𝕏 Twitter/X\n"
        "📘 Facebook\n📦 Terabox\n🎬 Vimeo\n🎧 Spotify\n☁️ SoundCloud\n📌 Pinterest\n\n"
        "/ping — Bot check",parse_mode=ParseMode.MARKDOWN)

async def cmd_ping(update:Update,context:ContextTypes.DEFAULT_TYPE):
    t=time.time(); m=await update.message.reply_text("🏓 Pong!")
    await m.edit_text(f"🏓 Pong! `{(time.time()-t)*1000:.0f}ms`",parse_mode=ParseMode.MARKDOWN)

async def handle_msg(update:Update,context:ContextTypes.DEFAULT_TYPE):
    user=update.effective_user; text=(update.message.text or"").strip()
    if not is_auth(user.id): await update.message.reply_text("⛔ Authorized nahi ho."); return
    if user.id in waiting:
        ph=waiting[user.id]; e=playlists.get(ph)
        if e:
            total=len(e["entries"]); idx=_psel(text,total)
            if idx is None:
                await update.message.reply_text(f"❌ Invalid!\nFormat: `1,3,5` ya `1-10`\nTotal: `{total}`",parse_mode=ParseMode.MARKDOWN); return
            e["sel"]=idx; waiting.pop(user.id,None)
            sm=await update.message.reply_text(f"✅ `{len(idx)}` select kiye. Quality le raha hoon...",parse_mode=ParseMode.MARKDOWN)
            loop=asyncio.get_event_loop()
            fu=e["entries"][idx[0]].get("url")or e["entries"][idx[0]].get("webpage_url")
            fi=await loop.run_in_executor(None,ydl_info,fu)
            if not fi: await sm.edit_text("❌ Quality fetch fail."); return
            fmts=parse_fmts(fi)
            await sm.edit_text(f"🎨 **Quality choose karo**\n\n📋 `{len(idx)}` videos pe apply:",
                parse_mode=ParseMode.MARKDOWN,reply_markup=kb_plq(ph,fmts,"sel")); return
    url=_url(text)
    if not url: await update.message.reply_text("❌ Valid URL nahi mila."); return
    plat=detect(url)
    if not plat:
        await update.message.reply_text("❌ Platform support nahi hota.\n\nYouTube, Instagram, TikTok, Twitter, Facebook, Terabox, Vimeo, Spotify, SoundCloud bhejo."); return
    sm=await update.message.reply_text(f"🔍 **{PLAT_LABEL.get(plat,plat)} — info le raha hoon...**",parse_mode=ParseMode.MARKDOWN)
    try: await route(url,plat,update,context,sm)
    except Exception as ex:
        logger.error(f"route error: {ex}")
        try: await sm.edit_text(f"❌ Error: `{str(ex)[:200]}`",parse_mode=ParseMode.MARKDOWN)
        except: pass

async def handle_cb(update:Update,context:ContextTypes.DEFAULT_TYPE):
    q=update.callback_query; await q.answer(); d=q.data.split("|")
    if d[0]=="cancel": pending.pop(d[1],None); await q.message.delete(); return
    if d[0]=="med":
        _,uh,idx=d; e=pending.get(uh)
        if not e: await q.message.reply_text("⚠️ Session expire."); return
        dl=e["medias"][int(idx)].get("url","")
        await q.message.edit_reply_markup(reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬇️ Download",url=dl)]])); return
    if d[0]=="dl":
        _,uh,dtype,fid=d; e=pending.get(uh)
        if not e: await q.message.reply_text("⚠️ Session expire. Link dobara bhejo."); return
        if q.from_user.id!=e["user_id"]: await q.answer("❌ Tera nahi!",show_alert=True); return
        url=e["url"]; info=e["info"]
        if dtype=="thumb":
            tu=info.get("thumbnail")
            if not tu: await q.message.reply_text("❌ Thumbnail nahi."); return
            sm=await q.message.reply_text("🖼️ Thumbnail le raha hoon...")
            try:
                rr=requests.get(tu,timeout=15,proxies=PROXIES)
                img=Image.open(BytesIO(rr.content)); buf=BytesIO(); img.save(buf,"JPEG"); buf.seek(0)
                await q.message.reply_document(document=buf,filename=f"{safe_name(info.get('title','thumb'))}_thumb.jpg",
                    caption=f"🖼️ **{(info.get('title','Thumbnail'))[:50]}**",parse_mode=ParseMode.MARKDOWN)
                await sm.delete()
            except Exception as ex: await sm.edit_text(f"❌ {ex}")
            return
        sm=await q.message.reply_text("⚙️ **Preparing...**",parse_mode=ParseMode.MARKDOWN)
        try: await do_dl(url,fid,dtype,info,sm,context,q.message.chat_id,uh)
        except Exception as ex:
            logger.error(f"DL: {ex}")
            try: await sm.edit_text(f"❌ **Failed!**\n`{str(ex)[:300]}`",parse_mode=ParseMode.MARKDOWN)
            except: pass
        finally:
            for f in os.listdir(DOWNLOAD_DIR):
                if f.startswith(uh):
                    try: os.remove(os.path.join(DOWNLOAD_DIR,f))
                    except: pass
            pending.pop(uh,None)
        return
    if d[0]=="pl":
        _,ph,sub=d; e=playlists.get(ph)
        if sub=="cancel": playlists.pop(ph,None); waiting.pop(q.from_user.id,None); await q.message.delete(); return
        if not e: await q.message.reply_text("⚠️ Session expire."); return
        if q.from_user.id!=e["user_id"]: await q.answer("❌ Tera nahi!",show_alert=True); return
        if sub=="thumbs":
            sm=await q.message.reply_text("🖼️ Saare thumbnails...",parse_mode=ParseMode.MARKDOWN); sent=0
            for i,en in enumerate(e["entries"],1):
                tu=en.get("thumbnail")or(en.get("thumbnails")or[{}])[-1].get("url","")
                if not tu: continue
                try:
                    rr=requests.get(tu,timeout=10,proxies=PROXIES)
                    img=Image.open(BytesIO(rr.content)); buf=BytesIO(); img.save(buf,"JPEG"); buf.seek(0)
                    await context.bot.send_document(chat_id=q.message.chat_id,document=buf,
                        filename=f"{i:02d}_{safe_name(en.get('title',''))}.jpg",
                        caption=f"🖼️ `{i}.` {(en.get('title')or'')[:40]}",parse_mode=ParseMode.MARKDOWN)
                    sent+=1; await asyncio.sleep(0.5)
                except: pass
                if i%5==0: await sm.edit_text(f"🖼️ `{i}/{len(e['entries'])}`...")
            await sm.edit_text(f"✅ `{sent}` thumbnails bheje!"); return
        if sub=="select":
            waiting[q.from_user.id]=ph; total=len(e["entries"]); vl=""
            for i,en in enumerate(e["entries"][:20],1):
                vl+=f"`{i:02d}.` {(en.get('title')or f'Video {i}')[:35]} `[{hms(en.get('duration',0))}]`\n"
            if total>20: vl+=f"_...{total-20} aur_"
            await q.message.reply_text(f"🎯 **Select karo:**\n\nTotal: `{total}`\n\n{vl}\n\nFormat: `1,3,5` ya `1-10` ya `1-3,7,10-12`",parse_mode=ParseMode.MARKDOWN); return
        if sub=="all":
            sm=await q.message.reply_text("🔍 Quality options...",parse_mode=ParseMode.MARKDOWN)
            loop=asyncio.get_event_loop()
            fu=e["entries"][0].get("url")or e["entries"][0].get("webpage_url")
            fi=await loop.run_in_executor(None,ydl_info,fu)
            if not fi: await sm.edit_text("❌ Quality fetch fail."); return
            fmts=parse_fmts(fi)
            await sm.edit_text(f"🎨 **Quality choose karo**\n\n📋 Sabhi `{len(e['entries'])}` videos:",
                parse_mode=ParseMode.MARKDOWN,reply_markup=kb_plq(ph,fmts,"all")); return
    if d[0]=="plq":
        _,ph,scope,dtype,fid,height=d; e=playlists.get(ph)
        if not e: await q.message.reply_text("⚠️ Session expire."); return
        if q.from_user.id!=e["user_id"]: await q.answer("❌ Tera nahi!",show_alert=True); return
        indices=list(range(len(e["entries"]))) if scope=="all" else e.get("sel",[])
        if not indices: await q.message.reply_text("⚠️ Koi selection nahi."); return
        sm=await q.message.reply_text(f"🚀 **Playlist download shuru!**\n📋 `{len(indices)}` videos | `{'Audio' if dtype=='a' else height+'p'}`",parse_mode=ParseMode.MARKDOWN)
        asyncio.create_task(do_playlist(ph,indices,dtype,height,sm,context,q.message.chat_id))

def _psel(text,total):
    idx=set()
    try:
        for p in text.strip().split(","):
            p=p.strip()
            if "-" in p:
                a,b=map(int,p.split("-",1))
                if a<1 or b>total or a>b: return None
                idx.update(range(a-1,b))
            else:
                n=int(p)
                if n<1 or n>total: return None
                idx.add(n-1)
        return sorted(idx)
    except: return None

# ══════════════════════════════════════════
#       HEALTH CHECK SERVER (Koyeb)
# ══════════════════════════════════════════
from aiohttp import web

BOT_START_TIME = time.time()

async def _health(request):
    uptime = int(time.time() - BOT_START_TIME)
    h, r   = divmod(uptime, 3600)
    m, s   = divmod(r, 60)
    return web.Response(
        content_type="application/json",
        text=f'{{"status":"ok","bot":"running","uptime":"{h:02d}:{m:02d}:{s:02d}","platform":"koyeb"}}',
    )

async def start_health():
    port = int(os.environ.get("PORT", 8000))
    app_web = web.Application()
    app_web.router.add_get("/",       _health)
    app_web.router.add_get("/health", _health)
    runner = web.AppRunner(app_web)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", port).start()
    logger.info(f"✅ Health check server on port {port}")

# ══════════════════════════════════════════
#              MAIN
# ══════════════════════════════════════════
async def run():
    logger.info(f"Cookies: {'✅' if os.path.exists(COOKIES_FILE) else '❌ Not found'}")
    logger.info(f"Proxy:   {'✅ Set' if PROXY_URL else '❌ None'}")
    logger.info(f"YT API:  {YT_API_BASE}")

    # Start health check first (Koyeb needs it)
    await start_health()

    builder=Application.builder().token(BOT_TOKEN)
    if PROXY_URL: builder=builder.request(HTTPXRequest(proxy=PROXY_URL))
    app=(builder.read_timeout(60).write_timeout(600).connect_timeout(30).pool_timeout(30).build())
    app.add_handler(CommandHandler("start",cmd_start))
    app.add_handler(CommandHandler("help",cmd_start))
    app.add_handler(CommandHandler("ping",cmd_ping))
    app.add_handler(MessageHandler(filters.TEXT&~filters.COMMAND,handle_msg))
    app.add_handler(CallbackQueryHandler(handle_cb))
    await app.initialize(); await app.start()
    await app.updater.start_polling(drop_pending_updates=True)
    logger.info("✅ Bot ready!")
    try: await asyncio.Event().wait()
    finally:
        await app.updater.stop(); await app.stop(); await app.shutdown()

if __name__=="__main__":
    asyncio.run(run())