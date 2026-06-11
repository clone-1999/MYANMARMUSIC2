# youtube.py - YouTube Download & Search Handler

import os
import re
import glob
import time
import yt_dlp
import random
import asyncio
import aiohttp
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
        self.api_url = "https://artistbots.onrender.com"  # သင့်ရဲ့ API URL
        self.api_key = "Artistbots3eueiX3jMWzy1ZLdYIqDWg"  # <--- သင်ရလာတဲ့ API Key ကို ဒီနေရာမှာ ထည့်ပါ
        self.cookie_url = "https://gist.githubusercontent.com/min-9876/69ba1894455f22b426ddccdd87dd126b/raw/69513d3263ca19563ed0c1f2430fa4a1e38bd8ab/gistfile1.txt"
        
        self.enable_api_fallback = True
        self.api_timeout = getattr(config, "API_TIMEOUT", 60)
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

        logger.info(f"🔄 YouTube API fallback enabled: {self.api_url}")
        
        # Bot တက်လာတာနဲ့ Cookie URL ကနေ Cookie တွေကို အလိုအလျောက် ဒေါင်းလုဒ်ဆွဲခိုင်းခြင်း
        asyncio.create_task(self.save_cookies([self.cookie_url]))

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

    def get_cookies(self):
        """Get random cookie file from cookies directory."""
        if not self.checked:
            cookies_dir = "Elevenyts/cookies"
            if os.path.exists(cookies_dir):
                for file in os.listdir(cookies_dir):
                    if file.endswith(".txt"):
                        self.cookies.append(file)
            self.checked = True
        
        if not self.cookies:
            if not self.warned:
                self.warned = True
                logger.warning("🍪 Cookies are missing; downloads might fail. Retrying download...")
                # Cookie မရှိသေးရင် ထပ်မံဒေါင်းလုဒ်ဆွဲဖို့ ကြိုးစားခိုင်းခြင်း
                asyncio.create_task(self.save_cookies([self.cookie_url]))
            return None
        
        cookie_file = f"Elevenyts/cookies/{random.choice(self.cookies)}"
        logger.debug(f"Using cookie file: {cookie_file}")
        return cookie_file

    async def save_cookies(self, urls: list[str]) -> None:
        """Save cookies from URLs to files."""
        logger.info("🍪 Saving cookies from urls...")
        saved_count = 0
        
        cookies_dir = Path("Elevenyts/cookies")
        cookies_dir.mkdir(parents=True, exist_ok=True)
        
        for url in urls:
            try:
                path = cookies_dir / f"cookie{random.randint(10000, 99999)}.txt"
                
                if "pastebin.com" in url:
                    link = url.replace("pastebin.com", "pastebin.com/raw")
                elif "batbin.me" in url:
                    link = url.replace("batbin.me", "batbin.me/raw")
                else:
                    link = url
                
                async with aiohttp.ClientSession() as session:
                    async with session.get(link, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                        if resp.status != 200:
                            logger.error(f"❌ Cookie download failed: HTTP {resp.status} from {url}")
                            continue
                        
                        content = await resp.read()
                        if not content or len(content) < 50:
                            logger.error(f"❌ Cookie file empty or invalid from {url}")
                            continue
                        
                        with open(path, "wb") as fw:
                            fw.write(content)
                        
                        if path.exists() and path.stat().st_size > 0:
                            saved_count += 1
                            cookie_filename = path.name
                            if cookie_filename not in self.cookies:
                                self.cookies.append(cookie_filename)
                            logger.info(f"✅ Saved: {cookie_filename} ({len(content)} bytes)")
                            
            except asyncio.TimeoutError:
                logger.error(f"❌ Cookie download timeout from {url}")
            except Exception as e:
                logger.error(f"❌ Cookie download error from {url}: {e}")
        
        self.checked = True
        if saved_count > 0:
            logger.info(f"✅ Cookies saved successfully! ({saved_count} file(s))")

    async def download_via_api(self, link: str, video: bool = False) -> Optional[str]:
        """Download audio/video using Railway API (fallback when cookies fail)."""
        if not self.enable_api_fallback:
            return None

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

        if os.path.exists(file_path):
            return file_path

        endpoint = "/vdown" if video else "/download"
        
        try:
            logger.info(f"🔄 Trying API fallback for {video_id} (endpoint: {endpoint})")
            
            async with aiohttp.ClientSession() as session:
                # API Key ကိုပါ Headers ထဲမှာဖြစ်စေ၊ Params ထဲမှာဖြစ်စေ လှမ်းပို့ပေးခြင်း
                params = {
                    "url": f"https://youtu.be/{video_id}",
                    "api_key": self.api_key  # Params ထဲတွင် API Key သယ်ဆောင်သွားခြင်း
                }
                headers = {
                    "Authorization": f"Bearer {self.api_key}"  # Header ထဲတွင်လည်း ထည့်ပေးထားခြင်း
                }
                
                async with session.get(
                    f"{self.api_url}{endpoint}",
                    params=params,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=self.api_timeout),
                ) as response:
                    if response.status != 200:
                        logger.debug(f"API returned status {response.status}")
                        return None
                    
                    content_type = response.headers.get('content-type', '')
                    
                    if 'application/json' in content_type:
                        data = await response.json()
                        stream_url = data.get('stream_url') or data.get('url')
                        
                        if not stream_url:
                            return None
                        
                        logger.info(f"📥 Downloading from stream URL: {stream_url[:50]}...")
                        async with session.get(stream_url, timeout=aiohttp.ClientTimeout(total=self.api_stream_timeout)) as file_response:
                            if file_response.status != 200:
                                return None
                            
                            with open(file_path, "wb") as f:
                                async for chunk in file_response.content.iter_chunked(16384):
                                    f.write(chunk)
                            
                            logger.info(f"✅ API download successful: {file_path}")
                            return file_path
                    else:
                        logger.info("📥 Receiving direct binary download...")
                        with open(file_path, "wb") as f:
                            async for chunk in response.content.iter_chunked(16384):
                                f.write(chunk)
                        
                        logger.info(f"✅ API download successful: {file_path}")
                        return file_path

        except asyncio.TimeoutError:
            logger.debug(f"API timeout for {video_id}")
            return None
        except Exception as e:
            logger.debug(f"API download failed for {video_id}: {e}")
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
                oldest_key = min(self.search_cache.keys(),
                                 key=lambda k: self.search_cache[k][1])
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
        except KeyError as e:
            raise Exception(f"Failed to parse playlist. YouTube may have changed their structure.")
        except Exception as e:
            logger.error(f"Playlist extraction error: {e}")
            raise

    async def download(self, video_id: str, is_live: bool = False, video: bool = False) -> Optional[str]:
        """Download audio/video from YouTube."""
        url = self.base + video_id

        if is_live:
            cookie = self.get_cookies()
            ydl_opts = {
                "quiet": True,
                "no_warnings": True,
                "cookiefile": cookie,
                "format": "bestaudio/best",
                "noplaylist": True,
                "socket_timeout": 20,
                "extractor_retries": 5,
                "sleep_interval_requests": 1,
                "extractor_args": {"youtube": {"player_client": ["android", "web"]}},
            }

            def _extract_url():
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    try:
                        info = ydl.extract_info(url, download=False)
                        if not info:
                            return None

                        direct = info.get("url")
                        if direct:
                            return direct

                        for fmt in info.get("formats", []):
                            if fmt.get("acodec") != "none" and fmt.get("url"):
                                return fmt["url"]

                        return info.get("manifest_url")
                    except Exception as ex:
                        logger.error(f"Live stream extraction failed: {ex}")
                        return None

            try:
                stream_url = await asyncio.wait_for(asyncio.to_thread(_extract_url), timeout=35)
                if stream_url:
                    logger.info(f"✅ Live stream URL extracted for {video_id}")
                return stream_url
            except asyncio.TimeoutError:
                logger.error(f"Live stream URL extraction timed out for {video_id}")
                return None

        filename_pattern = f"downloads/{video_id}"
        
        existing_files = [
            f for f in glob.glob(f"{filename_pattern}.*")
            if not f.endswith('.part')
        ]
        
        if video:
            video_candidates = [
                f for f in existing_files
                if Path(f).suffix.lower() in {".mp4", ".mkv", ".webm", ".mov"}
            ]
            if video_candidates:
                return video_candidates[0]
        else:
            audio_candidates = [
                f for f in existing_files
                if Path(f).suffix.lower() in {".m4a", ".webm", ".opus", ".mp3", ".ogg", ".wav", ".flac"}
            ]
            if audio_candidates:
                return audio_candidates[0]

            container_fallbacks = [
                f for f in existing_files
                if Path(f).suffix.lower() in {".mp4", ".mkv", ".mov"}
            ]
            if container_fallbacks:
                return container_fallbacks[0]
        
        downloads_dir = Path("downloads")
        if not downloads_dir.exists():
            try:
                downloads_dir.mkdir(parents=True, exist_ok=True)
            except Exception as e:
                logger.error(f"❌ Cannot create downloads directory: {e}")
                return None

        async with self._download_semaphore:
            cookie = self.get_cookies()
            base_opts = {
                "outtmpl": "downloads/%(id)s.%(ext)s",
                "quiet": True,
                "noplaylist": True,
                "geo_bypass": True,
                "no_warnings": True,
                "overwrites": False,
                "nocheckcertificate": True,
                "continuedl": True,
                "noprogress": True,
                "concurrent_fragment_downloads": 4,
                "http_chunk_size": 524288,
                "socket_timeout": 30,
                "retries": 2,
                "fragment_retries": 2,
                "extractor_retries": 5,
                "sleep_interval_requests": 1,
                "extractor_args": {"youtube": {"player_client": ["android", "web"]}},
            }

            if video:
                height_filter = f"[height<={self._max_video_height}]" if self._max_video_height else ""
                format_chain = (
                    f"bestvideo[ext=mp4]{height_filter}+bestaudio[ext=m4a]/"
                    f"bestvideo{height_filter}+bestaudio/"
                    "bestvideo+bestaudio/best"
                )
                ydl_opts = {
                    **base_opts,
                    "format": format_chain,
                    "merge_output_format": "mp4",
                    "postprocessors": [{"key": "FFmpegVideoConvertor", "preferedformat": "mp4"}],
                }
            else:
                ydl_opts = {
                    **base_opts,
                    "format": "bestaudio[ext=m4a]/bestaudio[acodec=opus]/bestaudio/best",
                    "postprocessors": [],
                }

            ydl_opts_cookie = {
                **ydl_opts,
                "cookiefile": cookie,
            }

            def _download(ydl_runtime_opts):
                ydl_instance = None
                try:
                    ydl_instance = yt_dlp.YoutubeDL(ydl_runtime_opts)
                    info = ydl_instance.extract_info(url, download=True)
                    if not info:
                        return None
                    
                    time.sleep(0.5)
                    located = self._locate_download_file(video_id, video=video)
                    if located:
                        return located
                    return None
                except Exception as ex:
                    logger.warning(f"⚠️ Download error for {video_id}: {ex}")
                    return self._locate_download_file(video_id, video=video)
                finally:
                    if ydl_instance:
                        try:
                            ydl_instance.close()
                        except Exception:
                            pass

            logger.info(f"📥 Downloading {video_id} with cookies...")
            result = await asyncio.to_thread(_download, ydl_opts_cookie)
            
            if not result and self.enable_api_fallback:
                logger.info(f"🔄 Cookie download failed for {video_id}, trying API fallback...")
                result = await self.download_via_api(url, video=video)
            
            return result
