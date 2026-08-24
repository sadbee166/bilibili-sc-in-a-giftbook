"""Same-origin aiohttp server for GiftBook and the live event WebSocket."""

from __future__ import annotations

import asyncio
import json
import math
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, List, Mapping, Optional, Tuple

from aiohttp import WSMsgType, web

from .event_hub import EventHub, EventSubscriber, RESYNC_REQUIRED
from .live_source import LiveSource


DEFAULT_FRONTEND_ROOT = Path(__file__).resolve().parent.parent / "gift-book"
DEFAULT_LOCAL_CONFIG_NAME = "giftbook.config.local.json"
BRIDGE_CONFIG_KEY = web.AppKey("bridge_config", Any)
EVENT_HUB_KEY = web.AppKey("event_hub", EventHub)
LIVE_SOURCE_KEY = web.AppKey("live_source", Any)
FRONTEND_ROOT_KEY = web.AppKey("frontend_root", Path)


@dataclass(frozen=True)
class BridgeConfig:
    room_ids: Tuple[int, ...] = ()
    sessdata: str = ""
    membership_logging: bool = False
    host: str = "127.0.0.1"
    port: int = 8080
    frontend_root: Path = DEFAULT_FRONTEND_ROOT
    replay_size: int = 256
    subscriber_queue_size: int = 64
    bilibili_heartbeat_seconds: float = 30.0
    websocket_heartbeat_seconds: float = 30.0
    subscription_timeout_seconds: float = 10.0

    def __post_init__(self) -> None:
        object.__setattr__(self, "room_ids", _parse_room_ids(self.room_ids, "room_ids"))
        object.__setattr__(self, "membership_logging", _parse_boolean(self.membership_logging, "membership_logging"))

    @classmethod
    def from_env(
        cls,
        environ: Optional[Mapping[str, str]] = None,
        *,
        frontend_root: Optional[Path] = None,
        config_path: Optional[Path] = None,
    ) -> "BridgeConfig":
        """Build immutable startup configuration from JSON first, then env overrides."""

        values = os.environ if environ is None else environ
        configured_path = Path(config_path) if config_path is not None else None
        if configured_path is None:
            configured_path_value = values.get("GIFTBOOK_CONFIG_FILE", "").strip()
            if configured_path_value:
                configured_path = Path(configured_path_value)
            else:
                local_config_path = Path.cwd() / DEFAULT_LOCAL_CONFIG_NAME
                if local_config_path.is_file():
                    configured_path = local_config_path

        file_values: Mapping[str, Any] = {}
        config_directory = Path.cwd()
        if configured_path is not None:
            resolved_config_path = configured_path.expanduser().resolve()
            try:
                with resolved_config_path.open("r", encoding="utf-8") as config_file:
                    loaded_values = json.load(config_file)
            except FileNotFoundError as error:
                raise ValueError(f"配置文件不存在: {resolved_config_path}") from error
            except json.JSONDecodeError as error:
                raise ValueError(f"配置文件不是有效的 JSON: {resolved_config_path}") from error
            if not isinstance(loaded_values, dict):
                raise ValueError("配置文件的顶层必须是 JSON 对象")
            file_values = loaded_values
            config_directory = resolved_config_path.parent

        def configured_value(config_key: str, environment_key: str, default: Any = None) -> Any:
            if environment_key in values:
                return values[environment_key]
            return file_values.get(config_key, default)

        room_value = configured_value("room_ids", "BILIBILI_ROOM_IDS")
        room_ids = _parse_room_ids(room_value, "BILIBILI_ROOM_IDS")

        def integer_value(config_key: str, environment_key: str, default: int, label: str) -> int:
            raw_value = configured_value(config_key, environment_key, default)
            if isinstance(raw_value, bool):
                raise ValueError(f"{label} must be an integer")
            try:
                return int(raw_value)
            except (TypeError, ValueError) as error:
                raise ValueError(f"{label} must be an integer") from error

        def positive_float_value(config_key: str, environment_key: str, default: float, label: str) -> float:
            raw_value = configured_value(config_key, environment_key, default)
            try:
                parsed_value = float(raw_value)
            except (TypeError, ValueError) as error:
                raise ValueError(f"{label} must be a positive number") from error
            if not math.isfinite(parsed_value) or parsed_value <= 0:
                raise ValueError(f"{label} must be a positive number")
            return parsed_value

        membership_logging = _parse_boolean(
            configured_value("membership_logging", "BILIBILI_MEMBERSHIP_LOGGING", False),
            "BILIBILI_MEMBERSHIP_LOGGING",
        )

        port = integer_value("port", "GIFTBOOK_PORT", 8080, "GIFTBOOK_PORT")
        replay_size = integer_value("replay_size", "GIFTBOOK_REPLAY_SIZE", 256, "GIFTBOOK_REPLAY_SIZE")
        subscriber_queue_size = integer_value(
            "subscriber_queue_size",
            "GIFTBOOK_SUBSCRIBER_QUEUE_SIZE",
            64,
            "GIFTBOOK_SUBSCRIBER_QUEUE_SIZE",
        )
        host = str(configured_value("host", "GIFTBOOK_HOST", "127.0.0.1") or "").strip()
        if not host:
            raise ValueError("GIFTBOOK_HOST must not be empty")

        if not replay_size > 0:
            raise ValueError("GIFTBOOK_REPLAY_SIZE must be positive")
        if not subscriber_queue_size > 0:
            raise ValueError("GIFTBOOK_SUBSCRIBER_QUEUE_SIZE must be positive")
        if not 1 <= port <= 65535:
            raise ValueError("GIFTBOOK_PORT must be between 1 and 65535")

        configured_frontend_root = frontend_root
        if configured_frontend_root is None:
            configured_frontend_value = configured_value("frontend_root", "GIFTBOOK_FRONTEND_ROOT")
            if configured_frontend_value is None or not str(configured_frontend_value).strip():
                configured_frontend_root = DEFAULT_FRONTEND_ROOT
            else:
                configured_frontend_root = Path(str(configured_frontend_value)).expanduser()
                if not configured_frontend_root.is_absolute():
                    configured_frontend_root = config_directory / configured_frontend_root

        return cls(
            room_ids=room_ids,
            sessdata=str(configured_value("sessdata", "BILIBILI_SESSDATA", "") or ""),
            membership_logging=membership_logging,
            host=host,
            port=port,
            frontend_root=Path(configured_frontend_root).resolve(),
            replay_size=replay_size,
            subscriber_queue_size=subscriber_queue_size,
            bilibili_heartbeat_seconds=positive_float_value(
                "bilibili_heartbeat_seconds",
                "BILIBILI_HEARTBEAT_SECONDS",
                30.0,
                "BILIBILI_HEARTBEAT_SECONDS",
            ),
            websocket_heartbeat_seconds=positive_float_value(
                "websocket_heartbeat_seconds",
                "GIFTBOOK_WS_HEARTBEAT_SECONDS",
                30.0,
                "GIFTBOOK_WS_HEARTBEAT_SECONDS",
            ),
            subscription_timeout_seconds=positive_float_value(
                "subscription_timeout_seconds",
                "GIFTBOOK_SUBSCRIBE_TIMEOUT_SECONDS",
                10.0,
                "GIFTBOOK_SUBSCRIBE_TIMEOUT_SECONDS",
            ),
        )

    @classmethod
    def from_file(cls, path: Path, *, environ: Optional[Mapping[str, str]] = None) -> "BridgeConfig":
        """Load a startup JSON file, allowing environment variables to override it."""

        return cls.from_env(environ, config_path=path)


