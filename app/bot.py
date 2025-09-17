from __future__ import annotations

from aiogram import Router, types
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.markdown import hbold

from .storage import Storage
from .youtube_client import YouTubeClient

router = Router()

HELP_TEXT = (
    "Я уведомляю выбранные чаты Telegram, когда каналы YouTube выходят в эфир.\n\n"
    "Команды (личный чат):\n"
    "/subscribe или /add — добавить канал и назначения через мастер\n"
    "/remove или /delete — удалить канал (по номеру)\n"
    "/list или /show — показать каналы и куда уходят уведомления\n"
    "/cancel — отменить текущее действие\n"
)


def _sanitize(text: str) -> str:
    return text.strip()


class SubscribeStates(StatesGroup):
    waiting_yt = State()
    waiting_dest = State()


class RemoveStates(StatesGroup):
    picking = State()


@router.message(Command("start"))
@router.message(Command("help"))
async def cmd_start(message: types.Message) -> None:
    await message.answer(HELP_TEXT)


@router.message(Command("list"))
@router.message(Command("show"))
async def cmd_list(message: types.Message, storage: Storage, yt: YouTubeClient) -> None:
    if message.chat.type != "private":
        return
    subs = storage.list_subscriptions(message.chat.id)
    if not subs:
        await message.answer("Каналы не настроены.")
        return
    lines = []
    for idx, cid in enumerate(subs, start=1):
        title = await yt.get_channel_title(cid) or cid
        dests = storage.list_destinations(cid)
        lines.append(f"{idx}. {title} ({cid})\n   → {', '.join(map(str, dests)) or 'только личный чат'}")
    await message.answer("Ваши подписки:\n" + "\n".join(lines))


@router.message(Command("subscribe"))
@router.message(Command("add"))
async def cmd_subscribe(message: types.Message, command: CommandObject, storage: Storage, yt: YouTubeClient, state: FSMContext) -> None:
    if message.chat.type != "private":
        return
    arg = _sanitize(command.args or "")
    if not arg:
        await state.set_state(SubscribeStates.waiting_yt)
        await message.answer("Отправьте ссылку/ID/@хэндл канала YouTube или /cancel")
        return
    channel_id = await yt.resolve_channel_id(arg)
    if not channel_id:
        await message.answer("Канал не найден. Укажите корректный URL, @хэндл или ID.")
        return
    storage.add_subscription(message.chat.id, channel_id)
    title = await yt.get_channel_title(channel_id) or channel_id
    import html as _html
    await message.answer(f"Оформлена подписка на {hbold(_html.escape(title))} ({channel_id}).")

    # If channel is already live and was notified before, inform this chat immediately.
    live = await yt.get_live_now(channel_id)
    if live:
        last = storage.get_last_live(channel_id)
        if last == live.video_id:
            url = yt.video_url(live.video_id)
            live_title = _html.escape(live.video_title or "Прямая трансляция")
            chan_name = _html.escape(live.channel_title or channel_id)
            await message.answer(f"{chan_name} в эфире: {live_title}\n{url}")


@router.message(SubscribeStates.waiting_yt)
async def sub_waiting_yt(message: types.Message, state: FSMContext, yt: YouTubeClient) -> None:
    if message.chat.type != "private":
        return
    text = _sanitize(message.text or "")
    if text.lower() in {"/cancel", "cancel", "отмена"}:
        await state.clear()
        await message.answer("Отменено.")
        return
    channel_id = await yt.resolve_channel_id(text)
    if not channel_id:
        await message.answer("Канал не найден. Отправьте другую ссылку/ID/@хэндл или /cancel")
        return
    await state.update_data(channel_id=channel_id)
    await state.set_state(SubscribeStates.waiting_dest)
    await message.answer(
        "Теперь отправьте назначения в Telegram (через пробел):\n"
        "- @username или t.me/username или числовой ID чата\n"
        "Отправьте 'skip' или 'пропустить', чтобы использовать только личный чат. Либо /cancel"
    )


