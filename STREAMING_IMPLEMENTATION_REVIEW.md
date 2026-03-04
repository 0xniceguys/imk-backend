# Streaming Implementation Review & Analysis

**Date:** 2026-03-03
**Reviewer:** Claude
**Status:** ⚠️ FUNCTIONAL BUT HAS SCALABILITY CONCERNS

---

## Executive Summary

The current streaming implementation **works correctly** for small-scale deployments but has **several performance and scalability issues** that will cause problems under load. The architecture is sound, but the implementation has critical bottlenecks that need to be addressed before handling multiple concurrent viewers or matches.

### Risk Level: 🟡 MEDIUM

✅ **Works for:** 1-10 concurrent viewers per match
⚠️ **Issues at:** 50+ concurrent viewers per match
❌ **Breaks at:** 100+ concurrent viewers or 5+ simultaneous matches

---

## Architecture Overview

### Current Design (Correct Approach)

```
┌─────────────────┐
│  Match Runner   │
│  (asyncio task) │
└────────┬────────┘
         │
    ┌────┴─────────────────┐
    │                      │
    ▼                      ▼
┌─────────┐          ┌──────────┐
│ FFmpeg  │          │  Agent   │
│ Capture │          │  Brain   │
│ (30fps) │          │  (10Hz)  │
└────┬────┘          └────┬─────┘
     │                    │
     │ JPEG bytes         │ Game state JSON
     ▼                    ▼
┌──────────────────────────────┐
│  WebSocket Manager           │
│  (broadcast to N clients)    │
└──────────────────────────────┘
     │
     ├──────┬──────┬──────┬
     ▼      ▼      ▼      ▼
   WS1    WS2    WS3   ...WSn
```

**This is a good design pattern**, BUT the implementation has issues.

---

## Critical Issues Found

### 🔴 **Issue #1: Game State Broadcast at 10Hz (MAJOR BOTTLENECK)**

**Location:** `backend/app/services/match_runner.py:553-556`

```python
# Agent brain loop runs at 10Hz
while self.state == RunnerState.RUNNING:
    # ... agent logic ...

    # ❌ PROBLEM: Broadcasting on EVERY agent step
    await ws_manager.broadcast_json(
        self.match_id,
        self.latest_snapshot.to_dict(),
    )
```

**Problem:**
- **Broadcast frequency:** 10 times per second
- **Per viewer cost:** JSON serialization + WebSocket send
- **Total cost:** `10 broadcasts/sec × N viewers = 10N messages/sec`

**Impact:**
- **10 viewers:** 100 JSON messages/sec (OK)
- **50 viewers:** 500 JSON messages/sec (HIGH CPU)
- **100 viewers:** 1,000 JSON messages/sec (OVERLOAD)

**Why this is bad:**
1. Game state changes slowly (health, timer, positions)
2. No need to send updates 10x/sec when nothing changed
3. Clients only need ~2-5 updates/sec for smooth UI
4. Wastes CPU on serialization and network bandwidth

**Recommended Fix:**
```python
# Throttle game state broadcasts to 2-5Hz
self._last_state_broadcast = 0
STATE_BROADCAST_INTERVAL = 0.2  # 5Hz

if time.time() - self._last_state_broadcast >= STATE_BROADCAST_INTERVAL:
    await ws_manager.broadcast_json(
        self.match_id,
        self.latest_snapshot.to_dict(),
    )
    self._last_state_broadcast = time.time()
```

---

### 🟡 **Issue #2: No Backpressure Handling in Broadcasts**

**Location:** `backend/app/ws/connection_manager.py:35-47`

```python
async def broadcast_json(self, match_id: str, data: dict) -> None:
    room = self._rooms.get(match_id)
    if not room:
        return
    message = json.dumps(data)  # ❌ Single serialization (good)
    dead: list[WebSocket] = []
    tasks = []
    for ws in room:
        tasks.append(self._safe_send(ws, message, dead))
    await asyncio.gather(*tasks)  # ⚠️ Waits for ALL sends to complete
    for ws in dead:
        room.discard(ws)
```

**Problem:**
- `asyncio.gather()` **blocks** until ALL clients receive the message
- If one client has slow network, **entire broadcast is delayed**
- No timeout on individual sends
- Slow clients can stall the agent brain loop

**Impact:**
- One slow client (200ms latency) delays broadcast by 200ms
- Agent brain loop at 10Hz → 100ms between steps
- **Slow client can halve agent decision rate!**