async def _send_event(ws: web.WebSocketResponse, event: Mapping[str, Any]) -> None:
    await ws.send_json({"type": "event", "event": dict(event)})


async def _send_subscriber_events(ws: web.WebSocketResponse, subscriber: EventSubscriber) -> None:
    async for event in subscriber:
        await _send_event(ws, event)


def _parse_room_ids(raw_value: Any, label: str) -> Tuple[int, ...]:
    """Parse a JSON room-id array or a comma-separated environment value."""

    if raw_value is None:
        return ()
    if isinstance(raw_value, str):
        if not raw_value.strip():
            return ()
        raw_values: List[Any] = raw_value.split(",")
    elif isinstance(raw_value, (list, tuple)):
        raw_values = list(raw_value)
    else:
        raise ValueError(f"{label} must be a JSON array or comma-separated values")

    room_ids = []
    seen = set()
    for raw_room_id in raw_values:
        if isinstance(raw_room_id, bool):
            raise ValueError(f"{label} must contain positive integer IDs")
        if isinstance(raw_room_id, int):
            room_id = raw_room_id
        elif isinstance(raw_room_id, str) and raw_room_id.strip():
            try:
                room_id = int(raw_room_id.strip())
            except ValueError as error:
                raise ValueError(f"{label} must contain positive integer IDs") from error
        else:
            raise ValueError(f"{label} must contain positive integer IDs")
        if room_id <= 0:
            raise ValueError(f"{label} must contain positive integer IDs")
        if room_id in seen:
            raise ValueError(f"{label} must not contain duplicate IDs")
        seen.add(room_id)
        room_ids.append(room_id)
    return tuple(room_ids)