@router.message(SubscribeStates.waiting_dest)
async def sub_waiting_dest(message: types.Message, state: FSMContext, storage: Storage, yt: YouTubeClient) -> None:
    if message.chat.type != "private":
        return
    text = _sanitize(message.text or "")
    if text.lower() in {"/cancel", "cancel", "отмена"}:
        await state.clear()
        await message.answer("Отменено.")
        return
    data = await state.get_data()
    channel_id = data.get("channel_id")
    if not channel_id:
        await state.clear()
        await message.answer("Сессия потеряна. Начните заново командой /subscribe")
        return
    # Always subscribe the current private chat
    storage.add_subscription(message.chat.id, channel_id)

    added = []
    failed = []
    if text.lower() != "skip" and text:
        tokens = text.split()
        bot = message.bot
        if bot is None:
            await message.answer("Экземпляр бота недоступен.")
            return
        for token in tokens:
            target = _normalize_tg_target(token)
            if target is None:
                failed.append(token)
                continue
            try:
                chat = await bot.get_chat(target)
                storage.add_destination(channel_id, chat.id)
                added.append(str(chat.id))
            except Exception:
                failed.append(token)

    await state.clear()
    title = await yt.get_channel_title(channel_id) or channel_id
    import html as _html
    parts = [f"Следим за {hbold(_html.escape(title))} ({channel_id})."]
    if added:
        parts.append("Добавлены назначения: " + ", ".join(added))
    if failed:
        parts.append("Не удалось: " + ", ".join(failed))
    await message.answer("\n".join(parts))


    

def _normalize_tg_target(s: str) -> str | int | None:
    t = s.strip()
    # numeric id
    try:
        return int(t)
    except ValueError:
        pass
    # @username
    if t.startswith("@") and len(t) > 1:
        return t
    # t.me links
    if t.startswith("http://") or t.startswith("https://"):
        lower = t.lower()
        if "t.me/" in lower:
            part = t.split("t.me/", 1)[1]
            part = part.split("?", 1)[0].split("/", 1)[0]
            if not part or part.startswith("+"):
                return None
            if not part.startswith("@"):
                part = "@" + part
            return part
    if t.startswith("t.me/"):
        part = t.split("t.me/", 1)[1]
        part = part.split("?", 1)[0].split("/", 1)[0]
        if not part or part.startswith("+"):
            return None
        if not part.startswith("@"):
            part = "@" + part
        return part
    return None

    


@router.message(Command("cancel"))
async def cmd_cancel(message: types.Message, state: FSMContext) -> None:
    if message.chat.type != "private":
        return
    await state.clear()
    await message.answer("Отменено.")



@router.message(Command("remove"))
@router.message(Command("delete"))
async def cmd_remove(message: types.Message, storage: Storage, yt: YouTubeClient, state: FSMContext) -> None:
    if message.chat.type != "private":
        return
    subs = storage.list_subscriptions(message.chat.id)
    if not subs:
        await message.answer("Каналы не настроены.")
        return
    # Show numbered list
    titles = []
    for cid in subs:
        titles.append(await yt.get_channel_title(cid) or cid)
    lines = [f"{i}. {t} ({subs[i-1]})" for i, t in enumerate(titles, start=1)]
    await state.update_data(subs=subs)
    await state.set_state(RemoveStates.picking)
    await message.answer("Отправьте номер для удаления (или /cancel):\n" + "\n".join(lines))


@router.message(RemoveStates.picking)
async def remove_picking(message: types.Message, state: FSMContext, storage: Storage) -> None:
    if message.chat.type != "private":
        return
    text = _sanitize(message.text or "")
    if text.lower() in {"/cancel", "cancel", "отмена"}:
        await state.clear()
        await message.answer("Отменено.")
        return
    if not text.isdigit():
        await message.answer("Пожалуйста, отправьте номер из списка или /cancel")
        return
    idx = int(text)
    data = await state.get_data()
    subs = data.get("subs", [])
    if idx < 1 or idx > len(subs):
        await message.answer("Вне диапазона. Попробуйте снова или /cancel")
        return
    channel_id = subs[idx - 1]
    # Remove subscription from this private chat and clear destinations for this channel
    storage.remove_subscription(message.chat.id, channel_id)
    storage.clear_destinations(channel_id)
    await state.clear()
    await message.answer(f"Канал {channel_id} и его назначения удалены.")

