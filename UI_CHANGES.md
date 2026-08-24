# GiftBook UI changes

| Element name | DOM ID/class | Source and method | Visible text and behavior | Change |
| --- | --- | --- | --- | --- |
| Live-room selector | `#edit-live-room-id` | `gift-book/index.html`, `GiftBookApp.showEditEventInfoModal()` / `populateLiveRoomSelector()` | Lists configured rooms numerically plus `未配对直播间`; the selected room is persisted with the current item. | New |
| Clear pairing control | `#clear-live-room-pairing` | `gift-book/index.html`, `GiftBookApp.showEditEventInfoModal()` | `清除配对`; immediately changes the selector to unpaired without requesting the password. | New |
| Pairing status | `#live-room-pairing-status` | `gift-book/index.html`, `GiftBookApp.updateLiveRoomPairingStatus()` | Explains whether the item is unpaired, paired to a configured room, or paired to a room absent from the current bridge configuration. | New |
| Live bridge status | `#live-bridge-status` | `gift-book/index.html`, `GiftBookApp.updateLiveBridgeStatus()` | Existing status text now includes the active room (`直播已连接（房间 …）`) and can show `当前事项未配对直播间` or `当前事项配对的直播间未配置`. | Modified |
| Automatic live-save notification | Existing notification elements created by `UIManager.showNotification()` | `gift-book/index.html`, `GiftBookApp.saveGift()` and `SuperChatSaver` | Shows `醒目留言已自动保存。`, `重复的醒目留言已忽略。`, or `醒目留言已撤回，匹配记录已标记为作废。` while using the existing notification styling. | Modified |

