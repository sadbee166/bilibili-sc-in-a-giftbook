# GiftBook UI changes

| Element name | DOM ID/class | Source and method | Visible text and behavior | Change |
| --- | --- | --- | --- | --- |
| Live-room selector | `#edit-live-room-id` | `gift-book/index.html`, `GiftBookApp.showEditEventInfoModal()` / `populateLiveRoomSelector()` | Lists configured rooms numerically plus `未配对直播间`; the selected room is persisted with the current item. | New |
| Clear pairing control | `#clear-live-room-pairing` | `gift-book/index.html`, `GiftBookApp.showEditEventInfoModal()` | `清除配对`; immediately changes the selector to unpaired without requesting the password. | New |
| Pairing status | `#live-room-pairing-status` | `gift-book/index.html`, `GiftBookApp.updateLiveRoomPairingStatus()` | Explains whether the item is unpaired, paired to a configured room, or paired to a room absent from the current bridge configuration, and shows whether membership logging is enabled. | Modified |
| Live bridge status | `#live-bridge-status` | `gift-book/index.html`, `GiftBookApp.updateLiveBridgeStatus()` | Existing status text includes the active room, pairing state, and whether membership logging is enabled. | Modified |
| Automatic live-save notification | Existing notification elements created by `UIManager.showNotification()` | `gift-book/index.html`, `GiftBookApp.saveGift()` and `LiveEventSaver` (`SuperChatSaver` alias) | Shows existing Super Chat save/duplicate/withdrawal notifications plus `B站会员已自动保存。`, `重复的B站会员记录已忽略。`, or `B站会员自动保存失败。` using the existing notification styling. | Modified |
| Membership live records | Existing GiftBook ledger rows and encrypted gift payload | `gift-book/index.html`, `GiftBookApp.saveLiveMembership()` and `gift-book/static/super-chat-saver.js` | When enabled and paired, saves a paid membership as type `其他`, with a visible `B站会员：...` remark and encrypted `sourceMetadata`; gifted membership events remain ignored. | New |
