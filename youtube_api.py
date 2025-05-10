import os
import uuid
import asyncio
from datetime import datetime, timedelta
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from collections import defaultdict
from youtubesearchpython import VideosSearch
import yt_dlp

# --- FastAPI App Setup ---
app = FastAPI(middleware=[
    Middleware(CORSMiddleware, allow_origins=["*"])
])

# --- Constants ---
API_KEYS = {"abc123": True}
REQUEST_LIMIT = 100
STREAM_EXPIRE = 3600

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
        self.ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'socket_timeout': 30,
            'user_agent': 'Mozilla/5.0',
            'extract_flat': True,
            'force_ipv4': True,
            'geo_bypass': True
        }

    async def search(self, query: str):
        try:
            videos_search = VideosSearch(query, limit=10)
            return videos_search.result()["result"]
        except Exception as e:
            return {"error": str(e)}

    async def details(self, link: str):
        try:
            results = VideosSearch(link, limit=1)
            for result in results.result()["result"]:
                return {
                    "title": result["title"],
                    "duration": result["duration"],
                    "id": result["id"],
                    "thumbnail": result["thumbnails"][0]["url"].split("?")[0],
                    "link": result["link"]
                }
        except Exception as e:
            return {"error": str(e)}

    async def formats(self, link: str):
        try:
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
        except Exception as e:
            return {"error": str(e)}

    async def stream_url(self, query: str, video=False):
        try:
            with yt_dlp.YoutubeDL(self.ydl_opts) as ydl:
                info = await asyncio.to_thread(ydl.extract_info, query, download=False)
                formats = info.get("formats", [])
                if video:
                    url = next((f["url"] for f in formats if f.get("vcodec") != "none"), None)
                else:
                    url = next((f["url"] for f in formats if f.get("acodec") != "none"), None)
                return url
        except Exception as e:
            return {"error": str(e)}

# --- API Endpoints ---
@app.get("/")
async def health_check():
    return {"status": "running", "app": "YouTube API"}

@app.get("/search")
async def search(query: str, api_key: str = Query(...)):
    if api_key not in API_KEYS:
        raise HTTPException(status_code=403, detail="Invalid API key")
    return await YouTubeAPI().search(query)

@app.get("/details")
async def get_details(link: str, api_key: str = Query(...)):
    if api_key not in API_KEYS:
        raise HTTPException(status_code=403, detail="Invalid API key")
    return await YouTubeAPI().details(link)

@app.get("/stream")
async def get_stream_url(query: str, video: bool = False, api_key: str = Query(...)):
    if api_key not in API_KEYS:
        raise HTTPException(status_code=403, detail="Invalid API key")
    return {"stream_url": await YouTubeAPI().stream_url(query, video)}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("youtube_api:app", host="0.0.0.0", port=8000)
