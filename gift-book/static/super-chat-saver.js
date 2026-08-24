(function (global) {
  "use strict";

  const MAX_DELETED_KEYS = 1_024;

  /**
   * Persists normalized live events through GiftBook's encrypted save path.
   * The source key is checked against decrypted GiftBook data before saving so
   * reconnect replay cannot create a second record for the same event.
   */
  class LiveEventSaver {
    constructor(options = {}) {
      this.getCurrentEvent = options.getCurrentEvent || (() => null);
      this.getRoomId = options.getRoomId || (() => null);
      this.hasSourceKey = options.hasSourceKey || (async () => false);
      this.saveEvent = options.saveEvent || (async () => false);
      this.withdrawSource = options.withdrawSource || (async () => false);
      this.notify = options.notify || (() => {});
      this.membershipLogging = options.membershipLogging === true;
      this.context = null;
      this.inFlightSourceKeys = new Set();
      this.deletedSourceKeys = new Set();
    }

    setMembershipLogging(enabled) {
      this.membershipLogging = enabled === true;
    }

    setContext({ eventId, roomId } = {}) {
      this.context = { eventId, roomId };
      this.deletedSourceKeys.clear();
    }

    clear() {
      this.context = null;
      this.inFlightSourceKeys.clear();
      this.deletedSourceKeys.clear();
    }

    async handleEvent(event) {
      if (!event || !this.getCurrentEvent()) return false;
      if (this.isMembershipEvent(event) && !this.membershipLogging) return false;
      if (!this.isConfiguredRoom(event.roomId)) return false;
      if (event.type === "super_chat.created") return this.handleCreated(event);
      if (event.type === "super_chat.deleted") return this.handleDeleted(event);
      if (event.type === "membership.created") return this.handleCreated(event);
      return false;
    }

    async handleCreated(event) {
      const sourceKey = this.sourceKeyFor(event);
      if (!sourceKey || this.deletedSourceKeys.has(sourceKey) || this.inFlightSourceKeys.has(sourceKey)) return false;

      const normalized = this.normalizeCreatedEvent(event);
      if (!normalized) return false;
      const eventKind = this.eventKind(event);
      this.inFlightSourceKeys.add(sourceKey);
      try {
        if (!this.isCurrentContext()) return false;
        if (await this.hasSourceKey(sourceKey)) {
          this.notify(this.duplicateMessage(eventKind), "info");
          return false;
        }
        if (!this.isCurrentContext() || this.deletedSourceKeys.has(sourceKey)) return false;
        const saved = !!(await this.saveEvent(normalized));
        if (eventKind === "membership") {
          this.notify(saved ? "B站会员已自动保存。" : "B站会员自动保存失败。", saved ? "success" : "error");
        }
        return saved;
      } catch (error) {
        if (eventKind === "membership") {
          console.error("B站会员自动保存失败:", error);
          this.notify("B站会员自动保存失败。", "error");
        }
        return false;
      } finally {
        this.inFlightSourceKeys.delete(sourceKey);
      }
    }

    async handleDeleted(event) {
      const sourceKey = this.sourceKeyFor(event);
      if (!sourceKey) return false;
      this.rememberDeleted(sourceKey);
      if (!this.isCurrentContext()) return false;

      const withdrawn = await this.withdrawSource(sourceKey);
      if (withdrawn) {
        this.notify("醒目留言已撤回，匹配记录已标记为作废。", "error");
      }
      return !!withdrawn;
    }

    isCurrentContext() {
      const event = this.getCurrentEvent();
      const roomId = this.getRoomId();
      return !!(
        event &&
        this.context &&
        String(event.id) === String(this.context.eventId) &&
        String(roomId) === String(this.context.roomId)
      );
    }

    isConfiguredRoom(roomId) {
      const activeRoomId = this.getRoomId();
      if (activeRoomId === null || activeRoomId === undefined || activeRoomId === "") return false;
      return String(roomId) === String(activeRoomId);
    }

    normalizeCreatedEvent(event) {
      if (this.isMembershipEvent(event)) return this.normalizeMembershipEvent(event);

      const name = String(event.userName || "").trim();
      const amount = Number(event.amount);
      const sourceKey = this.sourceKeyFor(event);
      if (!name || !sourceKey || !Number.isFinite(amount) || amount < 0) return null;
      return {
        sourceKey,
        sourceRoomId: Number(event.roomId),
        sourceState: "active",
        userName: name,
        amount,
        message: String(event.message || ""),
      };
    }

    normalizeMembershipEvent(event) {
      const name = String(event.userName || "").trim();
      const sourceKey = this.sourceKeyFor(event);
      const roomId = Number(event.roomId);
      const userId = Number(event.userId);
      const guardLevel = Number(event.guardLevel);
      const quantity = Number(event.quantity);
      const amount = Number(event.amount);
      const unitAmount = Number(event.unitAmount);
      const price = Number(event.price);
      const source = Number(event.source);
      if (
        !name ||
        !sourceKey ||
        !Number.isSafeInteger(roomId) ||
        !Number.isSafeInteger(userId) ||
        !Number.isSafeInteger(guardLevel) ||
        !Number.isSafeInteger(quantity) ||
        quantity < 1 ||
        !Number.isFinite(amount) ||
        amount < 0 ||
        !Number.isFinite(unitAmount) ||
        unitAmount < 0 ||
        !Number.isSafeInteger(price) ||
        price < 0 ||
        !Number.isSafeInteger(source)
      ) {
        return null;
      }

      const sourceMetadata = {
        sourceType: "membership",
        sourceKey,
        roomId,
        userId,
        userName: name,
        guardLevel,
        quantity,
        unit: String(event.unit || ""),
        amount,
        unitAmount,
        price,
        source,
        startTime: Number(event.startTime) || 0,
        endTime: Number(event.endTime) || 0,
        toastText: String(event.toastText || ""),
        giftId: Number(event.giftId) || 0,
      };
      return {
        ...event,
        sourceKey,
        sourceRoomId: roomId,
        sourceType: "membership",
        sourceState: "active",
        userName: name,
        amount,
        sourceMetadata,
      };
    }

    sourceKeyFor(event) {
      if (!event) return "";
      if (event.sourceKey) return String(event.sourceKey);
      if (this.isMembershipEvent(event)) {
        const fields = [
          Number(event.roomId),
          Number(event.userId),
          Number(event.guardLevel),
          Number(event.startTime),
          Number(event.endTime),
          Number(event.price),
          Number(event.quantity),
          Number(event.source),
        ];
        if (fields.every((value) => Number.isFinite(value))) return `bilibili:${fields[0]}:membership:${fields.slice(1).join(":")}`;
        return "";
      }
      if (event.messageId === undefined || event.messageId === null) return "";
      return `${event.roomId}:super-chat:${event.messageId}`;
    }

    isMembershipEvent(event) {
      return event?.type === "membership.created";
    }

    eventKind(event) {
      return this.isMembershipEvent(event) ? "membership" : "super_chat";
    }

    duplicateMessage(eventKind) {
      return eventKind === "membership" ? "重复的B站会员记录已忽略。" : "重复的醒目留言已忽略。";
    }

    rememberDeleted(sourceKey) {
      this.deletedSourceKeys.add(sourceKey);
      if (this.deletedSourceKeys.size > MAX_DELETED_KEYS) {
        const oldest = this.deletedSourceKeys.values().next().value;
        this.deletedSourceKeys.delete(oldest);
      }
    }
  }

  global.LiveEventSaver = LiveEventSaver;
  global.SuperChatSaver = LiveEventSaver;
})(window);
