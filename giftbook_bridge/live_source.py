"""Bilibili live source adapter for normalized Super Chat events."""

from __future__ import annotations

import http.cookies
import math
from typing import Any, Callable, Mapping, Optional

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
    """Own one BLiveClient and its aiohttp session for application lifecycle use."""

    def __init__(
        self,
        room_id: Optional[int],
        sessdata: str,
        publish: EventPublisher,
        *,
        client_factory: Callable[..., Any] = blivedm.BLiveClient,
        session_factory: Callable[..., aiohttp.ClientSession] = aiohttp.ClientSession,
        heartbeat_interval: float = 30.0,
    ) -> None:
        self.room_id = int(room_id) if room_id is not None else None
        self.sessdata = sessdata
        self._publish = publish
        self._client_factory = client_factory
        self._session_factory = session_factory
        self.heartbeat_interval = float(heartbeat_interval)
        if not math.isfinite(self.heartbeat_interval) or self.heartbeat_interval <= 0:
            raise ValueError("heartbeat_interval must be positive")
        self._session: Optional[aiohttp.ClientSession] = None
        self._client: Optional[Any] = None

    @property
    def client(self) -> Optional[Any]:
        return self._client

    async def start(self) -> None:
        if self._client is not None:
            return
        if self.room_id is None:
            return

        session = self._session_factory()
        self._session = session
        if self.sessdata:
            cookies = http.cookies.SimpleCookie()
            cookies["SESSDATA"] = self.sessdata
            cookies["SESSDATA"]["domain"] = "bilibili.com"
            session.cookie_jar.update_cookies(cookies)

        try:
            client = self._client_factory(
                self.room_id,
                session=session,
                heartbeat_interval=self.heartbeat_interval,
            )
            client.set_handler(SuperChatHandler(self._publish, room_id=self.room_id))
            client.start()
            self._client = client
        except Exception:
            await session.close()
            self._session = None
            raise

    async def stop(self) -> None:
        client, session = self._client, self._session
        self._client = None
        self._session = None

        if client is not None:
            await client.stop_and_close()
        if session is not None and not session.closed:
            await session.close()


__all__ = (
    "LiveSource",
    "SuperChatHandler",
    "normalize_super_chat",
    "normalize_super_chat_delete",
)
