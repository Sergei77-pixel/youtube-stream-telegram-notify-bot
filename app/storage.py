from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional, Set
import json
import threading


class Storage:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        if not self.path.exists():
            self._write({
                "subscriptions": {},
                "last_live": {},
                "last_live_at": {},
                "cooldown_until": {},
                "destinations": {}
            })

    def _read(self) -> Dict:
        with self._lock:
            try:
                return json.loads(self.path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                return {"subscriptions": {}, "last_live": {}, "last_live_at": {}, "cooldown_until": {}, "destinations": {}}

    def _write(self, data: Dict) -> None:
        with self._lock:
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(self.path)

    # Subscriptions mapping: chat_id -> list[channel_id]
    def add_subscription(self, chat_id: int, channel_id: str) -> None:
        data = self._read()
        subs: Dict[str, List[str]] = data.get("subscriptions", {})
        key = str(chat_id)
        channels = set(subs.get(key, []))
        channels.add(channel_id)
        subs[key] = sorted(channels)
        data["subscriptions"] = subs
        self._write(data)

    def remove_subscription(self, chat_id: int, channel_id: str) -> bool:
        data = self._read()
        subs: Dict[str, List[str]] = data.get("subscriptions", {})
        key = str(chat_id)
        channels = set(subs.get(key, []))
        if channel_id in channels:
            channels.remove(channel_id)
            subs[key] = sorted(channels)
            data["subscriptions"] = subs
            self._write(data)
            return True
        return False

    def list_subscriptions(self, chat_id: int) -> List[str]:
        data = self._read()
        return list(data.get("subscriptions", {}).get(str(chat_id), []))

    def all_subscribers_for(self, channel_id: str) -> Set[int]:
        data = self._read()
        result: Set[int] = set()
        for chat_id, channels in data.get("subscriptions", {}).items():
            if channel_id in channels:
                result.add(int(chat_id))
        return result

    def all_subscriptions(self) -> Dict[int, List[str]]:
        data = self._read()
        result: Dict[int, List[str]] = {}
        for chat_id, channels in data.get("subscriptions", {}).items():
            result[int(chat_id)] = list(channels)
        return result

    def all_channels(self) -> Set[str]:
        data = self._read()
        channels: Set[str] = set()
        for lst in data.get("subscriptions", {}).values():
            channels.update(lst)
        # include destinations keys as tracked channels
        channels.update(data.get("destinations", {}).keys())
        return channels

    # last_live mapping: channel_id -> video_id
    def get_last_live(self, channel_id: str) -> Optional[str]:
        data = self._read()
        return data.get("last_live", {}).get(channel_id)

    def set_last_live(self, channel_id: str, video_id: str) -> None:
        data = self._read()
        last_live: Dict[str, str] = data.get("last_live", {})
        last_live[channel_id] = video_id
        data["last_live"] = last_live
        self._write(data)

    # last_live_at mapping: channel_id -> ISO timestamp
    def get_last_live_at(self, channel_id: str) -> Optional[str]:
        data = self._read()
        return data.get("last_live_at", {}).get(channel_id)

    def set_last_live_at(self, channel_id: str, iso_ts: str) -> None:
        data = self._read()
        last_live_at: Dict[str, str] = data.get("last_live_at", {})
        last_live_at[channel_id] = iso_ts
        data["last_live_at"] = last_live_at
        self._write(data)

    # cooldown_until mapping: channel_id -> ISO timestamp
    def get_cooldown_until(self, channel_id: str) -> Optional[str]:
        data = self._read()
        return data.get("cooldown_until", {}).get(channel_id)

    def set_cooldown_until(self, channel_id: str, iso_ts: str) -> None:
        data = self._read()
        cooldown: Dict[str, str] = data.get("cooldown_until", {})
        cooldown[channel_id] = iso_ts
        data["cooldown_until"] = cooldown
        self._write(data)

    # destinations mapping: channel_id -> list[int chat_id]
    def add_destination(self, channel_id: str, chat_id: int) -> None:
        data = self._read()
        dests: Dict[str, List[int]] = data.get("destinations", {})
        lst = set(dests.get(channel_id, []))
        lst.add(int(chat_id))
        dests[channel_id] = sorted(lst)
        data["destinations"] = dests
        self._write(data)

    def remove_destination(self, channel_id: str, chat_id: int) -> bool:
        data = self._read()
        dests: Dict[str, List[int]] = data.get("destinations", {})
        lst = set(dests.get(channel_id, []))
        if int(chat_id) in lst:
            lst.remove(int(chat_id))
            dests[channel_id] = sorted(lst)
            data["destinations"] = dests
            self._write(data)
            return True
        return False

    def list_destinations(self, channel_id: str) -> List[int]:
        data = self._read()
        return list(data.get("destinations", {}).get(channel_id, []))

    def clear_destinations(self, channel_id: str) -> None:
        data = self._read()
        dests: Dict[str, List[int]] = data.get("destinations", {})
        if channel_id in dests:
            dests[channel_id] = []
            data["destinations"] = dests
            self._write(data)

