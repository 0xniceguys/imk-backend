import 'dart:async';

import 'package:flutter/foundation.dart';
import 'package:video_player/video_player.dart';

class HlsPlayerService {
  final ValueNotifier<VideoPlayerController?> controllerNotifier =
      ValueNotifier(null);
  final ValueNotifier<String?> activeMatchIdNotifier = ValueNotifier(null);
  final ValueNotifier<HlsPreloadState> stateNotifier =
      ValueNotifier(HlsPreloadState.idle);

  VideoPlayerController? get controller => controllerNotifier.value;
  String? get activeMatchId => activeMatchIdNotifier.value;
  HlsPreloadState get state => stateNotifier.value;

  bool _disposed = false;
  bool _initializing = false;
  int _initToken = 0;

  // When true, unmute after init completes (live screen is in the foreground).
  bool _wantsAudio = false;

  // ── Real-time controller state tracking ───────────────────────────────────
  bool _lastBuffering = false;
  bool _lastPlaying = false;
  bool _lastHasError = false;

  void _onControllerUpdate(VideoPlayerController ctrl, String matchId) {
    try {
      final v = ctrl.value;
      final buffering = v.isBuffering;
      final playing = v.isPlaying;
      final hasError = v.hasError;

      // Log transitions only (not every frame)
      if (buffering != _lastBuffering) {
        debugPrint(
          '[HlsPlayer] ${buffering ? "⏳ BUFFERING started" : "▶️ BUFFERING ended"} '
          'pos=${v.position} match=$matchId',
        );
        _lastBuffering = buffering;
      }
      if (playing != _lastPlaying) {
        debugPrint(
          '[HlsPlayer] ${playing ? "▶️ PLAYING" : "⏸️ NOT PLAYING"} '
          'pos=${v.position} match=$matchId',
        );
        _lastPlaying = playing;
      }
      if (hasError && !_lastHasError) {
        debugPrint(
          '[HlsPlayer] 🔴 ERROR detected: ${v.errorDescription} '
          'pos=${v.position} match=$matchId',
        );
        _lastHasError = hasError;
      }
    } catch (_) {}
  }

  // ── Stuck watchdog ────────────────────────────────────────────────────────
  Timer? _watchdogTimer;
  Duration _lastPosition = Duration.zero;
  int _stuckTicks = 0; // consecutive ticks with no position advance
  bool _watchdogPaused = false; // paused during known round-transition gaps
  int _graceTicks = 0;     // ticks to skip at startup before checking position
  int _bufferingTicks = 0; // consecutive ticks where controller is buffering
  int _healthyTickCount = 0;
  static const _watchdogInterval = Duration(seconds: 1);
  static const _stuckTicksBeforeRestart = 6;     // 6 × 1s = 6s frozen → restart
  static const _bufferingTicksBeforeRestart = 5; // 5 × 1s = 5s buffering → restart
  static const _watchdogGraceTicks = 4;           // skip first 4s (0.5s segments fill buffer fast)

  /// Called instead of restarting if set when the stream fatally errors or
  /// freezes. Used by live_match_screen to navigate after match end once the
  /// buffered video plays out and the stream naturally 404s.
  VoidCallback? onStreamDied;

