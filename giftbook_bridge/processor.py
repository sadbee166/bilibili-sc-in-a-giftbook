"""Unified application processor for GiftBook's live bridge."""

from __future__ import annotations

import asyncio
import signal
from typing import Any, Optional, Tuple

from aiohttp import web

from .event_hub import EventHub
from .live_source import LiveSource
from .web_server import BridgeConfig, create_app


class GiftBookProcessor:
    """Own the complete GiftBook process lifecycle.

    One processor instance starts the Bilibili source through aiohttp startup
    hooks, binds the same-origin frontend/WebSocket server, and shuts both down
    through one cleanup path. The optional dependencies make the lifecycle
    directly testable without connecting to a real Bilibili room.
    """

    def __init__(
        self,
        config: Optional[BridgeConfig] = None,
        *,
        event_hub: Optional[EventHub] = None,
        live_source: Optional[Any] = None,
    ) -> None:
        self.config = config or BridgeConfig.from_env()
        self.event_hub = event_hub or EventHub(self.config.replay_size, self.config.subscriber_queue_size)
        self.live_source = live_source or LiveSource(
            self.config.room_ids,
            self.config.sessdata,
            self.event_hub.publish,
            membership_logging=self.config.membership_logging,
            heartbeat_interval=self.config.bilibili_heartbeat_seconds,
        )
        self.app = create_app(self.config, event_hub=self.event_hub, live_source=self.live_source)
        self._runner: Optional[web.AppRunner] = None
        self._site: Optional[web.TCPSite] = None

    @property
    def is_running(self) -> bool:
        return self._runner is not None and self._site is not None

    @property
    def bound_addresses(self) -> Tuple[Any, ...]:
        """Return the addresses currently bound by the HTTP server."""

        if self._site is None:
            return ()
        server = getattr(self._site, "_server", None)
        if server is None or not server.sockets:
            return ()
        return tuple(sock.getsockname() for sock in server.sockets)

    async def start(self) -> "GiftBookProcessor":
        """Start the live source and same-origin server exactly once."""

        if self.is_running:
            return self

        runner = web.AppRunner(self.app)
        try:
            await runner.setup()
            site = web.TCPSite(runner, host=self.config.host, port=self.config.port)
            await site.start()
        except BaseException:
            await runner.cleanup()
            raise

        self._runner = runner
        self._site = site
        return self

    async def stop(self) -> None:
        """Stop the server and source; safe to call more than once."""

        runner = self._runner
        self._runner = None
        self._site = None
        if runner is not None:
            await runner.cleanup()

    async def run_forever(self, stop_event: Optional[asyncio.Event] = None) -> None:
        """Run until cancelled, interrupted, or the supplied event is set."""

        wait_for_stop = stop_event or asyncio.Event()
        loop = asyncio.get_running_loop()
        installed_signals = []
        for signum in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(signum, wait_for_stop.set)
                installed_signals.append(signum)
            except (NotImplementedError, RuntimeError):
                # Windows event loops and embedded runners may not expose POSIX
                # signal handlers. KeyboardInterrupt still reaches __main__.
                pass

        try:
            await self.start()
            await wait_for_stop.wait()
        finally:
            for signum in installed_signals:
                loop.remove_signal_handler(signum)
            await self.stop()


__all__ = ("GiftBookProcessor",)