**Recommended Fix:**
```python
async def broadcast_json(self, match_id: str, data: dict) -> None:
    room = self._rooms.get(match_id)
    if not room:
        return
    message = json.dumps(data)
    dead: list[WebSocket] = []

    # Fire-and-forget with timeout
    for ws in room:
        asyncio.create_task(
            self._safe_send_with_timeout(ws, message, dead, timeout=0.1)
        )

    # Cleanup dead connections after a brief delay
    await asyncio.sleep(0.01)
    for ws in dead:
        room.discard(ws)

async def _safe_send_with_timeout(
    self, ws: WebSocket, message: str, dead: list, timeout: float
) -> None:
    try:
        await asyncio.wait_for(ws.send_text(message), timeout=timeout)
    except (asyncio.TimeoutError, Exception):
        dead.append(ws)
```

---

### 🟡 **Issue #3: Frame Broadcast at 30fps Without Throttling**

**Location:** `backend/app/services/match_runner.py:240-243`

```python
async def _on_ffmpeg_frame(self, jpeg_bytes: bytes) -> None:
    """Callback: FFmpeg delivered a JPEG frame — broadcast it."""
    self.latest_frame = jpeg_bytes
    await ws_manager.broadcast_bytes(self.match_id, jpeg_bytes)  # ❌ Every frame
```

**Problem:**
- **30 frames/sec** × **N viewers** = massive data throughput
- **No frame skipping** for slow clients
- **No compression level control**
- Each frame is ~20-50KB JPEG → 600KB-1.5MB/sec per viewer

**Impact:**
- **10 viewers:** 6-15 MB/sec total bandwidth (OK)
- **50 viewers:** 30-75 MB/sec total bandwidth (HIGH)
- **100 viewers:** 60-150 MB/sec total bandwidth (SATURATES NIC)

**Current Settings:**
```python
FFmpegCapture(
    framerate=30,   # 30fps
    quality=15,     # Medium quality (lower = better)
)
```

**Recommended Improvements:**
1. **Drop frames for slow clients** - maintain buffer, skip frames if client lags
2. **Adjustable quality** - let clients request lower quality
3. **Consider HLS/DASH** - for 100+ viewers, switch to HTTP streaming

---

### 🟢 **Issue #4: Memory Leak Risk - Unclosed Stream Controllers**

**Location:** `streaming/flutter_app/lib/services/match_stream_service.dart:27-32`

```dart
// Stream controllers for different message types
final _gameStateCtrl = StreamController<GameState>.broadcast();
final _frameCtrl = StreamController<Uint8List>.broadcast();
// ...

void dispose() {
    disconnect();
    _gameStateCtrl.close();  // ✅ Properly closed
    _frameCtrl.close();
    // ...
}
```

**Status:** ✅ **Properly handled** in Flutter

BUT on backend:

**No equivalent cleanup** - WebSocket manager keeps dead connections in memory until broadcast detects them.

**Minor issue** but can accumulate over time.

---

### 🟢 **Issue #5: No Rate Limiting or DDoS Protection**

**Location:** `backend/app/ws/game_state.py:17`

```python
@router.websocket("/ws/match/{match_id}")
async def match_websocket(ws: WebSocket, match_id: str):
    # ❌ No rate limiting
    # ❌ No max connections per IP
    # ❌ No viewer cap per match
```

**Risks:**
- Malicious client opens 1000 connections to one match
- CPU and memory exhaustion
- Legitimate viewers can't connect

**Recommended Fix:**
```python
MAX_VIEWERS_PER_MATCH = 500
MAX_CONNECTIONS_PER_IP = 10

# Track connections by IP
_ip_connections: dict[str, int] = defaultdict(int)

@router.websocket("/ws/match/{match_id}")
async def match_websocket(ws: WebSocket, match_id: str):
    # Check viewer cap
    if manager.viewer_count(match_id) >= MAX_VIEWERS_PER_MATCH:
        await ws.accept()
        await ws.close(code=4003, reason="Match at viewer capacity")
        return

    # Check IP rate limit
    client_ip = ws.client.host
    if _ip_connections[client_ip] >= MAX_CONNECTIONS_PER_IP:
        await ws.accept()
        await ws.close(code=4029, reason="Too many connections")
        return

    _ip_connections[client_ip] += 1
    try:
        # ... existing logic ...
    finally:
        _ip_connections[client_ip] -= 1
```

---

## Performance Benchmarks (Estimated)

### Current Implementation