def _parse_boolean(raw_value: Any, label: str) -> bool:
    """Parse JSON booleans and the compact environment forms 1/0 and true/false."""

    if isinstance(raw_value, bool):
        return raw_value
    if isinstance(raw_value, int) and raw_value in (0, 1):
        return bool(raw_value)
    if isinstance(raw_value, str):
        normalized = raw_value.strip().casefold()
        if normalized in {"true", "1"}:
            return True
        if normalized in {"false", "0"}:
            return False
    raise ValueError(f"{label} must be true/false or 1/0")


def _subscription_from_payload(payload: Any, config: BridgeConfig) -> tuple[Any, int, Optional[int]]:
    if not isinstance(payload, dict) or payload.get("type") != "subscribe":
        raise ValueError("无效的订阅请求")
    if not config.room_ids:
        raise PermissionError("当前桥接服务尚未配置直播间")

    try:
        room_id = int(payload["roomId"])
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("缺少有效的直播间号") from error
    if room_id not in config.room_ids:
        raise PermissionError("该直播间未配置在当前桥接服务中")

    event_id = payload.get("eventId")
    if event_id in (None, ""):
        raise ValueError("缺少事项编号")

    last_seq_value = payload.get("lastSeq")
    if last_seq_value is None:
        return event_id, room_id, None
    if isinstance(last_seq_value, bool):
        raise ValueError("lastSeq 必须是整数")
    try:
        last_seq = int(last_seq_value)
    except (TypeError, ValueError) as error:
        raise ValueError("lastSeq 必须是整数") from error
    if last_seq < 0:
        raise ValueError("lastSeq 不能为负数")
    return event_id, room_id, last_seq