  Future<void> preload(String matchId, String hlsUrl, {int attempt_ = 1}) async {
    if (_disposed) return;
    const maxAttempts = 3;

    final alreadyPlaying =
        activeMatchIdNotifier.value == matchId &&
        controller?.value.isInitialized == true;
    if (alreadyPlaying) {
      debugPrint('[HlsPlayerService] Already playing $matchId — skipping preload');
      return;
    }

    final alreadyInitializing =
        _initializing && activeMatchIdNotifier.value == matchId;
    if (alreadyInitializing) {
      debugPrint('[HlsPlayerService] Already initializing $matchId — skipping duplicate');
      return;
    }

    await _teardown();
    if (_disposed) return;

    final token = ++_initToken;
    _initializing = true;
    activeMatchIdNotifier.value = matchId;
    _setState(HlsPreloadState.initializing);

    debugPrint('[HlsPlayerService] Loading HLS (attempt $attempt_/$maxAttempts): $matchId — $hlsUrl');

    VideoPlayerController? ctrl;
    try {
      ctrl = VideoPlayerController.networkUrl(
        Uri.parse(hlsUrl),
        videoPlayerOptions: VideoPlayerOptions(mixWithOthers: false),
      );

      await ctrl.initialize().timeout(const Duration(seconds: 15));

      if (_disposed || _initToken != token) {
        debugPrint('[HlsPlayerService] Stale init for $matchId — discarding');
        await _safeDispose(ctrl);
        return;
      }

      // Pre-load muted — audio only starts when user opens the live screen.
      await ctrl.setVolume(0.0);
      await ctrl.play();

      if (_disposed || _initToken != token) {
        debugPrint('[HlsPlayerService] Stale after play() for $matchId — discarding');
        await _safeDispose(ctrl);
        return;
      }

      // Listen for real-time state changes (buffering start/end, errors)
      final nonNullCtrl = ctrl;
      ctrl.addListener(() => _onControllerUpdate(nonNullCtrl, matchId));
      controllerNotifier.value = ctrl;
      _setState(HlsPreloadState.playing);
      _startWatchdog(matchId, hlsUrl);
      if (_wantsAudio) {
        await ctrl.setVolume(1.0);
        debugPrint('[HlsPlayerService] ✅ Playing $matchId (with audio)');
      } else {
        debugPrint('[HlsPlayerService] ✅ Playing $matchId (muted)');
      }
    } on TimeoutException {
      debugPrint('[HlsPlayerService] ⏰ Timeout initializing HLS $matchId (attempt $attempt_)');
      await _safeDispose(ctrl);
      if (_disposed || _initToken != token) return;
      if (attempt_ < maxAttempts) {
        debugPrint('[HlsPlayerService] Retrying in 2s (attempt ${attempt_ + 1}/$maxAttempts)…');
        await Future.delayed(const Duration(seconds: 2));
        if (!_disposed) preload(matchId, hlsUrl, attempt_: attempt_ + 1);
      } else {
        debugPrint('[HlsPlayerService] ❌ Giving up after $maxAttempts attempts for $matchId');
        _setState(HlsPreloadState.error);
        if (onStreamDied != null) {
          debugPrint('[HlsPlayerService] → stream exhausted all retries — calling onStreamDied');
          onStreamDied!();
        }
      }
    } catch (e, st) {
      debugPrint('[HlsPlayerService] ❌ Init error $matchId (attempt $attempt_): $e\n$st');
      await _safeDispose(ctrl);
      if (_disposed || _initToken != token) return;
      if (attempt_ < maxAttempts) {
        debugPrint('[HlsPlayerService] Retrying in 2s (attempt ${attempt_ + 1}/$maxAttempts)…');
        await Future.delayed(const Duration(seconds: 2));
        if (!_disposed) preload(matchId, hlsUrl, attempt_: attempt_ + 1);
      } else {
        debugPrint('[HlsPlayerService] ❌ Giving up after $maxAttempts attempts for $matchId');
        _setState(HlsPreloadState.error);
        if (onStreamDied != null) {
          debugPrint('[HlsPlayerService] → stream exhausted all retries — calling onStreamDied');
          onStreamDied!();
        }
      }
    } finally {
      if (_initToken == token) _initializing = false;
    }
  }

  // ── Watchdog ──────────────────────────────────────────────────────────────

