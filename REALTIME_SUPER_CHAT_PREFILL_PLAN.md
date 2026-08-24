# Real-Time Super Chat Logging Plan

## Objective

When an operator unlocks a `事项` and enters the `礼金录入` page, incoming Bilibili Super Chat messages should be committed to the `贺礼` table automatically in real time.

The live path must bypass the `礼金录入` form and confirmation modal. Manual entries continue to use the existing review-and-submit workflow.

## Current constraints

- `blivedm` already parses web-platform `SUPER_CHAT_MESSAGE` and `SUPER_CHAT_MESSAGE_DELETE` events.
- `gift-book` is a static browser application.
- Gift records are encrypted and stored in browser IndexedDB.
- `guest-screen.html` is updated by the main page through `postMessage`.
- The browser must not receive Bilibili credentials or connect directly to Bilibili.

## Target data flow

```text
Bilibili WebSocket
    -> blivedm BLiveClient
    -> bridge handler
    -> normalized event hub
    -> same-origin aiohttp /ws
    -> frontend live client
    -> SuperChatLogger module
    -> existing automatic save workflow
    -> 贺礼 table
```

## Repository shape

Add an application-level Python bridge while keeping `blivedm` reusable:

```text
giftbook/
├── blivedm/
├── gift-book/
│   └── static/
│       └── live-bridge.js
├── giftbook_bridge/
│   ├── __init__.py
│   ├── __main__.py
│   ├── live_source.py
│   ├── event_hub.py
│   └── web_server.py
├── pyproject.toml
└── REALTIME_SUPER_CHAT_PREFILL_PLAN.md
```

The root Python project should use the existing `blivedm` project as a local dependency. Do not copy or reimplement its Bilibili protocol code.

## Phase 1: Define the event interface

Create a versioned normalized event format. The frontend should not depend on `blivedm` dataclass details.

### Created event

```json
{
  "version": 1,
  "seq": 42,
  "type": "super_chat.created",
  "sourceKey": "bilibili:123456:super-chat:987654",
  "roomId": 123456,
  "messageId": 987654,
  "userName": "张三",
  "amount": 30,
  "message": "新婚快乐",
  "startTime": 1787380000,
  "endTime": 1787380300
}
```

### Deleted event

```json
{
  "version": 1,
  "seq": 43,
  "type": "super_chat.deleted",
  "sourceKey": "bilibili:123456:super-chat:987654",
  "roomId": 123456,
  "messageId": 987654
}
```

`sourceKey` is the idempotency key. It must include the room ID and Bilibili message ID; never deduplicate by username or amount.

## Phase 2: Implement the Python bridge

### `giftbook_bridge/live_source.py`

Implement a `blivedm.BaseHandler` adapter:

- `_on_super_chat()` converts `web_models.SuperChatMessage` to `super_chat.created`.
- `_on_super_chat_delete()` converts each deleted ID to `super_chat.deleted`.
- The callbacks only enqueue/publish events synchronously with `put_nowait()`.
- No browser, database, or slow network operation may run inside the handler callback.

Start one `BLiveClient` for the configured room during application startup and stop it during application cleanup.

### `giftbook_bridge/event_hub.py`

Implement a deep event-hub module with a small interface:

```python
publish(event) -> None
subscribe() -> subscriber
unsubscribe(subscriber) -> None
replay_after(seq) -> list[events] | resync_required
```

Responsibilities:

- Assign monotonically increasing sequence numbers.
- Broadcast through one queue per browser connection.
- Keep a bounded replay buffer for short browser reconnects.
- Avoid blocking the Bilibili receive coroutine.
- Remove disconnected subscribers.

### `giftbook_bridge/web_server.py`

Use `aiohttp` to serve both frontend and bridge:

- `/` serves `gift-book/index.html`.
- `/guest-screen.html` serves the second-screen page.
- `/static/*` serves frontend assets.
- `/ws` accepts browser WebSocket connections.

The browser should connect to the same origin using `ws://` or `wss://` derived from `location.protocol`.

The client handshake should support:

```json
{
  "type": "subscribe",
  "roomId": 123456,
  "eventId": 7,
  "lastSeq": 41
}
```

The server should reject or ignore events for a room that is not configured for the active `事项`.

Configuration should be supplied through environment variables or a local configuration file that is not committed:

```text
BILIBILI_ROOM_ID=123456
BILIBILI_SESSDATA=...
GIFTBOOK_HOST=127.0.0.1
GIFTBOOK_PORT=8080
GIFTBOOK_FRONTEND_ROOT=./gift-book
GIFTBOOK_REPLAY_SIZE=256
GIFTBOOK_SUBSCRIBER_QUEUE_SIZE=64
BILIBILI_HEARTBEAT_SECONDS=30
GIFTBOOK_WS_HEARTBEAT_SECONDS=30
GIFTBOOK_SUBSCRIBE_TIMEOUT_SECONDS=10
```

