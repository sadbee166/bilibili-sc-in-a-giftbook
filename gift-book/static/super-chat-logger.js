(function (global) {
  "use strict";

  const MAX_TRACKED_KEYS = 2_048;

  /**
   * Turns normalized Super Chat events into committed gift records.
   *
   * This module deliberately has no form or modal dependencies. The ordinary
   * form remains available for manual entries while live events use the same
   * application save path through the injected callback.
   */
  class SuperChatLogger {
    constructor(options = {}) {
      this.getCurrentEvent = options.getCurrentEvent || (() => null);
      this.getRoomId = options.getRoomId || (() => null);
      this.saveGift = options.saveGift || (async () => false);
      this.hasSavedSourceKey = options.hasSavedSourceKey || (() => false);
      this.notify = options.notify || (() => {});
      this.pending = [];
      this.seenSourceKeys = new Set();
      this.deletedSourceKeys = new Set();
      this.context = null;
      this.processing = false;
      this.processingSourceKey = null;
    }

    setContext({ eventId, roomId } = {}) {
      this.context = { eventId, roomId };
      this.pending = [];
      this.seenSourceKeys.clear();
      this.deletedSourceKeys.clear();
      this.processingSourceKey = null;
    }

    clear() {
      this.context = null;
      this.pending = [];
      this.seenSourceKeys.clear();
      this.deletedSourceKeys.clear();
      this.processingSourceKey = null;
    }

    get pendingCount() {
      return this.pending.length;
    }

    handleEvent(event) {
      if (!event || !this.getCurrentEvent()) return false;
      if (!this.isConfiguredRoom(event.roomId)) return false;
      if (event.type === "super_chat.created") return this.handleCreated(event);
      if (event.type === "super_chat.deleted") return this.handleDeleted(event);
      return false;
    }

    handleCreated(event) {
      const sourceKey = this.sourceKeyFor(event);
      if (!sourceKey || this.deletedSourceKeys.has(sourceKey)) return false;
      if (this.seenSourceKeys.has(sourceKey) || this.hasSavedSourceKey(sourceKey)) return false;

      const normalized = this.normalizeCreatedEvent(event);
      if (!normalized) return false;

      this.remember(this.seenSourceKeys, sourceKey);
      this.pending.push(normalized);
      void this.processPending();
      return true;
    }

    handleDeleted(event) {
      const sourceKey = this.sourceKeyFor(event);
      if (!sourceKey) return false;
      this.remember(this.deletedSourceKeys, sourceKey);

      const oldLength = this.pending.length;
      this.pending = this.pending.filter((item) => this.sourceKeyFor(item) !== sourceKey);
      if (this.pending.length !== oldLength) {
        this.notify("一条醒目留言已撤回，未写入礼簿。", "info");
        return true;
      }

      if (this.processingSourceKey === sourceKey || this.seenSourceKeys.has(sourceKey) || this.hasSavedSourceKey(sourceKey)) {
        this.notify("已自动录入的醒目留言已撤回，系统未自动删除礼簿记录。", "error");
        return true;
      }
      return false;
    }

    async processPending() {
      if (this.processing) return;
      this.processing = true;

      try {
        while (this.pending.length > 0) {
          const event = this.pending.shift();
          const sourceKey = this.sourceKeyFor(event);
          if (!sourceKey || this.deletedSourceKeys.has(sourceKey)) continue;
          if (!this.getCurrentEvent() || !this.isConfiguredRoom(event.roomId)) continue;

          this.processingSourceKey = sourceKey;
          try {
            const result = await this.saveGift(this.toGiftData(event), { automatic: true });
            if (result === false) {
              this.seenSourceKeys.delete(sourceKey);
              this.notify("醒目留言自动录入失败，请检查礼簿状态。", "error");
            } else {
              this.notify("醒目留言已自动录入礼簿。", "success");
            }
          } catch (error) {
            this.seenSourceKeys.delete(sourceKey);
            console.error("醒目留言自动录入失败:", error);
            this.notify("醒目留言自动录入失败，请检查礼簿状态。", "error");
          } finally {
            this.processingSourceKey = null;
          }
        }
      } finally {
        this.processing = false;
      }
    }

    isConfiguredRoom(roomId) {
      const activeRoomId = this.getRoomId();
      if (activeRoomId === null || activeRoomId === undefined || activeRoomId === "") return false;
      return String(roomId) === String(activeRoomId);
    }

    normalizeCreatedEvent(event) {
      const name = String(event.userName || "").trim();
      const amount = Number(event.amount);
      if (!name || !Number.isFinite(amount) || amount < 0) return null;
      return {
        ...event,
        userName: name,
        amount,
        message: String(event.message || "").trim(),
      };
    }

    toGiftData(event) {
      const sourceKey = this.sourceKeyFor(event);
      const remarkData = event.message ? { custom: event.message } : {};
      return {
        name: event.userName,
        amount: event.amount,
        type: "其他",
        remarkData,
        sourceKey,
      };
    }

    sourceKeyFor(event) {
      if (!event) return "";
      if (event.sourceKey) return String(event.sourceKey);
      if (event.messageId === undefined || event.messageId === null) return "";
      return `bilibili:${event.roomId}:super-chat:${event.messageId}`;
    }

    remember(set, sourceKey) {
      set.add(sourceKey);
      if (set.size > MAX_TRACKED_KEYS) {
        const oldest = set.values().next().value;
        set.delete(oldest);
      }
    }
  }

  global.SuperChatLogger = SuperChatLogger;
})(window);