  void _startWatchdog(String matchId, String hlsUrl) {
    _stopWatchdog();
    _lastPosition = Duration.zero;
    _stuckTicks = 0;
    _bufferingTicks = 0;
    _healthyTickCount = 0;
    _lastBuffering = false;
    _lastPlaying = false;
    _lastHasError = false;
    _graceTicks = _watchdogGraceTicks; // allow live-stream buffer to warm up

    _watchdogTimer = Timer.periodic(_watchdogInterval, (_) {
      if (_disposed) {
        _stopWatchdog();
        return;
      }
      final ctrl = controllerNotifier.value;
      if (ctrl == null || !ctrl.value.isInitialized) {
        _stopWatchdog();
        return;
      }

      // Skip tick entirely during known round-transition gaps so the stream
      // gap doesn't look like a stuck stream and cause a spurious restart.
      if (_watchdogPaused) {
        debugPrint('[HlsPlayerService] Watchdog tick skipped (round transition paused)');
        return;
      }

      // Grace period: HLS live streams sit at ~2 s position while the initial
      // buffer fills. Silently snapshot position each grace tick so the first
      // real comparison starts from a meaningful baseline, not Duration.zero.
      if (_graceTicks > 0) {
        _graceTicks--;
        _lastPosition = ctrl.value.position;
        debugPrint('[HlsPlayerService] Watchdog grace tick ($_graceTicks remaining), pos=${ctrl.value.position}');
        return;
      }

      try {
        final pos = ctrl.value.position;
        final isPlaying = ctrl.value.isPlaying;
        final hasError = ctrl.value.hasError;
        final isBuffering = ctrl.value.isBuffering;

        if (hasError) {
          debugPrint('[HlsPlayerService] 🔴 Controller error detected');
          _stopWatchdog();
          if (onStreamDied != null) {
            debugPrint('[HlsPlayerService] → calling onStreamDied (match ended mode)');
            onStreamDied!();
          } else {
            debugPrint('[HlsPlayerService] 🔄 Restarting stream via forceReload (error recovery)');
            forceReload(matchId, hlsUrl);
          }
          return;
        }

        // Prolonged buffering = decoder stalled (e.g. corrupt TS packets).
        // Position may still advance slightly so we can't rely on stuck-check alone.
        if (isBuffering) {
          _bufferingTicks++;
          _stuckTicks = 0; // position won't advance while buffering — don't double-count
          debugPrint(
            '[HlsPlayerService] ⏳ Buffering stall tick $_bufferingTicks/$_bufferingTicksBeforeRestart',
          );
          if (_bufferingTicks >= _bufferingTicksBeforeRestart) {
            _stopWatchdog();
            if (onStreamDied != null) {
              debugPrint('[HlsPlayerService] 🔄 Prolonged buffering (${_bufferingTicks}s) — calling onStreamDied (match ended, stream drained)');
              onStreamDied!();
            } else {
              debugPrint('[HlsPlayerService] 🔄 Prolonged buffering (${_bufferingTicks}s) — restarting stream via forceReload');
              forceReload(matchId, hlsUrl);
            }
          }
          return;
        } else {
          _bufferingTicks = 0;
        }

        if (!isPlaying) {
          // Not playing: try to resume before flagging as stuck
          ctrl.play().catchError((_) {});
          return;
        }

        if (pos == _lastPosition) {
          // On some Android devices (e.g. OnePlus CPH2649), the position
          // getter returns stale values while ExoPlayer is actively decoding
          // at full fps. If the controller reports playing=true AND
          // buffering=false, the stream is healthy — only the position
          // reporting is lagging. Don't count these ticks toward the stuck
          // threshold to avoid tearing down a working stream.
          if (isPlaying && !isBuffering) {
            _stuckTicks = 0; // reset — stream is healthy
            debugPrint(
              '[HlsWatchdog] ℹ️ Position stale but player healthy '
              'pos=$pos playing=$isPlaying buffering=$isBuffering — ignoring',
            );
          } else {
            _stuckTicks++;
            debugPrint(
              '[HlsWatchdog] ⚠️ STUCK pos=$pos tick=$_stuckTicks/$_stuckTicksBeforeRestart '
              'playing=$isPlaying buffering=$isBuffering error=$hasError',
            );
            if (_stuckTicks >= _stuckTicksBeforeRestart) {
              debugPrint('[HlsWatchdog] 🔄 Stream frozen for ${_stuckTicks}s — restarting');
              _stopWatchdog();
              if (onStreamDied != null) {
                debugPrint('[HlsWatchdog] → calling onStreamDied (frozen in match-ended mode)');
                onStreamDied!();
              } else {
                debugPrint('[HlsWatchdog] 🔄 Restarting stream via forceReload (stuck recovery)');
                forceReload(matchId, hlsUrl);
              }
            }
          }
        } else {
          if (_stuckTicks > 0) {
            debugPrint('[HlsWatchdog] ✅ Unstuck: pos advanced $_lastPosition → $pos');
          }
          _stuckTicks = 0;
          _lastPosition = pos;
          // Log healthy tick every ~30s (every 30th tick at 1s interval) to confirm stream is alive
          if (_healthyTickCount++ % 30 == 0) {
            debugPrint(
              '[HlsWatchdog] OK pos=$pos playing=$isPlaying buffering=$isBuffering',
            );
          }
        }
      } catch (e) {
        debugPrint('[HlsPlayerService] Watchdog check error: $e');
      }
    });
  }

  void _stopWatchdog() {
    _watchdogTimer?.cancel();
    _watchdogTimer = null;
    _stuckTicks = 0;
    _bufferingTicks = 0;
    _watchdogPaused = false;
  }

