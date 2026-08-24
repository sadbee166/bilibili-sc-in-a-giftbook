"""Small, non-blocking event hub used by live-event adapters and browsers."""

from __future__ import annotations

import asyncio
from collections import deque
from typing import Any, Deque, Dict, List, Mapping, Optional, Union


Event = Dict[str, Any]


class ResyncRequired:
    """Marker returned when the requested sequence is outside the replay window."""

    def __repr__(self) -> str:
        return "RESYNC_REQUIRED"


RESYNC_REQUIRED = ResyncRequired()
ReplayResult = Union[List[Event], ResyncRequired]


class EventSubscriber:
    """One browser connection's bounded, non-blocking event queue."""

    def __init__(self, max_queue_size: int) -> None:
        self._queue: "asyncio.Queue[Optional[Event]]" = asyncio.Queue(maxsize=max_queue_size)
        self._closed = False

    @property
    def closed(self) -> bool:
        return self._closed

    def put_nowait(self, event: Event) -> None:
        if self._closed:
            return
        self._queue.put_nowait(event)

    async def get(self) -> Optional[Event]:
        return await self._queue.get()

    def discard_through(self, seq: int) -> None:
        """Drop replay duplicates already sent by a WebSocket handshake."""

        retained = []
        while True:
            try:
                event = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            if event is not None and event.get("seq", 0) > seq:
                retained.append(event)

        for event in retained:
            self._queue.put_nowait(event)

    def close(self) -> None:
        if self._closed:
            return

        self._closed = True
        # A disconnected/slow browser no longer needs the queued payloads. Clear
        # the queue so the close sentinel can always be delivered without await.
        while True:
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        self._queue.put_nowait(None)

    def __aiter__(self) -> "EventSubscriber":
        return self

    async def __anext__(self) -> Event:
        event = await self.get()
        if event is None:
            raise StopAsyncIteration
        return event


class EventHub:
    """Assign sequence numbers, retain a short replay window, and fan out events.

    ``publish`` deliberately has no await points. A Bilibili handler can call it
    from the receive coroutine and either enqueue an event immediately or drop a
    subscriber whose queue is full. A slow browser therefore cannot hold up the
    upstream WebSocket.
    """

    def __init__(self, replay_size: int = 256, subscriber_queue_size: int = 64) -> None:
        if replay_size < 1:
            raise ValueError("replay_size must be positive")
        if subscriber_queue_size < 1:
            raise ValueError("subscriber_queue_size must be positive")

        self._next_seq = 1
        self._replay: Deque[Event] = deque(maxlen=replay_size)
        self._subscribers: set[EventSubscriber] = set()
        self._subscriber_queue_size = subscriber_queue_size

    @property
    def current_seq(self) -> int:
        return self._next_seq - 1

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    def publish(self, event: Mapping[str, Any]) -> None:
        """Assign a sequence and fan out a copy of ``event`` without blocking."""

        if not isinstance(event, Mapping):
            raise TypeError("event must be a mapping")

        normalized = dict(event)
        normalized.setdefault("version", 1)
        normalized["seq"] = self._next_seq
        self._next_seq += 1
        self._replay.append(normalized)

        for subscriber in tuple(self._subscribers):
            try:
                subscriber.put_nowait(normalized)
            except asyncio.QueueFull:
                # A full queue means this browser is no longer keeping up. Drop
                # it so future Bilibili messages remain immediately publishable.
                self.unsubscribe(subscriber)

    def subscribe(self) -> EventSubscriber:
        subscriber = EventSubscriber(self._subscriber_queue_size)
        self._subscribers.add(subscriber)
        return subscriber

    def unsubscribe(self, subscriber: EventSubscriber) -> None:
        if subscriber in self._subscribers:
            self._subscribers.remove(subscriber)
        subscriber.close()

    def replay_after(self, seq: int) -> ReplayResult:
        """Return events newer than ``seq`` or ``RESYNC_REQUIRED`` if too old."""

        if not isinstance(seq, int) or isinstance(seq, bool) or seq < 0:
            raise ValueError("seq must be a non-negative integer")
        if not self._replay:
            return []

        oldest_seq = self._replay[0]["seq"]
        if seq < oldest_seq - 1:
            return RESYNC_REQUIRED
        return [event for event in self._replay if event["seq"] > seq]


__all__ = (
    "Event",
    "EventHub",
    "EventSubscriber",
    "RESYNC_REQUIRED",
    "ReplayResult",
    "ResyncRequired",
)