async def websocket_handler(request: web.Request) -> web.WebSocketResponse:
    config: BridgeConfig = request.app[BRIDGE_CONFIG_KEY]
    hub: EventHub = request.app[EVENT_HUB_KEY]
    ws = web.WebSocketResponse(heartbeat=config.websocket_heartbeat_seconds)
    await ws.prepare(request)

    try:
        first_message = await asyncio.wait_for(ws.receive(), timeout=config.subscription_timeout_seconds)
        if first_message.type != WSMsgType.TEXT:
            await ws.send_json({"type": "error", "message": "请先发送订阅请求。"})
            return ws
        try:
            payload = json.loads(first_message.data)
            event_id, room_id, last_seq = _subscription_from_payload(payload, config)
        except PermissionError as error:
            await ws.send_json({"type": "error", "message": str(error)})
            await ws.close(code=1008, message=str(error).encode("utf-8"))
            return ws
        except (ValueError, json.JSONDecodeError) as error:
            await ws.send_json({"type": "error", "message": str(error)})
            await ws.close(code=1008, message=str(error).encode("utf-8"))
            return ws

        subscriber = hub.subscribe(room_id)
        # Take the replay snapshot before the first await. The hub publisher is
        # synchronous, so no upstream event can interleave these two calls.
        replay = hub.replay_after(last_seq, room_id)
        replay_cutoff = hub.current_seq
        sender = None
        try:
            await ws.send_json(
                {
                    "type": "subscribed",
                    "roomId": room_id,
                    "eventId": event_id,
                    "lastSeq": hub.current_seq,
                }
            )
            if replay is RESYNC_REQUIRED:
                await ws.send_json({"type": "resync_required", "lastSeq": hub.current_seq})
            else:
                for event in replay:
                    await _send_event(ws, event)

            # Events published while the replay was being written remain in the
            # queue. Remove only events covered by the replay snapshot so the
            # sender starts at the next sequence without duplicate writes.
            subscriber.discard_through(replay_cutoff)
            if subscriber.closed:
                return ws
            sender = asyncio.create_task(_send_subscriber_events(ws, subscriber))

            async for message in ws:
                if message.type == WSMsgType.ERROR:
                    break
                if message.type == WSMsgType.TEXT:
                    try:
                        client_message = json.loads(message.data)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(client_message, dict) and client_message.get("type") == "ping":
                        await ws.send_json({"type": "pong"})
                elif message.type in (WSMsgType.CLOSE, WSMsgType.CLOSED, WSMsgType.CLOSING):
                    break
        finally:
            if sender is not None:
                sender.cancel()
                await asyncio.gather(sender, return_exceptions=True)
            hub.unsubscribe(subscriber)
    except asyncio.TimeoutError:
        await ws.send_json({"type": "error", "message": "订阅请求超时。"})
        await ws.close(code=1008, message="subscription timeout".encode("utf-8"))
    finally:
        if not ws.closed:
            await ws.close()
    return ws


async def _start_live_source(app: web.Application) -> None:
    await app[LIVE_SOURCE_KEY].start()


async def _stop_live_source(app: web.Application) -> None:
    await app[LIVE_SOURCE_KEY].stop()


async def index_handler(request: web.Request) -> web.FileResponse:
    return web.FileResponse(request.app[FRONTEND_ROOT_KEY] / "index.html")


async def guest_screen_handler(request: web.Request) -> web.FileResponse:
    return web.FileResponse(request.app[FRONTEND_ROOT_KEY] / "guest-screen.html")


async def bridge_config_handler(request: web.Request) -> web.Response:
    config: BridgeConfig = request.app[BRIDGE_CONFIG_KEY]
    return web.json_response(
        {
            "version": 3,
            "roomIds": list(config.room_ids),
            "membershipLogging": config.membership_logging,
        }
    )


def create_app(
    config: Optional[BridgeConfig] = None,
    *,
    event_hub: Optional[EventHub] = None,
    live_source: Optional[Any] = None,
) -> web.Application:
    config = config or BridgeConfig.from_env()
    hub = event_hub or EventHub(config.replay_size, config.subscriber_queue_size)
    source = live_source or LiveSource(
        config.room_ids,
        config.sessdata,
        hub.publish,
        membership_logging=config.membership_logging,
        heartbeat_interval=config.bilibili_heartbeat_seconds,
    )
    frontend_root = config.frontend_root.resolve()

    app = web.Application()
    app[BRIDGE_CONFIG_KEY] = config
    app[EVENT_HUB_KEY] = hub
    app[LIVE_SOURCE_KEY] = source
    app[FRONTEND_ROOT_KEY] = frontend_root
    app.router.add_get("/", index_handler)
    app.router.add_get("/guest-screen.html", guest_screen_handler)
    app.router.add_get("/bridge-config", bridge_config_handler)
    app.router.add_get("/ws", websocket_handler)
    app.router.add_static("/static/", str(frontend_root / "static"), name="static")
    app.on_startup.append(_start_live_source)
    app.on_cleanup.append(_stop_live_source)
    return app


__all__ = (
    "BridgeConfig",
    "create_app",
    "websocket_handler",
)
