import os
import re
import uuid
import time
import asyncio
import random
from typing import Union
from datetime import datetime, timedelta
from fastapi import FastAPI, Request, Response, HTTPException, Query
from collections import defaultdict
from youtubesearchpython import VideosSearch
import yt_dlp
import subprocess

# --- FastAPI App Setup ---
app = FastAPI()

# --- Constants ---
API_KEYS = {"abc123": True}  # Replace or expand later using MongoDB
REQUEST_LIMIT = 100  # Per IP per minute
STREAM_EXPIRE = 3600  # Not used yet but can cache stream URLs
PROXIES = [None]  # Optional: Add proxy URLs

# --- Rate Limiting DB ---
rate_limit_db = defaultdict(list)

def check_rate_limit(ip: str):
    now = datetime.now()
    rate_limit_db[ip] = [t for t in rate_limit_db[ip] if now - t < timedelta(minutes=1)]
    if len(rate_limit_db[ip]) >= REQUEST_LIMIT:
        raise HTTPException(status_code=429, detail="Too many requests")
    rate_limit_db[ip].append(now)

# --- YouTube API Handler Class ---
class YouTubeAPI:
    def __init__(self):
        self.base = "https://www.youtube.com/watch?v="
        self.listbase = "https://youtube.com/playlist?list="
        self.regex = r"(?:youtube.com|youtu.be)"
        self.current_proxy = None
        self.rotate_proxy()

    def rotate_proxy(self):
        self.current_proxy = random.choice(PROXIES)
        self.ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'proxy': self.current_proxy,
            'socket_timeout': 30,
            'user_agent': 'Mozilla/5.0'
        }

    async def search(self, query: str):
        results = VideosSearch(query, limit=10)
        return (await results.next())["result"]

    async def details(self, link: str):
        results = VideosSearch(link, limit=1)
        for result in (await results.next())["result"]:
            return {
                "title": result["title"],
                "duration": result["duration"],
                "id": result["id"],
                "thumbnail": result["thumbnails"][0]["url"].split("?")[0],
                "link": result["link"]
            }

    async def formats(self, link: str):
        with yt_dlp.YoutubeDL({"quiet": True}) as ydl:
            info = await asyncio.to_thread(ydl.extract_info, link, download=False)
            return [
                {
                    "format": f["format"],
                    "filesize": f.get("filesize"),
                    "format_id": f["format_id"],
                    "ext": f["ext"],
                    "note": f.get("format_note")
                }
                for f in info.get("formats", []) if f.get("url")
            ]

    async def playlist(self, playlist_id: str, limit: int):
        link = self.listbase + playlist_id
        result = await shell_cmd(
            f"yt-dlp -i --get-id --flat-playlist --playlist-end {limit} {link}"
        )
        return [v for v in result.split("\n") if v.strip()]

    async def stream_url(self, query: str, video=False):
        with yt_dlp.YoutubeDL(self.ydl_opts) as ydl:
            info = await asyncio.to_thread(ydl.extract_info, query, download=False)
            formats = info.get("formats", [])
            if video:
                url = next((f["url"] for f in formats if f.get("vcodec") != "none"), None)
            else:
                url = next((f["url"] for f in formats if f.get("acodec") != "none"), None)
            return url

    async def download(self, link: str, format_id=None, audio=False, video=False, title=None):
        filename = title or str(uuid.uuid4())
        filepath = f"downloads/{filename}.%(ext)s"
        opts = {
            "format": format_id or ("bestaudio" if audio else "best"),
            "outtmpl": filepath,
            "quiet": True,
            "no_warnings": True,
            "geo_bypass": True,
            "prefer_ffmpeg": True,
        }
        if audio:
            opts["postprocessors"] = [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }]
        if video:
            opts["merge_output_format"] = "mp4"

        os.makedirs("downloads", exist_ok=True)

        def _download():
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([link])
            return filepath.replace("%(ext)s", "mp3" if audio else "mp4")

        return await asyncio.get_event_loop().run_in_executor(None, _download)

# --- Helper to run shell commands ---
async def shell_cmd(cmd):
    proc = await asyncio.create_subprocess_shell(
        cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate()
    return out.decode() if out else err.decode()

# --- Instantiate API Handler ---
api = YouTubeAPI()

# --- Rate Limiting Middleware ---
@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    client_ip = request.client.host
    check_rate_limit(client_ip)
    return await call_next(request)

# --- API Endpoints ---

@app.get("/search")
async def search(query: str, api_key: str = Query(...)):
    if api_key not in API_KEYS:
        raise HTTPException(status_code=403, detail="Invalid API key")
    return await api.search(query)

@app.get("/details")
async def get_details(link: str, api_key: str = Query(...)):
    if api_key not in API_KEYS:
        raise HTTPException(status_code=403, detail="Invalid API key")
    return await api.details(link)

@app.get("/formats")
async def get_formats(link: str, api_key: str = Query(...)):
    if api_key not in API_KEYS:
        raise HTTPException(status_code=403, detail="Invalid API key")
    return await api.formats(link)

@app.get("/playlist")
async def get_playlist(playlist_id: str, limit: int = 10, api_key: str = Query(...)):
    if api_key not in API_KEYS:
        raise HTTPException(status_code=403, detail="Invalid API key")
    return await api.playlist(playlist_id, limit)

@app.get("/stream")
async def get_stream_url(query: str, video: bool = False, api_key: str = Query(...)):
    if api_key not in API_KEYS:
        raise HTTPException(status_code=403, detail="Invalid API key")
    return {"stream_url": await api.stream_url(query, video)}

@app.get("/download")
async def download_file(link: str, format_id: str = None, audio: bool = False, video: bool = False, title: str = None, api_key: str = Query(...)):
    if api_key not in API_KEYS:
        raise HTTPException(status_code=403, detail="Invalid API key")
    return {"file_path": await api.download(link, format_id, audio, video, title)}

# --- Start the Server (for development) ---
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("youtube_api:app", host="0.0.0.0", port=8000, reload=True)
