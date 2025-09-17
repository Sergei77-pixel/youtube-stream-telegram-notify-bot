from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Optional
from datetime import datetime, timedelta, timezone

from aiogram import Bot, Dispatcher
from aiogram.enums.parse_mode import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from dotenv import load_dotenv

from app.storage import Storage
from app.youtube_client import YouTubeClient
from app.bot import router as bot_router
from app.middlewares import DepsMiddleware, AuthMiddleware


async def notifier_loop(bot: Bot, storage: Storage, yt: YouTubeClient, interval: int, cooldown_seconds: int) -> None:
    while True:
        try:
            channels = storage.all_channels()
            for channel_id in channels:
                # Cooldown check
                cd = storage.get_cooldown_until(channel_id)
                if cd:
                    try:
                        if datetime.now(timezone.utc) < datetime.fromisoformat(cd):
                            continue
                    except Exception:
                        pass

                live = await yt.get_live_now(channel_id)
                if not live:
                    continue
                last = storage.get_last_live(channel_id)
                if last == live.video_id:
                    continue
                storage.set_last_live(channel_id, live.video_id)
                storage.set_last_live_at(channel_id, datetime.now(timezone.utc).isoformat())
                # Set cooldown to avoid rechecking soon after a live
                if cooldown_seconds > 0:
                    storage.set_cooldown_until(
                        channel_id,
                        (datetime.now(timezone.utc) + timedelta(seconds=cooldown_seconds)).isoformat(),
                    )
                url = yt.video_url(live.video_id)
                import html
                title = html.escape(live.video_title or "Прямая трансляция")
                chan = html.escape(live.channel_title or channel_id)
                text = f"{chan} в эфире: {title}\n{url}"
                targets = set(storage.all_subscribers_for(channel_id)) | set(storage.list_destinations(channel_id))
                for chat_id in targets:
                    try:
                        await bot.send_message(chat_id, text)
                    except Exception:
                        # Ignore send errors per chat
                        pass
        except Exception:
            # Keep loop alive on errors
            pass
        await asyncio.sleep(interval)


async def main() -> None:
    load_dotenv()
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    yt_key = os.getenv("YT_API_KEY")
    yt_keys_env = os.getenv("YT_API_KEYS", "").strip()
    poll_interval = int(os.getenv("POLL_INTERVAL") or 120)
    cooldown_seconds = int(os.getenv("COOLDOWN_SECONDS") or 3600)
    storage_path = Path(os.getenv("STORAGE_PATH") or "data/storage.json")

    if not token:
        raise RuntimeError("Missing TELEGRAM_BOT_TOKEN in environment/.env")
    api_keys: list[str] = []
    if yt_keys_env:
        # Support comma, semicolon, whitespace separated list
        temp = yt_keys_env.replace(";", ",").replace("\n", ",")
        api_keys = [k.strip() for k in temp.split(",") if k.strip()]
    elif yt_key:
        api_keys = [yt_key]
    if not api_keys:
        raise RuntimeError("Missing YT_API_KEY or YT_API_KEYS in environment/.env")

    storage = Storage(storage_path)
    yt = YouTubeClient(api_keys)

    bot = Bot(token, parse_mode=ParseMode.HTML)
    dp = Dispatcher(storage=MemoryStorage())

    dp.include_router(bot_router)
    dp.message.middleware(DepsMiddleware(storage=storage, yt=yt))
    dp.channel_post.middleware(DepsMiddleware(storage=storage, yt=yt))
    # Restrict usage to allowed user IDs if provided
    allowed_env = os.getenv("ALLOWED_USER_IDS", "").strip()
    allowed_ids = None
    if allowed_env:
        try:
            allowed_ids = {int(x) for x in allowed_env.replace(";", ",").split(",") if x.strip()}
        except ValueError:
            raise RuntimeError("ALLOWED_USER_IDS must be a comma- or semicolon-separated list of integers")
    if allowed_ids:
        dp.message.middleware(AuthMiddleware(allowed_user_ids=allowed_ids))

    # Start/stop background notifier with proper async handlers
    notifier_task: Optional[asyncio.Task] = None

    async def on_startup(*_args, **_kwargs):
        nonlocal notifier_task
        notifier_task = asyncio.create_task(notifier_loop(bot, storage, yt, poll_interval, cooldown_seconds))

    async def on_shutdown(*_args, **_kwargs):
        nonlocal notifier_task
        if notifier_task is not None:
            notifier_task.cancel()
            try:
                await notifier_task
            except asyncio.CancelledError:
                pass
        await yt.aclose()

    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