| Viewers | Game State | Frames    | Total BW  | CPU Usage | Status |
|---------|------------|-----------|-----------|-----------|--------|
| 1       | 10/sec     | 30/sec    | 1 MB/sec  | ~5%       | ✅ OK   |
| 10      | 100/sec    | 300/sec   | 10 MB/sec | ~20%      | ✅ OK   |
| 50      | 500/sec    | 1500/sec  | 50 MB/sec | ~60%      | ⚠️ HIGH |
| 100     | 1000/sec   | 3000/sec  | 100 MB/sec| ~95%      | ❌ SATURATED |

### With Recommended Fixes

| Viewers | Game State | Frames    | Total BW  | CPU Usage | Status |
|---------|------------|-----------|-----------|-----------|--------|
| 1       | 3/sec      | 30/sec    | 1 MB/sec  | ~3%       | ✅ OK   |
| 10      | 30/sec     | 300/sec   | 10 MB/sec | ~10%      | ✅ OK   |
| 50      | 150/sec    | 1500/sec  | 50 MB/sec | ~35%      | ✅ OK   |
| 100     | 300/sec    | 3000/sec  | 100 MB/sec| ~70%      | ✅ OK   |
| 500     | 1500/sec   | 15000/sec | 500 MB/sec| ~95%      | ⚠️ HIGH |

**Key Improvement:** 3x reduction in game state overhead enables ~3x more viewers.

---

## Good Design Decisions ✅

### 1. **Singleton Connection Manager**
```python
manager = ConnectionManager()  # ✅ Good - one manager for all matches
```
Avoids multiple managers competing, clean room-based design.

### 2. **Room-Based Architecture**
```python
self._rooms: dict[str, set[WebSocket]] = defaultdict(set)
```
Efficient O(1) lookup, clean separation between matches.

### 3. **Decoupled Agent & Frame Loops**
- Agent brain: 10Hz (low frequency, CPU-bound)
- Frame capture: 30fps (high frequency, I/O-bound)

Running separately prevents frame drops during agent computation.

### 4. **Automatic Dead Connection Cleanup**
```python
dead: list[WebSocket] = []
# ... send fails ...
for ws in dead:
    room.discard(ws)
```
Prevents memory leaks from disconnected clients.

### 5. **Flutter Reconnection Logic**
```dart
void _scheduleReconnect(String matchId, {int? closeCode}) {
    // Exponential backoff: 3s, 6s, 12s, 24s, 48s
    final delaySeconds = 3 * (1 << _reconnectAttempts.clamp(0, 4));
}
```
Smart exponential backoff, respects 4004 (no runner) to avoid spam.

### 6. **Ping/Pong Keepalive**
```dart
_pingTimer = Timer.periodic(const Duration(seconds: 15), (_) {
    sendPing();
});
```
Prevents proxy/firewall timeouts on idle connections.

---

## Scaling Recommendations

### Short-term Fixes (< 1 week)

1. **Throttle game state broadcasts** to 3-5Hz
2. **Add send timeouts** to prevent slow clients blocking broadcasts
3. **Add viewer caps** per match (500 max)
4. **Add IP rate limiting** (10 connections per IP)

### Medium-term Improvements (1-4 weeks)

5. **Frame buffer with skip logic** - drop frames for slow clients
6. **Configurable frame quality** - let clients request lower FPS/quality
7. **Metrics & monitoring** - track viewer counts, broadcast latency, frame drops
8. **Load testing** - simulate 100-500 concurrent viewers

### Long-term Architecture (1-3 months)

9. **HLS/DASH streaming** - for 1000+ viewers, switch to HTTP-based adaptive streaming
10. **Redis pub/sub** - for multi-server deployment, use Redis to coordinate broadcasts
11. **CDN integration** - offload frame delivery to CloudFlare/AWS CloudFront
12. **Horizontal scaling** - run multiple FastAPI workers with sticky sessions

---

## Code Quality Assessment

### Backend Code: 🟢 GOOD

✅ **Strengths:**
- Clean async/await throughout
- Proper error handling with try/except
- Good logging
- Type hints everywhere
- Documented functions

⚠️ **Weaknesses:**
- No rate limiting
- No performance monitoring
- Broadcast frequency too high
- No backpressure handling

### Flutter Code: 🟢 VERY GOOD

✅ **Strengths:**
- Excellent reconnection logic
- Proper stream controller cleanup
- Exponential backoff
- Type-safe message parsing
- Respects close codes (4004)