The unified processor reads these values once before startup. A JSON file may be
passed with `python -m giftbook_bridge --config giftbook.config.local.json`, or
selected with `GIFTBOOK_CONFIG_FILE`; `giftbook.config.local.json` in the working
directory is discovered automatically. Environment variables override JSON
values. If no room is configured, the local GiftBook UI still starts without the
Bilibili source. The resulting `BridgeConfig` is immutable for the lifetime of
the process.

## Phase 3: Implement the frontend live client

Add `gift-book/static/live-bridge.js`.

Its public interface should remain small:

```javascript
connect({ roomId, eventId })
on(type, callback)
disconnect()
```

Responsibilities:

- Connect to `/ws` after `startSession()` successfully loads a `事项`.
- Send the active event and room subscription.
- Reconnect with bounded exponential backoff.
- Send the last received sequence number after reconnecting.
- Deduplicate replayed events using `sourceKey`.
- Dispatch only normalized events to the application.
- Disconnect when `showSetupScreen()` leaves the active `事项`.

Do not connect the second-screen page directly to `/ws`. The main page already owns the `guestScreenService` and can continue forwarding committed display data to `guest-screen.html`.

## Phase 4: Implement automatic gift logging

Add a `SuperChatLogger` module to the main frontend application.

### Behavior

1. Ignore events when no `currentEvent` is active.
2. Ignore events for another room.
3. Convert each valid created event into a gift record and save it immediately.
4. Serialize incoming writes so records remain in event order.
5. Do not write to, reset, or submit the manual `礼金录入` form.
6. Use `sourceKey` as the idempotency key across reconnects and page sessions.
7. Show a small status notification when an event is automatically logged or fails.

### Field mapping

```text
Super Chat userName  -> giftData.name
Super Chat amount    -> giftData.amount
payment type         -> giftData.type = 其他
Super Chat message   -> giftData.remarkData.custom
sourceKey            -> giftData.sourceKey
```

The existing `handleAddGift()` confirmation flow remains the operator-controlled path. The live path calls the same encrypted `saveGift()` persistence seam with an `automatic` option, which skips manual modal/form side effects and the out-of-time password prompt.

## Phase 5: Configure room ownership per 事项

Choose one of these configurations:

### Single-room deployment

Use `BILIBILI_ROOM_ID` globally. This is the smallest implementation and is appropriate if one running instance handles one livestream.

### Per-事项 deployment

Add an optional `bilibiliRoomId` field to the `events` IndexedDB records and the create/edit UI. The active `事项` sends that ID in the WebSocket subscription.

Prefer the single-room deployment first unless multiple simultaneous rooms are a requirement.

## Phase 6: Handle deletion events safely

For a `super_chat.deleted` event:

- Remove the message from the pending queue if it has not been saved.
- If it has already been saved, show a warning but do not silently delete the gift record.
- Do not automatically delete or abolish a saved gift record.

A Bilibili deletion is a live-message lifecycle event, not necessarily a confirmed financial reversal.

## Phase 7: Verification

Verify through the public module interfaces rather than testing implementation details.

### Backend checks

- A fake `SuperChatMessage` produces the expected normalized event.
- A fake deletion message produces the expected deletion event.
- Multiple subscribers receive the same sequence-ordered event.
- Reconnect replay does not duplicate events.
- A slow browser subscriber does not block Bilibili message handling.

### Frontend checks

- Entering a `事项` establishes the subscription.
- A Super Chat creates a `贺礼` table row with name, amount, type, and remark.
- The manual form and any open confirmation modal are not overwritten or reset.
- Multiple incoming events are saved in order.
- Replayed events do not create duplicate rows.
- Leaving the `事项` disconnects the live client.
- Deleted events do not silently erase saved gift records.

### Manual acceptance scenario

1. Start the bridge with a test room.
2. Open the served gift-book page.
3. Create or unlock a `事项`.
4. Confirm the WebSocket status is connected.
5. Send a real or injected Super Chat.
6. Confirm a `贺礼` row appears without a confirmation dialog or form interaction.
7. Confirm the existing gift-book list, totals, encryption, and second screen still behave normally.
8. Send another Super Chat and confirm it creates the next row.

## Non-goals for the first implementation

- Automatic deletion or abolition of financial records.
- Direct browser-to-Bilibili communication.
- Multi-room fan-out in one process.
- Replacing the existing IndexedDB storage model.
