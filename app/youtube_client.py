from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional, List

import asyncio
import httpx


_YT_CHANNEL_URL_RE = re.compile(r"https?://(www\.)?youtube\.com/(channel/|@)([A-Za-z0-9_\-\.]+)")
_YT_VIDEO_URL = "https://www.youtube.com/watch?v={video_id}"


@dataclass
class LiveInfo:
    channel_id: str
    channel_title: Optional[str]
    video_id: str
    video_title: Optional[str]
    scheduled_start_time: Optional[str]


class YouTubeClient:
    def __init__(self, api_keys: str | List[str]) -> None:
        if isinstance(api_keys, str):
            self._keys = [api_keys]
        else:
            self._keys = [k for k in api_keys if k]
        if not self._keys:
            raise ValueError("YouTubeClient requires at least one API key")
        self._key_index = 0
        self._client = httpx.AsyncClient(timeout=15)

    @property
    def api_key(self) -> str:
        return self._keys[self._key_index]

    def _advance_key(self) -> None:
        if len(self._keys) > 1:
            self._key_index = (self._key_index + 1) % len(self._keys)
            try:
                print(f"YouTubeClient: rotating API key (index {self._key_index+1}/{len(self._keys)})")
            except Exception:
                pass

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _get(self, url: str, *, params: dict, retries: int = 3) -> Optional[httpx.Response]:
        delay = 0.5
        keys_tried = 0
        # Try across keys; for each key, retry transient errors
        while keys_tried < len(self._keys):
            params_with_key = dict(params)
            params_with_key["key"] = self.api_key
            for attempt in range(retries):
                try:
                    r = await self._client.get(url, params=params_with_key)
                    # If quota exceeded for this key, rotate to next and try again
                    if r.status_code == 403:
                        try:
                            data = r.json()
                            reasons = {e.get("reason") for e in data.get("error", {}).get("errors", []) if isinstance(e, dict)}
                        except Exception:
                            reasons = set()
                        quota_reasons = {"quotaExceeded", "dailyLimitExceeded", "rateLimitExceeded", "keyInvalid"}
                        if reasons & quota_reasons:
                            self._advance_key()
                            keys_tried += 1
                            break  # break inner retry loop to switch key
                    r.raise_for_status()
                    return r
                except (httpx.ConnectError, httpx.ReadTimeout, httpx.WriteTimeout):
                    if attempt == retries - 1:
                        return None
                    await asyncio.sleep(delay)
                    delay *= 2
                except httpx.HTTPStatusError:
                    # Non-quota HTTP errors: don't rotate, return None
                    return None
            else:
                # Finished retries without quota rotation; stop trying keys
                return None
        return None

    @staticmethod
    def extract_channel_hint(text: str) -> Optional[str]:
        m = _YT_CHANNEL_URL_RE.search(text.strip())
        if not m:
            return None
        return m.group(3)

    async def resolve_channel_id(self, identifier_or_url: str) -> Optional[str]:
        hint = self.extract_channel_hint(identifier_or_url) or identifier_or_url.strip()
        if hint.startswith("UC") and len(hint) >= 20:
            return hint
        # Try search by handle or name
        params = {
            "part": "snippet",
            "q": hint,
            "type": "channel",
            "maxResults": 1,
            "key": self.api_key,
        }
        r = await self._get("https://www.googleapis.com/youtube/v3/search", params=params)
        if r is None:
            return None
        items = r.json().get("items", [])
        if not items:
            return None
        return items[0]["snippet"]["channelId"]

    async def get_channel_title(self, channel_id: str) -> Optional[str]:
        params = {"part": "snippet", "id": channel_id, "key": self.api_key}
        r = await self._get("https://www.googleapis.com/youtube/v3/channels", params=params)
        if r is None:
            return None
        items = r.json().get("items", [])
        if not items:
            return None
        return items[0]["snippet"].get("title")

    async def get_live_now(self, channel_id: str) -> Optional[LiveInfo]:
        # Search for live events
        params = {
            "part": "snippet",
            "channelId": channel_id,
            "eventType": "live",
            "type": "video",
            "maxResults": 1,
            "order": "date",
            "key": self.api_key,
        }
        r = await self._get("https://www.googleapis.com/youtube/v3/search", params=params)
        if r is None:
            return None
        items = r.json().get("items", [])
        if not items:
            return None
        video_id = items[0]["id"]["videoId"]
        # Fetch video details for title/schedule
        params2 = {"part": "snippet,liveStreamingDetails", "id": video_id, "key": self.api_key}
        r2 = await self._get("https://www.googleapis.com/youtube/v3/videos", params=params2)
        vitems = r2.json().get("items", []) if r2 is not None else []
        title = None
        sched = None
        if vitems:
            s = vitems[0].get("snippet", {})
            title = s.get("title")
            lsd = vitems[0].get("liveStreamingDetails", {})
            sched = lsd.get("scheduledStartTime") or lsd.get("actualStartTime")
        chan_title = await self.get_channel_title(channel_id)
        return LiveInfo(channel_id=channel_id, channel_title=chan_title, video_id=video_id, video_title=title, scheduled_start_time=sched)

    @staticmethod
    def video_url(video_id: str) -> str:
        return _YT_VIDEO_URL.format(video_id=video_id)
