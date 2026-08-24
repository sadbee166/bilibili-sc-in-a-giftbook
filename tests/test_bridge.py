import asyncio
import json
import tempfile
import unittest
from pathlib import Path

import aiohttp
from aiohttp.test_utils import TestClient, TestServer

from giftbook_bridge.event_hub import EventHub
from giftbook_bridge.live_source import LiveSource
from giftbook_bridge.web_server import BridgeConfig, create_app


class FakeClient:
    def __init__(self, room_id, **kwargs):
        self.room_id = room_id
        self.kwargs = kwargs
        self.handler = None
        self.started = False
        self.stopped = False

    def set_handler(self, handler):
        self.handler = handler

    def start(self):
        self.started = True

    async def stop_and_close(self):
        self.stopped = True


class FakeSource:
    async def start(self):
        return None

    async def stop(self):
        return None


class BridgeTests(unittest.IsolatedAsyncioTestCase):
    def test_multi_room_configuration_from_csv_and_json(self):
        config = BridgeConfig.from_env({"BILIBILI_ROOM_IDS": "123, 456"})
        self.assertEqual(config.room_ids, (123, 456))

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps({"room_ids": [123, 456]}), encoding="utf-8")
            config = BridgeConfig.from_file(path, environ={})
            self.assertEqual(config.room_ids, (123, 456))

        for value in ("1,1", "0", "-2", "abc", "true"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                BridgeConfig.from_env({"BILIBILI_ROOM_IDS": value})

    async def test_live_source_starts_and_cleans_one_client_per_room(self):
        created = []
        session = aiohttp.ClientSession()

        def client_factory(room_id, **kwargs):
            client = FakeClient(room_id, **kwargs)
            created.append(client)
            return client

        source = LiveSource((101, 202), "", lambda event: None, client_factory=client_factory, session_factory=lambda: session)
        try:
            await source.start()
            self.assertEqual(tuple(source.clients), (101, 202))
            self.assertTrue(all(client.started for client in created))
            self.assertIs(created[0].kwargs["session"], session)
            await source.stop()
            self.assertTrue(all(client.stopped for client in created))
            self.assertTrue(session.closed)
        finally:
            if not session.closed:
                await session.close()

    async def test_websocket_filters_rooms_and_distinguishes_fresh_from_reconnect(self):
        hub = EventHub(replay_size=16, subscriber_queue_size=8)
        app = create_app(
            BridgeConfig(room_ids=(101, 202), frontend_root=Path(__file__).parents[1] / "gift-book"),
            event_hub=hub,
            live_source=FakeSource(),
        )
        server = TestServer(app)
        client = TestClient(server)
        await client.start_server()
        try:
            response = await client.get("/bridge-config")
            self.assertEqual(await response.json(), {"version": 2, "roomIds": [101, 202]})

            hub.publish({"type": "super_chat.created", "roomId": 101, "sourceKey": "old-101"})
            ws = await client.ws_connect("/ws")
            await ws.send_json({"type": "subscribe", "roomId": 101, "eventId": 1, "lastSeq": None})
            subscribed = await ws.receive_json()
            self.assertEqual(subscribed["type"], "subscribed")
            self.assertEqual(subscribed["roomId"], 101)
            with self.assertRaises(asyncio.TimeoutError):
                await ws.receive(timeout=0.05)

            hub.publish({"type": "super_chat.created", "roomId": 202, "sourceKey": "new-202"})
            hub.publish({"type": "super_chat.created", "roomId": 101, "sourceKey": "new-101"})
            event_message = await ws.receive_json()
            self.assertEqual(event_message["event"]["sourceKey"], "new-101")
            last_seq = event_message["event"]["seq"]
            await ws.close()

            hub.publish({"type": "super_chat.created", "roomId": 202, "sourceKey": "missed-202"})
            hub.publish({"type": "super_chat.created", "roomId": 101, "sourceKey": "missed-101"})
            ws = await client.ws_connect("/ws")
            await ws.send_json({"type": "subscribe", "roomId": 101, "eventId": 1, "lastSeq": last_seq})
            await ws.receive_json()
            replayed = await ws.receive_json()
            self.assertEqual(replayed["event"]["sourceKey"], "missed-101")
            await ws.close()
        finally:
            await client.close()


if __name__ == "__main__":
    unittest.main()
