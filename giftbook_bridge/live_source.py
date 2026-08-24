"""Bilibili live source adapter for normalized Super Chat events."""

from __future__ import annotations

import asyncio
import http.cookies
import math
from typing import Any, Callable, Dict, Mapping, Optional, Sequence, Tuple

import aiohttp
import blivedm
import blivedm.models.web as web_models


EventPublisher = Callable[[Mapping[str, Any]], None]


def _room_id(client: Any) -> int:
    return int(client.room_id)


def _source_key(room_id: int, message_id: int) -> str:
    return f"bilibili:{room_id}:super-chat:{message_id}"


def normalize_super_chat(room_id: int, message: web_models.SuperChatMessage) -> dict[str, Any]:
    """Convert a blivedm model into the bridge's versioned event shape."""

    message_id = int(message.id)
    return {
        "version": 1,
        "type": "super_chat.created",
        "sourceKey": _source_key(room_id, message_id),
        "roomId": room_id,
        "messageId": message_id,
        "userName": str(message.uname),
        "amount": int(message.price),
        "message": str(message.message),
        "startTime": int(message.start_time),
        "endTime": int(message.end_time),
    }


def normalize_super_chat_delete(room_id: int, message_id: int) -> dict[str, Any]:
    """Build a deletion event without retaining Bilibili model details."""

    message_id = int(message_id)
    return {
        "version": 1,
        "type": "super_chat.deleted",
        "sourceKey": _source_key(room_id, message_id),
        "roomId": room_id,
        "messageId": message_id,
    }


class SuperChatHandler(blivedm.BaseHandler):
    """Publish only normalized Super Chat lifecycle events from blivedm callbacks."""

    def __init__(self, publish: EventPublisher, room_id: Optional[int] = None) -> None:
        self._publish = publish
        self._configured_room_id = int(room_id) if room_id is not None else None

    def _event_room_id(self, client: Any) -> int:
        # Keep subscriptions stable even when Bilibili resolves a configured
        # short room number to its canonical room number internally.
        return self._configured_room_id if self._configured_room_id is not None else _room_id(client)

    def _on_super_chat(self, client: Any, message: web_models.SuperChatMessage) -> None:
        self._publish(normalize_super_chat(self._event_room_id(client), message))

    def _on_super_chat_delete(self, client: Any, message: web_models.SuperChatDeleteMessage) -> None:
        room_id = self._event_room_id(client)
        for message_id in message.ids:
            self._publish(normalize_super_chat_delete(room_id, message_id))


class LiveSource:
    """Own one BLiveClient per configured room for application lifecycle use."""

    def __init__(
        self,
        room_ids: Optional[Sequence[int]],
        sessdata: str,
        publish: EventPublisher,
        *,
        client_factory: Callable[..., Any] = blivedm.BLiveClient,
        session_factory: Callable[..., aiohttp.ClientSession] = aiohttp.ClientSession,
        heartbeat_interval: float = 30.0,
    ) -> None:
        parsed_room_ids = tuple(int(room_id) for room_id in (room_ids or ()))
        if any(room_id <= 0 for room_id in parsed_room_ids) or len(set(parsed_room_ids)) != len(parsed_room_ids):
            raise ValueError("room_ids must contain unique positive integer IDs")
        self.room_ids = parsed_room_ids
        self.sessdata = sessdata
        self._publish = publish
        self._client_factory = client_factory
        self._session_factory = session_factory
        self.heartbeat_interval = float(heartbeat_interval)
        if not math.isfinite(self.heartbeat_interval) or self.heartbeat_interval <= 0:
            raise ValueError("heartbeat_interval must be positive")
        self._session: Optional[aiohttp.ClientSession] = None
        self._clients: Dict[int, Any] = {}

    @property
    def clients(self) -> Mapping[int, Any]:
        return dict(self._clients)

    async def start(self) -> None:
        if self._clients:
            return
        if not self.room_ids:
            return

        session = self._session_factory()
        self._session = session
        if self.sessdata:
            cookies = http.cookies.SimpleCookie()
            cookies["SESSDATA"] = self.sessdata
            cookies["SESSDATA"]["domain"] = "bilibili.com"
            session.cookie_jar.update_cookies(cookies)

        try:
            for room_id in self.room_ids:
                client = self._client_factory(
                    room_id,
                    session=session,
                    heartbeat_interval=self.heartbeat_interval,
                )
                self._clients[room_id] = client
                client.set_handler(SuperChatHandler(self._publish, room_id=room_id))
                client.start()
        except Exception:
            await asyncio.gather(*(client.stop_and_close() for client in self._clients.values()), return_exceptions=True)
            self._clients.clear()
            await session.close()
            self._session = None
            raise

    async def stop(self) -> None:
        clients, session = tuple(self._clients.values()), self._session
        self._clients.clear()
        self._session = None

        await asyncio.gather(*(client.stop_and_close() for client in clients), return_exceptions=True)
        if session is not None and not session.closed:
            await session.close()


__all__ = (
    "LiveSource",
    "SuperChatHandler",
    "normalize_super_chat",
    "normalize_super_chat_delete",
)
