"""Bilibili live source adapter for normalized live events."""

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


def _membership_source_key(
    room_id: int,
    user_id: int,
    guard_level: int,
    start_time: int,
    end_time: int,
    price: int,
    quantity: int,
    source: int,
) -> str:
    """Build a stable key for a membership purchase replayed by Bilibili."""

    parts = (room_id, user_id, guard_level, start_time, end_time, price, quantity, source)
    return "bilibili:" + ":membership:" + ":".join(str(part) for part in parts)


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


def normalize_membership(room_id: int, message: web_models.UserToastV2Message) -> dict[str, Any]:
    """Convert a paid ``USER_TOAST_MSG_V2`` model into a normalized event."""

    user_id = int(message.uid)
    guard_level = int(message.guard_level)
    start_time = int(message.start_time)
    end_time = int(message.end_time)
    price = int(message.price)
    quantity = int(message.num)
    source = int(message.source)
    return {
        "version": 1,
        "type": "membership.created",
        "sourceKey": _membership_source_key(
            room_id,
            user_id,
            guard_level,
            start_time,
            end_time,
            price,
            quantity,
            source,
        ),
        "roomId": room_id,
        "userId": user_id,
        "userName": str(message.username),
        "guardLevel": guard_level,
        "quantity": quantity,
        "unit": str(message.unit),
        "amount": round(price * quantity / 1000, 2),
        "unitAmount": round(price / 1000, 2),
        "price": price,
        "source": source,
        "startTime": start_time,
        "endTime": end_time,
        "toastText": str(message.toast_msg),
        "giftId": int(message.gift_id),
    }


class LiveEventHandler(blivedm.BaseHandler):
    """Publish normalized Super Chat and opt-in paid membership events."""

    def __init__(
        self,
        publish: EventPublisher,
        room_id: Optional[int] = None,
        *,
        membership_logging: bool = False,
    ) -> None:
        self._publish = publish
        self._configured_room_id = int(room_id) if room_id is not None else None
        self._membership_logging = bool(membership_logging)

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

    def _on_user_toast_v2(self, client: Any, message: web_models.UserToastV2Message) -> None:
        if not self._membership_logging or int(message.source) == 2:
            return
        self._publish(normalize_membership(self._event_room_id(client), message))


# Keep the old import name for callers that only know about Super Chats.
SuperChatHandler = LiveEventHandler


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
        membership_logging: bool = False,
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
        self.membership_logging = bool(membership_logging)
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
                client.set_handler(
                    LiveEventHandler(
                        self._publish,
                        room_id=room_id,
                        membership_logging=self.membership_logging,
                    )
                )
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
    "LiveEventHandler",
    "LiveSource",
    "normalize_membership",
    "SuperChatHandler",
    "normalize_super_chat",
    "normalize_super_chat_delete",
)
