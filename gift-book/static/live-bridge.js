(function (global) {
  "use strict";

  const MAX_RECONNECT_DELAY = 30_000;
  const MAX_SEEN_EVENTS = 2_048;

  /**
   * Same-origin WebSocket client for normalized live events.
   *
   * The bridge intentionally owns transport concerns only. Application code
   * subscribes to normalized event types through `on()` and never sees a
   * Bilibili protocol payload.
   */
  class LiveBridge {
    constructor(options = {}) {
      this.WebSocket = options.WebSocket || global.WebSocket;
      this.location = options.location || global.location;
      this.setTimeout = options.setTimeout || global.setTimeout;
      this.clearTimeout = options.clearTimeout || global.clearTimeout;
      this.listeners = new Map();
      this.socket = null;
      this.reconnectTimer = null;
      this.reconnectAttempt = 0;
      this.lastSeq = null;
      this.seenEventKeys = new Set();
      this.generation = 0;
      this.manualDisconnect = true;
      this.roomId = null;
      this.eventId = null;
    }

    on(type, callback) {
      if (typeof callback !== "function") return () => {};
      if (!this.listeners.has(type)) this.listeners.set(type, new Set());
      this.listeners.get(type).add(callback);
      return () => this.listeners.get(type)?.delete(callback);
    }

    connect({ roomId, eventId } = {}) {
      this.disconnect();
      if (roomId === undefined || roomId === null || eventId === undefined || eventId === null) {
        this.emit("status", { state: "error", message: "缺少直播间或事项信息。" });
        return this;
      }

      this.roomId = roomId;
      this.eventId = eventId;
      this.lastSeq = null;
      this.seenEventKeys.clear();
      this.reconnectAttempt = 0;
      this.manualDisconnect = false;
      this.generation += 1;
      this.openSocket(this.generation);
      return this;
    }

    disconnect() {
      this.manualDisconnect = true;
      this.generation += 1;
      this.clearReconnectTimer();
      const socket = this.socket;
      this.socket = null;
      if (socket) {
        try {
          socket.close();
        } catch (error) {
          console.debug("关闭礼簿直播连接失败", error);
        }
      }
      this.emit("status", { state: "disconnected" });
      return this;
    }

    openSocket(generation) {
      if (this.manualDisconnect || generation !== this.generation) return;

      if (typeof this.WebSocket !== "function" || !this.location?.host) {
        this.emit("status", { state: "unavailable", message: "当前页面未连接到礼簿桥接服务。" });
        return;
      }

      const protocol = this.location.protocol === "https:" ? "wss:" : "ws:";
      const url = `${protocol}//${this.location.host}/ws`;
      this.emit("status", { state: "connecting" });

      let socket;
      try {
        socket = new this.WebSocket(url);
      } catch (error) {
        this.emit("status", { state: "error", message: "礼簿桥接连接失败。", error });
        this.scheduleReconnect(generation);
        return;
      }

      this.socket = socket;
      socket.onopen = () => {
        if (generation !== this.generation) return;
        this.reconnectAttempt = 0;
        socket.send(
          JSON.stringify({
            type: "subscribe",
            roomId: this.roomId,
            eventId: this.eventId,
            lastSeq: this.lastSeq,
          })
        );
        this.emit("status", { state: "connected", roomId: this.roomId, eventId: this.eventId });
      };

      socket.onmessage = (message) => {
        if (generation !== this.generation) return;
        this.handleMessage(message.data);
      };

      socket.onerror = (error) => {
        if (generation === this.generation) {
          this.emit("status", { state: "error", message: "礼簿桥接连接异常。", error });
        }
      };

      socket.onclose = () => {
        if (generation !== this.generation) return;
        this.socket = null;
        if (!this.manualDisconnect) {
          this.emit("status", { state: "disconnected" });
          this.scheduleReconnect(generation);
        }
      };
    }

    handleMessage(rawMessage) {
      let payload;
      try {
        payload = typeof rawMessage === "string" ? JSON.parse(rawMessage) : rawMessage;
      } catch (error) {
        this.emit("status", { state: "error", message: "礼簿桥接消息格式无效。", error });
        return;
      }

      if (!payload || typeof payload !== "object") return;
      if (payload.type === "event" && payload.event) {
        this.dispatchEvent(payload.event);
      } else if (payload.type === "subscribed") {
        const subscribedSeq = Number(payload.lastSeq);
        if (Number.isFinite(subscribedSeq)) this.lastSeq = subscribedSeq;
      } else if (payload.type === "resync_required") {
        const resyncSeq = Number(payload.lastSeq);
        if (Number.isFinite(resyncSeq)) this.lastSeq = resyncSeq;
        this.emit("status", { state: "resync_required", lastSeq: this.lastSeq });
      } else if (payload.type === "error") {
        this.emit("status", { state: "error", message: payload.message || "礼簿桥接服务返回错误。" });
      }
    }

    dispatchEvent(event) {
      if (!event || typeof event !== "object" || !event.type) return;

      const sourceKey = event.sourceKey || `${event.roomId}:${event.messageId}`;
      const seq = Number(event.seq);
      if (Number.isFinite(seq)) this.lastSeq = this.lastSeq === null ? seq : Math.max(this.lastSeq, seq);
      // Created and deleted are two lifecycle events for one sourceKey. Keep
      // their deduplication keys separate so a real deletion is not swallowed.
      const dedupeKey = `${event.type}:${sourceKey}`;
      if (this.seenEventKeys.has(dedupeKey)) return;
      this.seenEventKeys.add(dedupeKey);
      if (this.seenEventKeys.size > MAX_SEEN_EVENTS) {
        const oldest = this.seenEventKeys.values().next().value;
        this.seenEventKeys.delete(oldest);
      }

      this.emit(event.type, event);
      this.emit("event", event);
    }

    scheduleReconnect(generation) {
      if (this.manualDisconnect || generation !== this.generation || this.reconnectTimer) return;
      const delay = Math.min(1_000 * 2 ** this.reconnectAttempt, MAX_RECONNECT_DELAY);
      this.reconnectAttempt += 1;
      this.reconnectTimer = this.setTimeout(() => {
        this.reconnectTimer = null;
        this.openSocket(generation);
      }, delay);
    }

    clearReconnectTimer() {
      if (this.reconnectTimer) {
        this.clearTimeout(this.reconnectTimer);
        this.reconnectTimer = null;
      }
    }

    emit(type, payload) {
      const callbacks = this.listeners.get(type);
      if (!callbacks) return;
      callbacks.forEach((callback) => {
        try {
          callback(payload);
        } catch (error) {
          console.error(`礼簿直播事件处理失败: ${type}`, error);
        }
      });
    }
  }

  global.LiveBridge = LiveBridge;
})(window);
