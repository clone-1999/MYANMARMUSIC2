# youtube.py - YouTube Download & Search Handler (Fast API-First & Verified Cookie Fallback)

import os
import re
import glob
import time
import yt_dlp
import random
import asyncio
import aiohttp
import requests  # Async loop ပြဿနာ ကင်းဝေးစေရန် သုံးထားသည်
from dataclasses import replace
from pathlib import Path
from typing import Optional, Union

from pyrogram import enums, types
from py_yt import Playlist, VideosSearch
from Elevenyts import config, logger
from Elevenyts.helpers import Track, utils


class YouTube:
    def __init__(self):
        """Initialize YouTube handler with configuration and caching."""
        self.base = "https://www.youtube.com/watch?v="
        self.cookies = []
        self.checked = False
        self.warned = False

        # --- ပြင်ဆင်သတ်မှတ်ထားသော API နှင့် COOKIE URL များ ---
        self.api_url = "https://artistbots.onrender.com"
        self.api_key = "Artistbots3eueiX3jMWzy1ZLdYIqDWg"
        # ✨ ပိုမိုစိတ်ချရသော Netscape Format စစ်စစ် Cookie Link ကို ပြောင်းလဲပေးထားပါသည်
        self.cookie_url = "https://gist.githubusercontent.com/Aki-Ikeda/d6878b17bbfeb465f24f5a31b402ea10/raw/cookie.txt"
        
        self.enable_api_fallback = True
        self.api_timeout = getattr(config, "API_TIMEOUT", 30)  # တုံ့ပြန်မှု မြန်ဆန်စေရန် 30s သို့ လျှော့ချထားသည်
        self.api_stream_timeout = getattr(config, "API_STREAM_TIMEOUT", 120)
        # --------------------------------------------------

        # Regular expression to match YouTube URLs
        self.regex = re.compile(
            r"(https?://)?(www\.|m\.|music\.)?"
            r"(youtube\.com/(watch\?v=|shorts/|live/|embed/|playlist\?list=)|youtu\.be/)"
            r"([A-Za-z0-9_-]{11}|PL[A-Za-z0-9_-]+)([&?][^\s]*)?"
        )

        # Cache search results (10 minute TTL)
        self.search_cache = {}
        self._download_semaphore = asyncio.Semaphore(5)
        self._max_video_height = getattr(config, "VIDEO_MAX_HEIGHT", 720)

        logger.info(f"⚡ YouTube API First Mode Enabled: {self.api_url}")
        
        # Async Loop Crash ကာကွယ်ရန် သမရိုးကျ Sync စနစ်ဖြင့် Bot တက်ချိန် Cookie အမြန်သိမ်းမည်
        self.sync_save_cookies([self.cookie_url])

    def _locate_download_file(self, video_id: str, video: bool = False) -> Optional[str]:
        """Locate any completed download file for a video id."""
        pattern = f"downloads/{video_id}*"
        candidates = sorted([
            path for path in glob.glob(pattern)
            if not path.endswith((".part", ".ytdl", ".info.json", ".temp"))
        ])

        video_exts = {".mp4", ".mkv", ".webm", ".mov"}
        audio_exts = {".m4a", ".webm", ".opus", ".mp3", ".ogg", ".wav", ".flac"}

        if video:
            for path in candidates:
                if os.path.isdir(path):
                    continue
                if Path(path).suffix.lower() in video_exts:
                    return path
        else:
            for path in candidates:
                if os.path.isdir(path):
                    continue
                if Path(path).suffix.lower() in audio_exts:
                    return path

        for path in candidates:
            if os.path.isdir(path):
                continue
            return path
        return None

    async def get_cookies_async(self):
        """Asynchronously get cookie file from cookies directory."""
        if not self.checked:
            cookies_dir = "Elevenyts/cookies"
            if os.path.exists(cookies_dir):
                for file in os.listdir(cookies_dir):
                    if file.endswith(".txt"):
                        if file not in self.cookies:
                            self.cookies.append(file)
            self.checked = True
        
        if not self.cookies:
            return None
        
        cookie_file = f"Elevenyts/cookies/{random.choice(self.cookies)}"
        return cookie_file

    def sync_save_cookies(self, urls: list[str]) -> None:
        """Crash ကာကွယ်ရန်နှင့် ဒေါင်းလုဒ်မြန်စေရန် Cookie ဟောင်းများကို ရှင်းထုတ်ပြီး အသစ်သိမ်းဆည်းခြင်း"""
        cookies_dir = Path("Elevenyts/cookies")
        cookies_dir.mkdir(parents=True, exist_ok=True)
        
        # ဖွင့်ရနှေးစေသော Cookie အဟောင်းအပျက်များရှိပါက ဖျက်ထုတ်ပစ်မည်
        try:
            for f in os.listdir(cookies_dir):
                if f.endswith(".txt"):
                    os.remove(cookies_dir / f)
            self.cookies = []
        except Exception:
            pass
        
        for url in urls:
            try:
                path = cookies_dir / f"cookie{random.randint(10000, 99999)}.txt"
                link = url.replace("pastebin.com", "pastebin.com/raw") if "pastebin.com" in url else url
                link = link.replace("batbin.me", "batbin.me/raw") if "batbin.me" in url else link
                
                response = requests.get(link, timeout=10)
                if response.status_code == 200:
                    content = response.content
                    if content and len(content) > 50:
                        with open(path, "wb") as fw:
                            fw.write(content)
                        cookie_filename = path.name
                        if cookie_filename not in self.cookies:
                            self.cookies.append(cookie_filename)
            except Exception:
                pass
        self.checked = True

    async def download_via_api(self, link: str, video: bool = False) -> Optional[str]:
        """Download audio/video directly using ArtistBots API (Fast Track with Validation)."""
        if "v=" in link:
            video_id = link.split("v=")[-1].split("&")[0]
        elif "youtu.be" in link:
            video_id = link.split("/")[-1].split("?")[0]
        else:
            video_id = link

        if not video_id or len(video_id) < 3:
            return None

        DOWNLOAD_DIR = "downloads"
        os.makedirs(DOWNLOAD_DIR, exist_ok=True)
        
        file_ext = ".mp4" if video else ".mp3"
        file_path = os.path.join(DOWNLOAD_DIR, f"{video_id}{file_ext}")

        # ဖိုင်ရှိပြီးသားဖြစ်ပြီး ဖိုင်အစစ် (50KB အထက်) ဖြစ်ပါက ချက်ချင်း ဖွင့်မည်
        if os.path.exists(file_path):
            if os.path.getsize(file_path) > 50000:
                return file_path
            else:
                try:
                    os.remove(file_path)
                except Exception:
                    pass

        endpoint = "/vdown" if video else "/download"
        
        try:
            logger.info(f"🚀 [API FIRST] Tapping ArtistBots API for {video_id}...")
            
            async with aiohttp.ClientSession() as session:
                params = {
                    "url": f"https://youtu.be/{video_id}",
                    "api_key": self.api_key
                }
                headers = {
                    "Authorization": f"Bearer {self.api_key}"
                }
                
                async with session.get(
                    f"{self.api_url}{endpoint}",
                    params=params,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=self.api_timeout),
                ) as response:
                    if response.status != 200:
                        logger.debug(f"⚠️ API returned status {response.status}")
                        return None
                    
                    content_type = response.headers.get('content-type', '')
                    
                    if 'application/json' in content_type:
                        data = await response.json()
                        stream_url = data.get('stream_url') or data.get('url') or data.get('data', {}).get('url')
                        
                        if not stream_url:
                            return None
                        
                        async with session.get(stream_url, timeout=aiohttp.ClientTimeout(total=self.api_stream_timeout)) as file_response:
                            if file_response.status == 200:
                                with open(file_path, "wb") as f:
                                    async for chunk in file_response.content.iter_chunked(16384):
                                        f.write(chunk)
                    else:
                        with open(file_path, "wb") as f:
                            async for chunk in response.content.iter_chunked(16384):
                                f.write(chunk)

            # ရရှိလာသောဖိုင်သည် စာသားအမှားမဟုတ်ဘဲ တကယ့် သီချင်းဖိုင်စစ်စစ် ဖြစ်ကြောင်း စစ်ဆေးခြင်း
            if os.path.exists(file_path) and os.path.getsize(file_path) > 50000:
                logger.info(f"✅ API Download Success & Verified: {file_path}")
                return file_path
            else:
                if os.path.exists(file_path):
                    try:
                        os.remove(file_path)
                    except Exception:
                        pass
                logger.warning(f"⚠️ API returned an invalid or empty file for {video_id}.")
                return None

        except Exception as e:
            if os.path.exists(file_path):
                try:
                    os.remove(file_path)
                except Exception:
                    pass
            logger.debug(f"❌ ArtistBots API Download failed: {e}")
            return None

    def valid(self, url: str) -> bool:
        """Check if URL is a valid YouTube URL."""
        return bool(re.match(self.regex, url))

    def url(self, message_1: types.Message) -> Union[str, None]:
        """Extract YouTube URL from message."""
        messages = [message_1]
        link = None
        if message_1.reply_to_message:
            messages.append(message_1.reply_to_message)

        for message in messages:
            text = message.text or message.caption or ""
            if message.entities:
                for entity in message.entities:
                    if entity.type == enums.MessageEntityType.URL:
                        link = text[entity.offset: entity.offset + entity.length]
                        break
            if message.caption_entities:
                for entity in message.caption_entities:
                    if entity.type == enums.MessageEntityType.TEXT_LINK:
                        link = entity.url
                        break

        if link:
            return link.split("&si")[0].split("?si")[0]
        return None

    async def search(self, query: str, m_id: int) -> Track | None:
        """Search for a song on YouTube."""
        cache_key = query
        current_time = asyncio.get_running_loop().time()

        if cache_key in self.search_cache:
            cached_result, cache_timestamp = self.search_cache[cache_key]
            if current_time - cache_timestamp < 600:
                fresh = replace(cached_result)
                fresh.message_id = m_id
                fresh.file_path = None
                fresh.user = None
                fresh.time = 0
                fresh.video = False
                return fresh

        try:
            _search = VideosSearch(query, limit=1)
            results = await _search.next()
        except Exception as e:
            logger.warning(f"⚠️ YouTube search failed for '{query}': {e}")
            return None

        if results and results["result"]:
            data = results["result"][0]
            duration = data.get("duration")
            is_live = duration is None or duration == "LIVE"

            track = Track(
                id=data.get("id"),
                channel_name=data.get("channel", {}).get("name"),
                duration=duration if not is_live else "LIVE",
                duration_sec=0 if is_live else utils.to_seconds(duration),
                message_id=m_id,
                title=data.get("title")[:25],
                thumbnail=data.get("thumbnails", [{}])[-1].get("url").split("?")[0],
                url=data.get("link"),
                view_count=data.get("viewCount", {}).get("short"),
                is_live=is_live,
            )

            self.search_cache[cache_key] = (track, current_time)
            
            if len(self.search_cache) > 100:
                oldest_key = min(self.search_cache.keys(), key=lambda k: self.search_cache[k][1])
                del self.search_cache[oldest_key]

            return replace(track)
        return None

    async def playlist(self, limit: int, user: str, url: str) -> list[Track]:
        """Extract tracks from a YouTube playlist."""
        try:
            plist = await Playlist.get(url)
            tracks = []

            if not plist or "videos" not in plist or not plist["videos"]:
                return []

            for data in plist["videos"][:limit]:
                try:
                    thumbnails = data.get("thumbnails", [])
                    thumbnail_url = ""
                    if thumbnails and len(thumbnails) > 0:
                        thumbnail_url = thumbnails[-1].get("url", "").split("?")[0]

                    link = data.get("link", "")
                    if "&list=" in link:
                        link = link.split("&list=")[0]

                    track = Track(
                        id=data.get("id", ""),
                        channel_name=data.get("channel", {}).get("name", ""),
                        duration=data.get("duration", "0:00"),
                        duration_sec=utils.to_seconds(data.get("duration", "0:00")),
                        title=(data.get("title", "Unknown")[:25]),
                        thumbnail=thumbnail_url,
                        url=link,
                        user=user,
                        view_count="",
                    )
                    tracks.append(track)
                except Exception as e:
                    logger.warning(f"Failed to parse playlist item: {e}")
                    continue

            return tracks
        except Exception as e:
            logger.error(f"Playlist extraction error: {e}")
            raise

    async def download(self, video_id: str, is_live: bool = False, video: bool = False) -> Optional[str]:
        """Download audio/video prioritizing the API for MAXIMUM SPEED."""
        url = self.base + video_id

        # 1. တိုက်ရိုက် Live Stream ဖြစ်လျှင်
        if is_live:
            cookie = await self.get_cookies_async()
            ydl_opts = {
                "quiet": True, "no_warnings": True, "cookiefile": cookie,
                "format": "bestaudio/best", "noplaylist": True, "socket_timeout": 20,
                "extractor_args": {"youtube": {"player_client": ["android", "web"]}},
            }
            def _extract_url():
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    try:
                        info = ydl.extract_info(url, download=False)
                        if info:
                            return info.get("url") or info.get("manifest_url")
                    except Exception:
                        return None
            return await asyncio.wait_for(asyncio.to_thread(_extract_url), timeout=35)

        # 2. Local Storage ထဲမှာ သီချင်းရှိနှင့်ပြီးသားလား အရင်စစ်မည် (50KB ထက် ကြီးရမည်)
        existing = self._locate_download_file(video_id, video=video)
        if existing and os.path.exists(existing) and os.path.getsize(existing) > 50000:
            return existing

        # 3. ⭐ [API FIRST MODE] အချိန်မဆိုင်းဘဲ ArtistBots API ကို ဒေါင်းလုဒ်အမြန်ဆုံးရရန် အရင်ခေါ်မည်
        if self.enable_api_fallback:
            try:
                api_result = await self.download_via_api(url, video=video)
                if api_result and os.path.exists(api_result) and os.path.getsize(api_result) > 50000:
                    return api_result
                else:
                    if api_result and os.path.exists(api_result):
                        try:
                            os.remove(api_result)
                        except Exception:
                            pass
            except Exception as e:
                logger.warning(f"⚠️ API First Exception caught: {e}")

        # 4. 🛡️ [Fallback Track] API ပါ လုံးဝအဆင်မပြေမှသာ Local yt-dlp + FFmpeg ဖြင့် ဆွဲမည်
        logger.info(f"🔄 API Failed or Returned Invalid File. Falling back strictly to local yt-dlp for {video_id}...")
        async with self._download_semaphore:
            cookie = await self.get_cookies_async()
            base_opts = {
                "outtmpl": "downloads/%(id)s.%(ext)s", "quiet": True, "noplaylist": True,
                "geo_bypass": True, "no_warnings": True, "overwrites": True,  
                "socket_timeout": 30, "retries": 3,
                "extractor_args": {"youtube": {"player_client": ["android", "web"]}},
            }

            if video:
                height_filter = f"[height<={self._max_video_height}]" if self._max_video_height else ""
                ydl_opts = {
                    **base_opts, "format": f"bestvideo[ext=mp4]{height_filter}+bestaudio[ext=m4a]/bestvideo+bestaudio/best",
                    "merge_output_format": "mp4", "postprocessors": [{"key": "FFmpegVideoConvertor", "preferedformat": "mp4"}],
                }
            else:
                ydl_opts = {
                    **base_opts, 
                    "format": "bestaudio[ext=m4a]/bestaudio/best",
                    "postprocessors": [{
                        "key": "FFmpegExtractAudio",
                        "preferredcodec": "mp3",
                        "preferredquality": "192",
                    }],
                }

            if cookie:
                ydl_opts["cookiefile"] = cookie

            def _local_download():
                try:
                    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                        ydl.extract_info(url, download=True)
                    return self._locate_download_file(video_id, video=video)
                except Exception as ex:
                    logger.error(f"❌ Local yt-dlp download failed completely: {ex}")
                    return self._locate_download_file(video_id, video=video)

            return await asyncio.to_thread(_local_download)
