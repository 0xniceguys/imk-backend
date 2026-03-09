# Flutter UX Problems — TODO

## 1. "Stream Unavailable" Shown Too Early
- **File:** `lib/screens/live_match_screen.dart` ~line 617
- **Problem:** Dead camera icon + "Stream unavailable" text appears during normal startup gap (WS connected but `streaming_state: ready` not yet received). Looks like a failure but isn't.
- **Fix:** Only show error icon after 20s+ timeout. Show spinner + "Stream starting..." in the gap.

---

## 2. Full Screen Rebuilds 5x Per Second (Jank)
- **File:** `lib/screens/live_match_screen.dart` ~line 198–204
- **Problem:** `game_state` listener calls `setState(() {})` on the entire screen. Video player, header, fighter names, bet button all rebuild 5 times/second.
- **Fix:** Extract health bars, timer, round dots into their own `ConsumerWidget` that watches `gameStateProvider` independently.

---

## 3. Match Data Re-Fetches on Live Screen Entry
- **File:** `lib/screens/live_match_screen.dart` ~line 101, `lib/providers/match_provider.dart`
- **Problem:** Opening the live screen triggers `startFastPolling()` → fresh HTTP fetch. User already tapped the match — we know what match it is. Causes a spinner on entry before anything shows.
- **Fix:** Pass the `Match` object directly through navigation. Don't re-fetch data the app already has.

---

## 4. Timer Frozen on Screen Entry
- **File:** `lib/screens/live_match_screen.dart` `_HealthOverlayState`
- **Problem:** Timer starts at 99 and stays frozen until first `game_state` WebSocket message arrives (can be 2–4s after screen opens).
- **Fix:** Pre-connect WebSocket from `battle_detail_screen` or `arena_list_screen` while countdown is visible, so game state is already flowing by the time user enters the live screen.

---

## 5. HLS Never Auto-Retries After Error
- **File:** `lib/services/hls_player_service.dart` ~line 95–102
- **Problem:** After a single 15-second HLS init timeout, state goes to `error` permanently. No automatic retry.
- **Fix:** Retry once silently (after 3s) before setting `error` state and showing it to the user.

---

## 6. Empty Match List Treated as Network Failure
- **File:** `lib/providers/match_provider.dart` ~line 108–120
- **Problem:** If match list API returns `[]` and there were previously matches, `_failureCount` increments and polling backs off. Valid empty state treated as an error.
- **Fix:** Only increment `_failureCount` on `Exception`, not on empty array.

---

## 7. No Pre-Connection — Every Step Is Sequential and Visible
- **Files:** `lib/screens/battle_detail_screen.dart`, `lib/screens/arena_list_screen.dart`
- **Problem:** WS connect → stream init → HLS init all happen after user opens live screen. Every step is visible as a blank/loading UI.
- **Fix:** Start WS connection from the previous screen (detail/arena list) while countdown is still showing. By the time user taps LIVE, WS is already connected and potentially HLS is already preloading.

---

## 8. Wallet Price Fetch Can Take Up To 36 Seconds
- **File:** `lib/providers/wallet_provider.dart` ~line 311–333
- **Problem:** SOL price is fetched by trying 6 APIs one-by-one in a `for` loop (Jupiter → CoinGecko → Binance → Kraken → DexScreener → CoinPaprika), each with a 6s timeout. SKR does the same with 5 APIs. Worst case: 36s + 30s before wallet shows any price. User sees "loading" indefinitely.
- **Fix:** Fire all sources in parallel with `Future.wait`, take the first non-zero result. Worst case drops to 6 seconds for a single timeout.

---

## 9. Arena List and Battle Detail Rebuild Every Second
- **Files:** `lib/screens/arena_list_screen.dart` line 150, `lib/screens/battle_detail_screen.dart` line 129
- **Problem:** Both screens call `ref.watch(clockTickProvider)` which fires every second. This causes the entire screen — match cards, fighter images, all text — to rebuild every second, not just the countdown timer label.
- **Fix:** Extract the countdown timer into its own small widget that watches `clockTickProvider` in isolation. Parent screens should not watch it at all.

---

## 10. Post-Match "Preparing Result" Has No Auto-Retry
- **File:** `lib/screens/post_match_screen.dart` ~line 57–63
- **Problem:** If the match isn't settled yet when the post-match screen opens, it shows a "Preparing Result" spinner. But there's no timer inside the screen polling for updates — it just waits for the next background `matchProvider` poll (up to 5s away). User is stuck on a dead spinner with no progress.
- **Fix:** Add a local `Timer.periodic` on this screen that calls `ref.read(matchProvider.notifier).refresh()` every 1–2 seconds until the match status changes to `completed`.

---

## 11. Battle Detail Shows Wrong Match When ID Not Found
- **File:** `lib/screens/battle_detail_screen.dart` ~line 354–362
- **Problem:** `_resolveMatch()` falls back to `matches.first` when the requested `matchId` is not in the loaded list. User silently sees a completely different match with no error shown.
- **Fix:** If `matchId` is specified and not found, show an explicit "Match not found" error rather than falling back to another match.

---