  /// Pauses the stuck-position watchdog during a known round-transition gap.
  /// Call this when receiving streaming_state: round_transition from the backend.
  void pauseWatchdog() {
    _watchdogPaused = true;
    _stuckTicks = 0; // reset so transition time doesn't count toward stuck threshold
    debugPrint('[HlsPlayerService] Watchdog paused (round transition)');
  }

  /// Resumes the watchdog after HLS restarts for the new round.
  void resumeWatchdog() {
    _watchdogPaused = false;
    _stuckTicks = 0;
    _lastPosition = Duration.zero; // clear stale position so new round starts clean
    debugPrint('[HlsPlayerService] Watchdog resumed (new round)');
  }

  /// Unmutes — called when user enters the live screen.
  /// If init is still in progress, sets wantsAudio=true so preload() will
  /// unmute automatically once the controller is ready.
  void requestAudio() {
    debugPrint('[HlsPlayerService] requestAudio() — wantsAudio=true, hasCtrl=${controllerNotifier.value != null}');
    _wantsAudio = true;
    try {
      controllerNotifier.value?.setVolume(1.0);
    } catch (e) {
      debugPrint('[HlsPlayerService] requestAudio error: $e');
    }
  }

  /// Mutes and clears the wantsAudio flag — called when user leaves the live screen.
  void silenceAndReset() {
    debugPrint('[HlsPlayerService] silenceAndReset() — muting, wantsAudio=false, match=${activeMatchIdNotifier.value}');
    _wantsAudio = false;
    try {
      controllerNotifier.value?.setVolume(0.0);
    } catch (e) {
      debugPrint('[HlsPlayerService] silenceAndReset error: $e');
    }
  }

  /// Legacy alias kept for call sites that already use unmute().
  void unmute() => requestAudio();

  /// Legacy alias kept for call sites that already use mute().
  void mute() => silenceAndReset();

  /// Force a clean reload of the HLS stream for [matchId], even if a
  /// controller is already playing that match.
  ///
  /// Used for round transitions where the matchId is unchanged but the backend
  /// has reset the HLS segment sequence — the normal preload() guard would
  /// incorrectly skip the reload, leaving the client stuck on the dead
  /// round-1 stream.
  Future<void> forceReload(String matchId, String hlsUrl) async {
    debugPrint('[HlsPlayerService] forceReload() for $matchId — tearing down existing controller (was=${activeMatchIdNotifier.value}, state=$state)');
    await _teardown();
    if (_disposed) return;
    debugPrint('[HlsPlayerService] forceReload() teardown complete — starting fresh preload');
    await preload(matchId, hlsUrl);
  }

  // ── Lifecycle ─────────────────────────────────────────────────────────────

  Future<void> stop() async {
    debugPrint('[HlsPlayerService] stop() — match ${activeMatchIdNotifier.value}');
    _wantsAudio = false; // clear flag so restart doesn't unmute in background
    onStreamDied = null;  // clear callback — never leak into next match session
    _stopWatchdog();
    await _teardown();
    // Use 'stopped' (not 'idle') so globalHlsPreloaderProvider knows the user
    // intentionally left and should NOT restart the preload automatically.
    _setState(HlsPreloadState.stopped);
  }

  Future<void> _teardown() async {
    _stopWatchdog();
    _initializing = false;
    ++_initToken;
    final old = controllerNotifier.value;
    controllerNotifier.value = null;
    activeMatchIdNotifier.value = null;
    await _safeDispose(old);
  }

  Future<void> _safeDispose(VideoPlayerController? ctrl) async {
    if (ctrl == null) return;
    try {
      await ctrl.dispose();
    } catch (e) {
      debugPrint('[HlsPlayerService] Error disposing controller: $e');
    }
  }

  void _setState(HlsPreloadState s) {
    if (!_disposed) stateNotifier.value = s;
  }

  void dispose() {
    if (_disposed) return;
    _disposed = true;
    _stopWatchdog();
    _teardown();
    controllerNotifier.dispose();
    activeMatchIdNotifier.dispose();
    stateNotifier.dispose();
  }
}

/// `stopped` = user intentionally left the live screen; globalHlsPreloader
/// will not auto-restart until the arena list calls preload() again.
enum HlsPreloadState { idle, initializing, playing, error, stopped }