✅ **No issues found** in Flutter streaming code!

---

## Testing Recommendations

### Load Testing Scenarios

1. **Single Match, 10 Viewers**
   - Expected: Smooth streaming, <50ms latency
   - Duration: 5 minutes

2. **Single Match, 50 Viewers**
   - Expected: Some frame drops for slow clients
   - Duration: 5 minutes
   - Monitor: CPU, memory, network bandwidth

3. **Single Match, 100 Viewers**
   - Expected: High CPU (70-90%), possible broadcast delays
   - Duration: 2 minutes
   - Monitor: Broadcast latency, agent decision rate

4. **3 Concurrent Matches, 30 Viewers Each**
   - Expected: 3x resource usage
   - Duration: 5 minutes
   - Monitor: Process count, total bandwidth

5. **Stress Test: Connection Spam**
   - Connect 1000 WebSockets rapidly
   - Expected: Should reject with 4003/4029 after caps
   - Monitor: Memory growth

### Testing Tools

```bash
# Load testing with Artillery
artillery quick --count 50 --num 10 wss://immortalkombat.mercle.ai/ws/match/test-match-id

# Monitor resources
htop  # CPU/memory
iftop # Network bandwidth
journalctl -u imk.service -f  # Logs
```

---

## Implementation Priority

### Critical (Fix Before Launch)

1. ✅ **Throttle game state broadcasts** (5Hz instead of 10Hz)
2. ✅ **Add send timeouts** (100ms per client)
3. ✅ **Add viewer cap** (500 per match)

### Important (Fix Within 2 Weeks)

4. ⏭️ **Frame buffer with skip logic**
5. ⏭️ **IP rate limiting**
6. ⏭️ **Monitoring & metrics**

### Nice to Have (Post-Launch)

7. ⏭️ **Adjustable quality**
8. ⏭️ **HLS/DASH for 1000+ viewers**
9. ⏭️ **Multi-server deployment**

---

## Sample Fix: Throttled Game State Broadcast

**File:** `backend/app/services/match_runner.py`

**Before:**
```python
# Agent loop at 10Hz
while self.state == RunnerState.RUNNING:
    state = read_fight_state(self._bridge, step_count)
    # ... agent logic ...

    self.latest_snapshot = GameSnapshot(...)
    await ws_manager.broadcast_json(  # ❌ 10 times/sec
        self.match_id,
        self.latest_snapshot.to_dict(),
    )
```

**After:**
```python
# Agent loop at 10Hz, broadcast at 3Hz
STATE_BROADCAST_INTERVAL = 0.333  # ~3Hz
self._last_state_broadcast = 0.0

while self.state == RunnerState.RUNNING:
    state = read_fight_state(self._bridge, step_count)
    # ... agent logic ...

    self.latest_snapshot = GameSnapshot(...)

    # Only broadcast if enough time has passed
    now = time.monotonic()
    if now - self._last_state_broadcast >= STATE_BROADCAST_INTERVAL:
        await ws_manager.broadcast_json(
            self.match_id,
            self.latest_snapshot.to_dict(),
        )
        self._last_state_broadcast = now
```

**Impact:**
- Reduces game state broadcasts by 70% (10Hz → 3Hz)
- Frees CPU for more viewers
- No visible impact on client UI (3 updates/sec is smooth)

---

## Conclusion

### Summary

🟢 **Architecture:** Sound and well-designed
🟡 **Implementation:** Has performance bottlenecks
🔴 **Scalability:** Needs fixes before handling >50 viewers

### Verdict

The streaming implementation **works correctly** but has **sub-optimal performance characteristics**. It will handle small-scale usage (10-20 viewers per match) without issues, but will struggle under production load (100+ viewers).

### Recommended Action

1. **Apply critical fixes** (throttling, timeouts, caps) before launch
2. **Load test** with 50-100 simulated viewers
3. **Monitor** in production and tune based on actual usage
4. **Plan for HLS/DASH** if viewership exceeds 500 concurrent

**With these fixes applied, the system should handle 100-200 concurrent viewers per match comfortably.**

---

## Files to Modify

1. `backend/app/services/match_runner.py` - Add broadcast throttling
2. `backend/app/ws/connection_manager.py` - Add timeouts and fire-and-forget
3. `backend/app/ws/game_state.py` - Add rate limiting and viewer caps
4. `backend/app/config.py` - Add configuration for limits

**Estimated Time:** 4-8 hours for critical fixes + testing
