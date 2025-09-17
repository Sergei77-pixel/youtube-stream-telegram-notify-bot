from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict, Iterable, Optional, Set

from aiogram import BaseMiddleware


class DepsMiddleware(BaseMiddleware):
    def __init__(self, **deps: Any) -> None:
        super().__init__()
        self._deps = deps

    async def __call__(
        self,
        handler: Callable[[Any, Dict[str, Any]], Awaitable[Any]],
        event: Any,
        data: Dict[str, Any],
    ) -> Any:
        data.update(self._deps)
        return await handler(event, data)


class AuthMiddleware(BaseMiddleware):
    def __init__(self, allowed_user_ids: Optional[Iterable[int]] = None) -> None:
        super().__init__()
        self._allowed: Optional[Set[int]] = set(allowed_user_ids) if allowed_user_ids else None

    async def __call__(
        self,
        handler: Callable[[Any, Dict[str, Any]], Awaitable[Any]],
        event: Any,
        data: Dict[str, Any],
    ) -> Any:
        # Only enforce for user-originated messages (private/groups). Channel posts lack from_user.
        if self._allowed:
            user = getattr(event, "from_user", None)
            if user is not None and getattr(user, "id", None) not in self._allowed:
                try:
                    await event.answer("You are not allowed to use this bot.")
                except Exception:
                    pass
                return
        return await handler(event, data)
