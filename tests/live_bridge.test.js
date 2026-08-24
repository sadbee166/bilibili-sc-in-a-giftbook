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

test("LiveEventSaver opt-in membership records are normalized and durably deduplicated", async () => {
  const saved = new Map();
  const notifications = [];
  const window = loadBrowserScript("super-chat-saver.js");
  const saver = new window.LiveEventSaver({
    getCurrentEvent: () => ({ id: 1 }),
    getRoomId: () => 101,
    membershipLogging: true,
    hasSourceKey: async (sourceKey) => saved.has(sourceKey),
    saveEvent: async (event) => {
      saved.set(event.sourceKey, event);
      return true;
    },
    notify: (message, type) => notifications.push({ message, type }),
  });
  saver.setContext({ eventId: 1, roomId: 101 });

  const event = {
    type: "membership.created",
    roomId: 101,
    sourceKey: "bilibili:101:membership:42:3:10:20:1990:2:1",
    userId: 42,
    userName: "舰长用户",
    guardLevel: 3,
    quantity: 2,
    unit: "月",
    amount: 3.98,
    unitAmount: 1.99,
    price: 1990,
    source: 1,
    startTime: 10,
    endTime: 20,
    toastText: "舰长用户开通了舰长",
    giftId: 1001,
  };

  assert.equal(await saver.handleEvent(event), true);
  assert.equal(saved.get(event.sourceKey).sourceMetadata.sourceType, "membership");
  assert.equal(saved.get(event.sourceKey).sourceMetadata.price, 1990);
  assert.equal(await saver.handleEvent(event), false);
  assert.equal(notifications.some(({ message }) => message === "B站会员已自动保存。"), true);
  assert.equal(notifications.some(({ message }) => message === "重复的B站会员记录已忽略。"), true);

  saver.setMembershipLogging(false);
  assert.equal(await saver.handleEvent({ ...event, sourceKey: "disabled" }), false);
});

test("LiveEventSaver ignores membership events for unpaired or different rooms", async () => {
  const window = loadBrowserScript("super-chat-saver.js");
  const saver = new window.SuperChatSaver({
    getCurrentEvent: () => ({ id: 1 }),
    getRoomId: () => null,
    membershipLogging: true,
    saveEvent: async () => {
      throw new Error("must not save");
    },
  });
  saver.setContext({ eventId: 1, roomId: null });
  assert.equal(
    await saver.handleEvent({ type: "membership.created", roomId: 101, sourceKey: "unpaired", userName: "用户" }),
    false
  );
});
