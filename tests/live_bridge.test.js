const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

function loadBrowserScript(fileName, extra = {}) {
  const source = fs.readFileSync(path.join(__dirname, "..", "gift-book", "static", fileName), "utf8");
  const window = { console, ...extra };
  vm.runInNewContext(source, { window, console, ...extra });
  return window;
}

test("LiveBridge uses null for fresh pairing and the prior sequence on reconnect", () => {
  const sockets = [];
  const timers = [];
  class FakeWebSocket {
    constructor() {
      this.sent = [];
      sockets.push(this);
    }
    send(payload) {
      this.sent.push(JSON.parse(payload));
    }
    close() {
      this.onclose?.();
    }
    open() {
      this.onopen?.();
    }
    message(payload) {
      this.onmessage?.({ data: JSON.stringify(payload) });
    }
    closeUnexpectedly() {
      this.onclose?.();
    }
  }

  const window = loadBrowserScript("live-bridge.js", {
    WebSocket: FakeWebSocket,
    location: { protocol: "http:", host: "localhost" },
    setTimeout: (callback) => {
      timers.push(callback);
      return timers.length;
    },
    clearTimeout: () => {},
  });
  const bridge = new window.LiveBridge();
  bridge.connect({ roomId: 101, eventId: 1 });
  sockets[0].open();
  assert.equal(sockets[0].sent[0].lastSeq, null);
  sockets[0].message({ type: "subscribed", roomId: 101, eventId: 1, lastSeq: 4 });
  sockets[0].message({ type: "event", event: { type: "super_chat.created", roomId: 101, sourceKey: "k", seq: 5 } });
  sockets[0].closeUnexpectedly();
  timers.shift()();
  sockets[1].open();
  assert.equal(sockets[1].sent[0].lastSeq, 5);
});

test("SuperChatSaver durably checks source keys and withdraws matching records", async () => {
  const savedKeys = new Set();
  const notifications = [];
  const withdrawn = [];
  const window = loadBrowserScript("super-chat-saver.js");
  const saver = new window.SuperChatSaver({
    getCurrentEvent: () => ({ id: 1 }),
    getRoomId: () => 101,
    hasSourceKey: async (sourceKey) => savedKeys.has(sourceKey),
    saveEvent: async (event) => {
      savedKeys.add(event.sourceKey);
      return true;
    },
    withdrawSource: async (sourceKey) => {
      withdrawn.push(sourceKey);
      return true;
    },
    notify: (message) => notifications.push(message),
  });
  saver.setContext({ eventId: 1, roomId: 101 });

  const event = { type: "super_chat.created", roomId: 101, sourceKey: "bilibili:101:super-chat:7", userName: "张三", amount: 20, message: "贺喜" };
  assert.equal(await saver.handleEvent(event), true);
  assert.equal(await saver.handleEvent(event), false);
  assert.equal(notifications.includes("重复的醒目留言已忽略。"), true);
  assert.equal(await saver.handleEvent({ type: "super_chat.deleted", roomId: 101, sourceKey: event.sourceKey }), true);
  assert.deepEqual(withdrawn, [event.sourceKey]);
});