## 12. Two Separate Navigation Listeners in Battle Detail (Race Condition Risk)
- **File:** `lib/screens/battle_detail_screen.dart` lines 49–52, 135–147
- **Problem:** One `ref.listenManual` in `initState` and one `ref.listen` in `build()` both watch `matchProvider` and both try to navigate to the live screen. The `_navigatedToLive` flag prevents most double-fires, but the two listeners check it slightly differently, creating a race condition risk on fast state changes.
- **Fix:** Consolidate into a single listener. Remove the `ref.listen` from `build()` entirely — `initState` listener is sufficient.

---

## 13. Typo in Post-Match Screen
- **File:** `lib/screens/post_match_screen.dart` line 290
- **Problem:** Text reads `"No bet(S) placed on this match"` — capital S mid-word looks unprofessional.
- **Fix:** Change to `"No bets placed on this match"`.

---

## 14. Countdown Hits Zero But LIVE Status Delayed 1–6 Seconds
- **Files:** `lib/screens/arena_list_screen.dart`, `lib/screens/battle_detail_screen.dart`, `lib/providers/global_events_provider.dart`
- **Problem:** When the match countdown reaches 0:00, users see "NEXT MATCH" frozen for 1–6 more seconds before it switches to LIVE. This is caused by a chain of delays:
  1. Flutter's local countdown is computed from a stale REST response (already 2–3s old)
  2. Backend must complete a Solana `lock_match` transaction (1–3s on devnet) before updating DB status
  3. Flutter only discovers the LIVE status via REST polling (up to 3s interval)
  The global WebSocket `match_status_changed: live` event should solve this but only if that WS is reliably connected.
- **Fix (two parts):**
  1. The moment countdown hits 0, switch to rapid polling every 500ms until LIVE status is confirmed (currently aggressive refresh only happens at T-2s, stops at T=0)
  2. Audit `global_events_provider.dart` to ensure the global events WebSocket is always connected and auto-reconnects — if healthy, the live transition will be instant via WS event with no polling needed

---

## 15. Arena List Unexpectedly Auto-Navigates to Live Match
- **Files:** `lib/screens/arena_list_screen.dart` lines 97–146, `lib/services/global_events_service.dart`
- **Problem:** When a match goes live, the arena list auto-navigates the user without them tapping anything. This is intentional code but has edge cases:
  1. **Stale event replay on WS reconnect** — `GlobalEventsService` reconnects with exponential backoff. The backend sends `match_status_changed: live` on every fresh connection, so a reconnect fires unwanted navigation even if the match has been live for several minutes.
  2. **`_autoNavigating` flag only lasts 2 seconds** — if the event fires again after the 2s window (e.g., second reconnect), the guard is gone and navigation fires again.
  3. **No opt-out** — there's no way for the user to stay on the arena list if they don't want to watch.
- **Fixes:**
  1. Check the `timestamp` on the event payload — ignore any `match_status_changed: live` where `event['timestamp']` is more than 10 seconds old (timestamp already exists in backend payload at `match_runner.py` line 453)
  2. Make `_autoNavigating` sticky per match ID until the match ends, not a 2-second timeout
  3. Consider showing a "Match is live — Watch Now" snackbar button instead of forcing navigation

---

## 16. Video Stops During Streaming Between Rounds
- **Files:** `lib/services/hls_player_service.dart`, `backend/app/services/match_runner.py` ~line 791
- **Problem:** The backend intentionally stops the HLS capture between rounds (`await self._hls_capture.stop()` at line 791), then restarts it after savestate reload. This creates a real gap in the HLS stream. Flutter's watchdog sees the video position freeze for 10 seconds and restarts the controller — but by then a new segment playlist has started, causing a double-init race. Additionally, the HLS `.m3u8` playlist becomes invalid during this gap, so any client trying to probe it gets a 404 or a stale manifest.
- **Fix:** 
  1. Backend: keep HLS running between rounds — only pause agent inputs, not the capture. OR send a `round_transition` WS event so Flutter knows to show a "Round X starting..." overlay instead of trying to play a dead stream.
  2. Flutter watchdog: extend the stuck threshold from 10s to 20s for known round transition windows, or pause the watchdog when a `round_end` event is received.

---

## 17. Match Shows as Not Completed When It Has Ended
- **Files:** `backend/app/services/match_runner.py` ~line 756–778, `lib/screens/post_match_screen.dart`
- **Problem:** The sequence on the backend is:
  1. `match_ended` WS event broadcasts to all clients ← Flutter navigates to post-match HERE
  2. `_auto_settle()` starts — on-chain `resolve_match` transaction (1–5s on devnet)
  3. DB updated to `completed` with winner
  Flutter arrives at post-match BEFORE step 3 is done. The match status in DB is still `live`, so `post_match_screen.dart` shows "Preparing Result" with no retry logic (see Issue #10). If settlement fails and falls back to `_mark_match_completed_fallback`, it can take even longer.
- **Fix (two parts):**
  1. Flutter: start aggressive polling (every 1s) the moment it navigates to post-match, until match status = `completed` (fixes Issue #10 together)
  2. Backend: broadcast a second `match_status_changed: completed` global event AFTER `_auto_settle()` finishes, so Flutter gets an instant notification instead of polling
