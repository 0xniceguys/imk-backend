import 'dart:async';
import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:video_player/video_player.dart';

import '../core/constants.dart';
import '../core/palette.dart';
import '../core/runtime_client_config.dart';
import '../core/typography.dart';
import '../router.dart';
import '../models/match.dart';
import '../models/game_state.dart';
import '../providers/bet_provider.dart';
import '../providers/match_provider.dart';
import '../providers/match_stream_provider.dart';
import '../providers/global_events_provider.dart';
import '../services/hls_player_service.dart';
import '../app.dart' show routeObserver;
import '../widgets/shared/app_shell.dart';
import '../widgets/shared/ornate_button.dart';
import '../widgets/shared/ik_loader.dart';
import '../widgets/betting/bet_bottom_sheet.dart';

enum LiveStreamClientState {
  idle,
  wsConnected,
  streamInitializing,
  streamReady,
  playerInitializing,
  playing,
  ended,
  error,
}


class LiveMatchScreen extends ConsumerStatefulWidget {
  const LiveMatchScreen({super.key, required this.onNavigate, this.matchId});
  final void Function(String) onNavigate;
  final String? matchId;

  @override
  ConsumerState<LiveMatchScreen> createState() => _LiveMatchScreenState();
}

class _LiveMatchScreenState extends ConsumerState<LiveMatchScreen>
    with SingleTickerProviderStateMixin
    implements RouteAware {
  late final AnimationController _pulseCtrl;

  // ignore: unused_field
  // Subscription references kept to prevent premature GC of listenManual subs.
  ProviderSubscription<MatchState>? _matchStateSub;
  // ignore: unused_field
  ProviderSubscription<AsyncValue<void>>? _matchEndSub;
  // ignore: unused_field
  ProviderSubscription<AsyncValue<Map<String, dynamic>>>? _streamingStateSub;

  // ignore: unused_field
  ProviderSubscription<AsyncValue<bool>>? _wsConnectedSub;

  String? _lastConnectedMatchId;
  bool _navigatedToPostMatch = false;
  bool _waitingForResult = false; // shows loader overlay when match ends
  bool _matchEndScheduled = false; // guard against firing the 2.5s delay twice
  bool _inRoundTransition = false; // shows round overlay between rounds
  int _nextRound = 2; // round number to display in the overlay
  Timer? _fastPollTimer;

  // Active match tracking
  String? _activeMatchId;
  LiveStreamClientState _streamState = LiveStreamClientState.idle;

  // Combined HLS player: video + audio in one stream
  VideoPlayerController? _hlsController;
  String? _hlsMatchId; // guard against double-init for the same match
  bool _hlsInitializing = false;

  // FPS readout driven by VideoPlayerController listener
  int _fps = 0;
  int _lastFpsCheck = 0;
  int _fpsFrameCount = 0;
  // Guard: _checkEarlyStreamReady() fires from two code paths on entry (initState
  // postFrameCallback + _onMatchState postFrameCallback). This flag prevents the
  // second call from starting a duplicate preload before the first one completes.
  bool _earlyStartAttempted = false;

  void _onPlayerUpdate() {
    // Track FPS from VideoPlayer positions advancing (used for internal diagnostics).
    // No setState here — avoids a 1/s rebuild; the fps value was only used by the
    // debug overlay which has been removed.
    final now = DateTime.now().millisecondsSinceEpoch;
    _fpsFrameCount++;
    if (now - _lastFpsCheck >= 1000) {
      _fps = _fpsFrameCount;
      _fpsFrameCount = 0;
      _lastFpsCheck = now;
    }
  }

  @override
  void initState() {
    super.initState();
    _pulseCtrl = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 1200),
    )..repeat(reverse: true);
    _streamState = LiveStreamClientState.idle;
    ref.read(matchProvider.notifier).startFastPolling();
    _setupListeners();
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!mounted) return;
      // Unmute HLS audio now that the live screen is visible.
      try {
        ref.read(hlsPlayerServiceProvider).requestAudio();
      } catch (_) {}
      // If we have an explicit matchId, connect the WS immediately without
      // waiting for the REST poll to confirm match.status == live.
      // The WS rejects with 4004 if not ready yet and retries automatically.
      // This removes the stale-status delay (up to 2s) that occurred when
      // the cached match status was still 'upcoming' after navigation.
      if (widget.matchId != null) {
        _activeMatchId = widget.matchId;
        _connectToMatch();
        _checkEarlyStreamReady();
      }
    });
  }

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    // Subscribe to route observer so we get didPop/didPushNext immediately
    // when navigation starts — not after the 300ms exit animation completes.
    final route = ModalRoute.of(context);
    if (route != null) {
      routeObserver.subscribe(this, route);
    }
  }

  // ── RouteAware: fires at the START of navigation, before animation ──────────

  /// User pressed back — mute IMMEDIATELY (before the 300ms exit animation).
  @override
  void didPop() {
    debugPrint('[LiveMatch] RouteAware: didPop — silencing audio immediately');
    _silenceNow();
  }

  /// Another screen pushed on top — mute immediately.
  @override
  void didPushNext() {
    debugPrint('[LiveMatch] RouteAware: didPushNext — silencing audio immediately');
    _silenceNow();
  }

  /// Screen came back into view — restore audio.
  @override
  void didPopNext() {
    debugPrint('[LiveMatch] RouteAware: didPopNext — restoring audio');
    try {
      ref.read(hlsPlayerServiceProvider).requestAudio();
    } catch (_) {}
  }

  @override
  void didPush() {
    debugPrint('[LiveMatch] RouteAware: didPush');
  }

  void _silenceNow() {
    debugPrint('[LiveMatch] _silenceNow() — calling silenceAndReset on HlsPlayerService');
    try {
      ref.read(hlsPlayerServiceProvider).silenceAndReset();
    } catch (_) {}
  }

  void _setupListeners() {
    _matchStateSub = ref.listenManual<MatchState>(
      matchProvider,
      (prev, next) => _onMatchState(next),
      fireImmediately: true,
    );
    _matchEndSub = ref.listenManual<AsyncValue<void>>(matchEndProvider, (
      _,
      next,
    ) {
      if (!next.hasValue) return;
      _handleMatchEnded();
    });
    _streamingStateSub = ref.listenManual<AsyncValue<Map<String, dynamic>>>(
      streamingStateProvider,
      (_, next) {
        final data = next.valueOrNull;
        if (data == null) return;
        final state = data['state'] as String?;
        if (state == null) return;

        debugPrint('[LiveMatch] streaming_state received: "$state" | activeMatchId=$_activeMatchId | inRoundTransition=$_inRoundTransition');

        // ── Round transition: HLS is intentionally stopping between rounds ─────
        if (state == 'round_transition') {
          final round = (data['round'] as num?)?.toInt() ?? (_nextRound);
          debugPrint('[LiveMatch] round_transition → round $round. Pausing watchdog, showing overlay.');
          ref.read(hlsPlayerServiceProvider).pauseWatchdog();
          if (mounted) setState(() { _inRoundTransition = true; _nextRound = round; });
          return;
        }

        if (state != 'ready' && state != 'playing') {
          debugPrint('[LiveMatch] Ignoring streaming_state "$state" (not ready/playing)');
          return;
        }
        if (_activeMatchId == null) {
          debugPrint('[LiveMatch] streaming_state "$state" received but _activeMatchId is null — ignoring');
          return;
        }

        // Always route through HlsPlayerService — never create a local controller.
        final hlsSvc = ref.read(hlsPlayerServiceProvider);
        debugPrint('[LiveMatch] streaming_state "$state" | hlsSvc.state=${hlsSvc.state} | hlsSvc.activeMatchId=${hlsSvc.activeMatchId}');

        // If we were in a round transition, this 'ready' signal means the new
        // round's HLS is up. ALWAYS force a fresh preload() here — the service
        // still shows state=playing from the old dead round-1 HLS, so the
        // svcHasThisMatch check below would incorrectly skip the reload.
        if (_inRoundTransition) {
          debugPrint('[LiveMatch] Round transition ended — forcing fresh reload() for new round HLS.');
          hlsSvc.resumeWatchdog();
          if (mounted) setState(() => _inRoundTransition = false);
          final url = '$kStreamBaseUrl/stream/$_activeMatchId/stream.m3u8';
          hlsSvc.forceReload(_activeMatchId!, url).then((_) {
            if (mounted) {
              debugPrint('[LiveMatch] forceReload() resolved — unmuting for round 2 (mounted=true)');
              hlsSvc.unmute();
            } else {
              debugPrint('[LiveMatch] forceReload() resolved but widget unmounted — skipping unmute');
            }
          });
          return;
        }

        final svcHasThisMatch = hlsSvc.activeMatchId == _activeMatchId &&
            (hlsSvc.state == HlsPreloadState.playing ||
             hlsSvc.state == HlsPreloadState.initializing);
        if (svcHasThisMatch) {
          debugPrint('[LiveMatch] HlsPlayerService already active for $_activeMatchId (${hlsSvc.state}) — unmuting only');
          hlsSvc.unmute();
          return;
        }
        // Global service not active for this match — trigger it directly
        final url = '$kStreamBaseUrl/stream/$_activeMatchId/stream.m3u8';
        debugPrint('[LiveMatch] HlsPlayerService not active for $_activeMatchId — calling preload() url=$url');
        hlsSvc.preload(_activeMatchId!, url).then((_) {
          if (mounted) {
            debugPrint('[LiveMatch] preload() resolved — unmuting (mounted=true)');
            hlsSvc.unmute();
          } else {
            debugPrint('[LiveMatch] preload() resolved but widget unmounted — skipping unmute');
          }
        });
      },
    );

    _wsConnectedSub = ref.listenManual<AsyncValue<bool>>(wsConnectedProvider, (
      _,
      next,
    ) {
      final connected = next.valueOrNull;
      if (connected == true &&
          (_streamState == LiveStreamClientState.idle ||
              _streamState == LiveStreamClientState.error)) {
        if (mounted) setState(() => _streamState = LiveStreamClientState.wsConnected);
      }
    });
    // Global match-status events (completed/cancelled) from the backend
    ref.listenManual<AsyncValue<Map<String, dynamic>>>(
      matchStatusEventsProvider,
      (_, next) {
        next.whenData((event) {
          final eventMatchId = event['match_id'] as String?;
          final currentMatchId = widget.matchId ?? _lastConnectedMatchId;
          if (eventMatchId != currentMatchId) return;
          if (event['type'] == 'match_status_changed') {
            final status = event['status'] as String?;
            if (status == 'completed' || status == 'cancelled') {
              debugPrint('[LiveMatch] Global event: match $eventMatchId ended ($status)');
              _stopHls();
              if (eventMatchId != null) _navigateToPostMatch(eventMatchId);
            }
          }
        });
      },
    );
  }

  /// Waits for the HLS stream to naturally die (backend stops FFmpeg ~12s after
  /// match end) before navigating to post-match.  This ensures the user sees
  /// the full fight — the video is 15-30 s behind live, so a fixed 2.5 s delay
  /// would cut away before the KO clip finishes.
  ///
  /// Flow:
  ///   1. Register onStreamDied on the watchdog → fires when stream 404s/freezes.
  ///   2. Set a 20 s safety timeout in case the stream never errors cleanly.
  ///   3. Whichever fires first calls _doPostMatchNav().
  void _handleMatchEnded() {
    if (_matchEndScheduled) return;
    _matchEndScheduled = true;
    final matchId = _activeMatchId ?? widget.matchId;
    if (matchId == null) return;

    debugPrint('[LiveMatch] match ended — waiting for stream to drain before navigating');

    // Safety timeout: if the stream never cleanly 404s (e.g. the watchdog
    // grace period is still running), navigate anyway after 20 s.
    Timer? safetyTimer;
    bool navigated = false;

    void doNav() {
      if (navigated) return;
      navigated = true;
      safetyTimer?.cancel();
      // Clear the callback so the service doesn't hold a stale reference.
      try { ref.read(hlsPlayerServiceProvider).onStreamDied = null; } catch (_) {}
      if (!mounted) return;
      _stopHls();
      _navigateToPostMatch(matchId);
    }

    safetyTimer = Timer(const Duration(seconds: 20), () {
      debugPrint('[LiveMatch] Safety timeout fired — navigating to post-match');
      doNav();
    });

    // Register watchdog callback: fires when stream errors (404) or freezes.
    try {
      ref.read(hlsPlayerServiceProvider).onStreamDied = () {
        debugPrint('[LiveMatch] onStreamDied fired — stream drained, navigating');
        doNav();
      };
    } catch (_) {
      // If service is already disposed, fall back to safety timer only.
    }
  }


  /// Connects the WebSocket for the current active match.
  void _connectToMatch() {
    final matchId = widget.matchId ?? _findLiveMatchId();
    if (matchId == null) return;
    if (_lastConnectedMatchId == matchId) return;
    _lastConnectedMatchId = matchId;
    _activeMatchId = matchId;
    ref.read(matchStreamServiceProvider).connect(matchId);
  }

  void _onMatchState(MatchState state) {
    if (!mounted) return;
    final matchId = _findLiveMatchId(state.matches) ?? widget.matchId;
    if (matchId == null) return;

    if (_activeMatchId != matchId) {
      _activeMatchId = matchId;
    }

    final match = ref.read(matchProvider.notifier).matchById(matchId);
    if (match == null) return;

    if (match.status == MatchStatus.completed ||
        match.status == MatchStatus.cancelled) {
      _handleMatchEnded();
      return;
    }

    if (match.status != MatchStatus.live) {
      return;
    }

    // Connect after first frame — at this point providers may already have data.
    WidgetsBinding.instance.addPostFrameCallback((_) {
      _connectToMatch();
      // If the WS was pre-connected from battle_detail_screen and the backend
      // already signalled 'ready', kick off HLS immediately without waiting.
      _checkEarlyStreamReady();
    });
  }

  /// Eagerly starts HLS if the pre-connected WebSocket already received a
  /// 'ready' signal before the user navigated to this screen.
  void _checkEarlyStreamReady() {
    // Guard: this method is called from two postFrameCallbacks on every navigation
    // (initState + _onMatchState with fireImmediately). Without this flag both
    // would race to call hlsSvc.preload() concurrently before the first one's
    // await _teardown() completes, causing two ExoPlayer instances to be created.
    if (_earlyStartAttempted) return;
    // Guard: if match already ended, don't try to restart a dead HLS stream.
    // This fires when a bottom sheet is dismissed after match-end (didPopNext →
    // postFrameCallback), but the stream is already stopped.
    if (_matchEndScheduled) {
      debugPrint('[LiveMatch] _checkEarlyStreamReady() skipped — match already ended');
      return;
    }
    final matchId = widget.matchId ?? _findLiveMatchId();
    if (matchId == null) return;
    final currentState = ref.read(streamingStateProvider);
    currentState.whenData((data) {
      final state = data['state'] as String?;
      if (state != 'ready') return;
      // Always route through HlsPlayerService to avoid dual-controller conflict
      final hlsSvc = ref.read(hlsPlayerServiceProvider);
      if (hlsSvc.activeMatchId == matchId &&
          (hlsSvc.state == HlsPreloadState.playing ||
           hlsSvc.state == HlsPreloadState.initializing)) {
        // Already handled — just unmute
        hlsSvc.unmute();
        return;
      }
      final url = '$kStreamBaseUrl/stream/$matchId/stream.m3u8';
      debugPrint('[LiveMatch] Early HLS start via hlsService (stream was ready on entry)');
      _earlyStartAttempted = true; // must be set BEFORE the async call
      hlsSvc.preload(matchId, url).then((_) {
        if (mounted) {
          debugPrint('[LiveMatch] Early preload() resolved — unmuting (mounted=true)');
          hlsSvc.unmute();
        } else {
          debugPrint('[LiveMatch] Early preload() resolved but widget unmounted — skipping unmute');
        }
      });
    });
  }


  void _navigateToPostMatch(String matchId) {
    if (_navigatedToPostMatch || !mounted) return;
    _navigatedToPostMatch = true;
    _fastPollTimer?.cancel();
    // Show "Calculating results..." overlay immediately
    if (mounted) setState(() => _waitingForResult = true);
    ref.read(betProvider.notifier).refresh();
    ref.read(matchProvider.notifier).refresh();
    Future.delayed(const Duration(milliseconds: 1500), () {
      if (mounted) widget.onNavigate('/post-match/$matchId');
    });
  }



  /// Returns the ID of the first truly LIVE match, or null.
  /// Never falls back to non-live matches to avoid connecting a WS
  /// that the backend will immediately close with 4004.
  String? _findLiveMatchId([List<Match>? source]) {
    final matches = source ?? ref.read(matchProvider).matches;
    try {
      return matches.firstWhere((m) => m.status == MatchStatus.live).id;
    } catch (_) {
      return null;
    }
  }

  @override
  void dispose() {
    debugPrint('[LiveMatch] dispose() — muting audio FIRST (matchId=$_activeMatchId)');
    // ── Mute FIRST — before anything else, so audio never bleeds regardless
    // of how we got here (RouteAware callback, programmatic navigation, etc.)
    try {
      final hlsSvc = ref.read(hlsPlayerServiceProvider);
      hlsSvc.silenceAndReset(); // synchronous — instant audio cut
      hlsSvc.stop();            // async fire-and-forget — full ExoPlayer disposal
    } catch (_) {
      // ref may be invalidated on hot restart — ignore
    }

    _fastPollTimer?.cancel();
    _pulseCtrl.dispose();
    _stopHls(); // stops local _hlsController (no-op if already stopped above)

    routeObserver.unsubscribe(this);
    debugPrint('[LiveMatch] dispose() complete');
    super.dispose();
  }

  // ── HLS player helpers ──────────────────────────────────────────────────────

  void _startHls(String matchId) {
    if (_hlsMatchId == matchId) return; // already initing/playing this match
    _stopHls();           // clears _hlsMatchId, controller, etc.
    _hlsMatchId = matchId; // set AFTER stop so guard in _initHls passes
    _initHls(matchId, attempt: 1);
  }

  Future<void> _initHls(String matchId, {required int attempt}) async {
    if (!mounted || _hlsMatchId != matchId) return;
    _hlsInitializing = true;

    // Backend signals when HLS is ready, so we should be able to init immediately.
    // Still allow retries for network issues, but fewer and with shorter delays.
    final url = '$kStreamBaseUrl/stream/$matchId/stream.m3u8';

    if (attempt > 1) {
      // Retry delay: 1s (network hiccup recovery)
      await Future.delayed(const Duration(seconds: 1));
    }
    if (!mounted || _hlsMatchId != matchId) return;

    final controller = VideoPlayerController.networkUrl(
      Uri.parse(url),
      videoPlayerOptions: VideoPlayerOptions(mixWithOthers: false),
    );
    _hlsController = controller;
    controller.addListener(_onPlayerUpdate);

    try {
      await controller.initialize();
      if (!mounted || _hlsMatchId != matchId) {
        controller.dispose();
        return;
      }
      controller.setVolume(1.0);
      controller.play();
      _hlsInitializing = false;
      if (mounted) setState(() {});
      debugPrint('[HLS] ▶ Playing $url (attempt $attempt)');
    } catch (e) {
      debugPrint('[HLS] ✗ Init failed (attempt $attempt): $e');
      controller.removeListener(_onPlayerUpdate);
      controller.dispose();
      if (!mounted || _hlsMatchId != matchId) return;
      if (attempt < 3) {
        debugPrint('[HLS] Retrying in 1s (attempt ${attempt + 1}/3)...');
        _initHls(matchId, attempt: attempt + 1);
      } else {
        debugPrint('[HLS] ✗ Giving up after $attempt attempts for $matchId');
        _hlsInitializing = false;
        if (mounted) setState(() {});
      }
    }
  }

  void _stopHls() {
    _hlsMatchId = null;
    _hlsInitializing = false;
    final ctrl = _hlsController;
    _hlsController = null;
    ctrl?.removeListener(_onPlayerUpdate);
    ctrl?.dispose();
    // Also stop the global service to ensure full cleanup regardless of
    // which path triggered the stop (match end event, navigation, etc.)
    try {
      ref.read(hlsPlayerServiceProvider).stop();
    } catch (_) {}
  }

  @override
  Widget build(BuildContext context) {
    final matchState = ref.watch(matchProvider);
    final matches = matchState.matches;

    // Retry connecting when match list loads / updates. Also detects when
    // REST polling catches a completed match (missed WS event or cold-start).
    ref.listen<MatchState>(matchProvider, (prev, next) {
      final id = widget.matchId ?? _findLiveMatchId();
      if (id != null) {
        final updated = next.matches.cast<Match?>().firstWhere(
          (m) => m?.id == id,
          orElse: () => null,
        );
        if (updated != null &&
            (updated.status == MatchStatus.completed ||
                updated.status == MatchStatus.cancelled)) {
          _handleMatchEnded();
          return;
        }
      }
      _connectToMatch();
    });

    final matchId = widget.matchId ?? _findLiveMatchId();
    final match = matchId != null
        ? matches.cast<Match?>().firstWhere(
            (m) => m?.id == matchId,
            orElse: () => null,
          )
        : null;

    if (match == null) {
      final isStillLoading = !matchState.hasLoaded;
      return AppShell(
        activeTab: NavTab.arena,
        onNavigate: (slug) => widget.onNavigate(routeFor(slug)),
        content: Center(
          child: isStillLoading
              ? const IKLoader(size: 44)
              : Column(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    Text(
                      'Match not found',
                      style: bodyStyle(color: Palette.muted),
                    ),
                    const SizedBox(height: 16),
                    OrnateButton(
                      label: 'Back',
                      color: Palette.muted,
                      onTap: () => widget.onNavigate('/arena-list'),
                    ),
                  ],
                ),
        ),
      );
    }

    final viewerAsync = ref.watch(viewerCountProvider);
    final streamingStateAsync = ref.watch(streamingStateProvider);

    // Reactive: rebuilds when pre-loaded global controller appears or changes
    final hlsCtrlAsync = ref.watch(hlsControllerProvider);
    final hlsStateAsync = ref.watch(hlsPreloadStateProvider);
    final preloadedCtrl = hlsCtrlAsync.valueOrNull;
    // Exclusively use global controller — no local fallback
    final hlsCtrl = (preloadedCtrl != null && preloadedCtrl.value.isInitialized)
        ? preloadedCtrl
        : null;
    final isGlobalHlsReady = hlsCtrl != null;
    final hlsPreloadState = hlsStateAsync.valueOrNull ?? HlsPreloadState.idle;
    // Error = explicit failure (timeout/init crash). All other non-playing states
    // (idle, initializing, stopped) are normal startup gaps — show a spinner, not an error.
    final isHlsError = hlsPreloadState == HlsPreloadState.error;

    // Determine what message to show when stream isn't playing
    String streamStatusMessage = 'Stream starting...';
    streamingStateAsync.whenData((data) {
      final state = data['state'] as String?;
      final error = data['error'] as String?;
      switch (state) {
        case 'initializing':
          streamStatusMessage = 'Initializing stream...';
          break;
        case 'ready':
          streamStatusMessage = 'Stream ready, loading...';
          break;
        case 'error':
          streamStatusMessage = error ?? 'Stream error';
          break;
        default:
          streamStatusMessage = 'Stream starting...';
      }
    });

    // ── Waiting-for-result overlay (shown immediately when match ends) ─────────
    if (_waitingForResult) {
      return Container(
        color: Palette.black,
        child: Center(
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              const IKLoader(size: 48),
              const SizedBox(height: 20),
              Text(
                'Calculating Results...',
                style: displayStyle(size: 22, color: Palette.gold),
              ),
              const SizedBox(height: 8),
              Text(
                'Please wait',
                style: bodyStyle(size: 14, color: Palette.muted),
              ),
            ],
          ),
        ),
      );
    }

    return AppShell(
      activeTab: NavTab.arena,
      onNavigate: (slug) => widget.onNavigate(routeFor(slug)),
      content: Column(
        children: [
          // Header row: LIVE indicator + label
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 2),
            child: Row(
              mainAxisAlignment: MainAxisAlignment.spaceBetween,
              children: [
                Row(
                  children: [
                    FadeTransition(
                      opacity: Tween<double>(begin: 0.3, end: 1.0).animate(
                        CurvedAnimation(
                          parent: _pulseCtrl,
                          curve: Curves.easeInOut,
                        ),
                      ),
                      child: Container(
                        width: 8,
                        height: 8,
                        decoration: const BoxDecoration(
                          shape: BoxShape.circle,
                          color: Palette.red,
                        ),
                      ),
                    ),
                    const SizedBox(width: 6),
                    Text(
                      'LIVE',
                      style: bodyStyle(size: 14, color: Palette.red),
                    ),
                    const SizedBox(width: 12),
                    viewerAsync.when(
                      data: (count) => Text(
                        '$count watching',
                        style: bodyStyle(size: 12, color: Palette.muted),
                      ),
                      loading: () => const SizedBox.shrink(),
                      error: (e, s) => const SizedBox.shrink(),
                    ),
                  ],
                ),
                Text(
                  match.label,
                  style: bodyStyle(size: 14, color: Palette.muted),
                ),
              ],
            ),
          ),
          const SizedBox(height: 8),

          // Combined HLS video+audio player (with round transition overlay on top)
          Stack(
            children: [
              // ── Video layer ──
              Stack(
                children: [
                  AspectRatio(
                    aspectRatio: 4 / 3,
                    child: Container(
                      color: Palette.black,
                      child: isGlobalHlsReady
                          ? VideoPlayer(hlsCtrl!)
                          : Center(
                              child: Column(
                                mainAxisSize: MainAxisSize.min,
                                children: [
                                  // Only show dead camera on explicit HLS error.
                                  // All other gaps (idle, initializing, stopped) show a spinner.
                                  if (!isHlsError) const IKLoader(size: 44)
                                  else const Icon(Icons.videocam_off,
                                      color: Palette.muted, size: 36),
                                  const SizedBox(height: 12),
                                  Text(
                                    isHlsError
                                        ? 'Stream unavailable'
                                        : streamStatusMessage,
                                    style: const TextStyle(
                                        color: Palette.muted, fontSize: 13),
                                  ),
                                ],
                              ),
                            ),
                    ),
                  ),
                  // Connection-lost banner
                  Builder(builder: (ctx) {
                    final svc = ref.watch(matchStreamServiceProvider);
                    if (!svc.hasGivenUp) return const SizedBox.shrink();
                    return Positioned(
                      bottom: 0, left: 0, right: 0,
                      child: GestureDetector(
                        onTap: () {
                          final id = widget.matchId ?? _lastConnectedMatchId;
                          if (id != null) {
                            _lastConnectedMatchId = null;
                            svc.resetAndReconnect(id);
                          }
                        },
                        child: Container(
                          padding: const EdgeInsets.symmetric(vertical: 8),
                          color: Palette.red.withValues(alpha: 0.85),
                          child: const Row(
                            mainAxisAlignment: MainAxisAlignment.center,
                            children: [
                              Icon(Icons.wifi_off, size: 14, color: Colors.white),
                              SizedBox(width: 6),
                              Text('Connection lost — tap to retry',
                                  style: TextStyle(
                                    color: Colors.white,
                                    fontSize: 12,
                                    fontWeight: FontWeight.w600,
                                  ),
                                ),
                              ],
                            ),
                          ),
                        ),
                      );
                    },
                  ),
                ],
              ),

              // ── Round-transition overlay ────────────────────────────────────
              // Sits ON TOP of the video (same Stack) so it doesn't push the
              // Column down and cause RenderFlex overflow.
              if (_inRoundTransition)
                Positioned.fill(
                  child: Container(
                    color: Palette.black.withValues(alpha: 0.92),
                    child: Column(
                      mainAxisAlignment: MainAxisAlignment.center,
                      children: [
                        const IKLoader(size: 40),
                        const SizedBox(height: 16),
                        Text(
                          'ROUND $_nextRound',
                          style: displayStyle(size: 28, color: Palette.gold),
                        ),
                        const SizedBox(height: 4),
                        Text(
                          'starting…',
                          style: bodyStyle(size: 14, color: Palette.secondary),
                        ),
                      ],
                    ),
                  ),
                ),
            ],
          ),

          const SizedBox(height: 8),

          // HUD: round dots — self-updating, never rebuilds parent screen
          const _GameHud(),
          const SizedBox(height: 6),

          // Fighter names

          Text(
            '${match.fighter1?.name ?? '---'} V/S ${match.fighter2?.name ?? '---'}',
            style: displayStyle(size: 22, color: Palette.gold),
          ),
          const SizedBox(height: 4),
          Text(
            '${match.fighter1?.llmModel ?? ''} vs ${match.fighter2?.llmModel ?? ''}',
            style: bodyStyle(size: 14, color: Palette.secondary),
          ),
          const SizedBox(height: 12),

          // Bet pool breakdown — both sides always visible during live match
          _LiveBetPools(match: match),
          const SizedBox(height: 12),

          // Place Bet button (only if betting is open)
          if (match.bettingOpen)
            OrnateButton(
              label: 'Place Bet',
              color: Palette.gold,
              onTap: () {
                showModalBottomSheet<void>(
                  context: context,
                  isScrollControlled: true,
                  backgroundColor: Colors.transparent,
                  builder: (_) => BetBottomSheet(match: match),
                );
              },
            ),
          const SizedBox(height: 8),
        ],
      ),
    );
  }
}


/// Compact bet pool breakdown shown during live matches.
/// Shows both fighters' pool percentages and SKR amounts at a glance.
class _LiveBetPools extends StatelessWidget {
  const _LiveBetPools({required this.match});
  final Match match;

  String _fmt(double v) {
    final s = v.toStringAsFixed(2);
    return s.replaceFirst(RegExp(r'\.?0+$'), '');
  }

  @override
  Widget build(BuildContext context) {
    final token = RuntimeClientConfig.instance.tokenSymbol;
    final odds = match.odds;
    final sideAPool = odds.fighter1Pool > 0
        ? odds.fighter1Pool
        : match.totalPool * odds.fighter1PoolPct;
    final sideBPool = odds.fighter2Pool > 0
        ? odds.fighter2Pool
        : match.totalPool * odds.fighter2PoolPct;

    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 8),
      child: Column(
        children: [
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Expanded(
                child: _PoolSide(
                  name: match.fighter1?.name ?? 'Fighter 1',
                  pool: sideAPool,
                  pct: odds.fighter1PoolPct,
                  token: token,
                ),
              ),
              Container(
                width: 1,
                height: 48,
                margin: const EdgeInsets.symmetric(horizontal: 12),
                color: Palette.border,
              ),
              Expanded(
                child: _PoolSide(
                  name: match.fighter2?.name ?? 'Fighter 2',
                  pool: sideBPool,
                  pct: odds.fighter2PoolPct,
                  token: token,
                  alignRight: true,
                ),
              ),
            ],
          ),
          const SizedBox(height: 6),
          Text(
            '${_fmt(match.totalPool)} $token total · ${match.activeBets} bets',
            style: bodyStyle(size: 11, color: Palette.muted),
            textAlign: TextAlign.center,
          ),
        ],
      ),
    );
  }
}

class _PoolSide extends StatelessWidget {
  const _PoolSide({
    required this.name,
    required this.pool,
    required this.pct,
    required this.token,
    this.alignRight = false,
  });

  final String name;
  final double pool;
  final double pct;
  final String token;
  final bool alignRight;

  @override
  Widget build(BuildContext context) {
    final align = alignRight ? TextAlign.right : TextAlign.left;
    final crossAlign =
        alignRight ? CrossAxisAlignment.end : CrossAxisAlignment.start;
    final poolStr = pool > 0 ? '${pool.toStringAsFixed(1)} $token' : '—';
    return Column(
      crossAxisAlignment: crossAlign,
      children: [
        Text(
          name.toUpperCase(),
          maxLines: 1,
          overflow: TextOverflow.ellipsis,
          textAlign: align,
          style: bodyStyle(size: 10, color: Palette.muted, letterSpacing: 0.5),
        ),
        const SizedBox(height: 3),
        Text(
          '${(pct * 100).toStringAsFixed(1)}%',
          textAlign: align,
          style: bodyStyle(
            size: 20,
            color: Palette.gold,
            weight: FontWeight.w700,
          ),
        ),
        Text(
          poolStr,
          textAlign: align,
          style: bodyStyle(size: 12, color: Palette.secondary),
        ),
      ],
    );
  }
}

/// Isolated HUD ConsumerWidget: watches [gameStateProvider] directly so that
/// 5Hz game-state updates never trigger a rebuild of [LiveMatchScreen].
class _GameHud extends ConsumerWidget {
  const _GameHud();

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final gameState = ref.watch(gameStateProvider).valueOrNull;
    if (gameState == null) return const SizedBox.shrink();
    return _RoundDots(
      bestOf: gameState.bestOf,
      roundsWonP1: gameState.roundsWonP1,
      roundsWonP2: gameState.roundsWonP2,
      currentRound: gameState.currentRound,
    );
  }
}


/// Round score dots — shows filled circles for won rounds.
class _RoundDots extends StatelessWidget {
  const _RoundDots({
    required this.bestOf,
    required this.roundsWonP1,
    required this.roundsWonP2,
    required this.currentRound,
  });

  final int bestOf;
  final int roundsWonP1;
  final int roundsWonP2;
  final int currentRound;

  @override
  Widget build(BuildContext context) {
    final roundsToWin = (bestOf ~/ 2) + 1;
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 4),
      child: Row(
        mainAxisAlignment: MainAxisAlignment.center,
        children: [
          // P1 round dots
          _dots(roundsWonP1, roundsToWin, const Color(0xFF4CAF50)),
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 12),
            child: Text(
              'R$currentRound',
              style: bodyStyle(size: 12, color: Palette.muted),
            ),
          ),
          // P2 round dots
          _dots(roundsWonP2, roundsToWin, const Color(0xFFF44336)),
        ],
      ),
    );
  }

  Widget _dots(int won, int total, Color color) {
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: List.generate(total, (i) {
        final isWon = i < won;
        return Padding(
          padding: const EdgeInsets.symmetric(horizontal: 3),
          child: Container(
            width: 10,
            height: 10,
            decoration: BoxDecoration(
              shape: BoxShape.circle,
              color: isWon ? color : Colors.transparent,
              border: Border.all(
                color: isWon ? color : Palette.muted.withValues(alpha: 0.4),
                width: 1.5,
              ),
            ),
          ),
        );
      }),
    );
  }
}

/// Reusable pulsing dot widget
class PulsingDot extends StatefulWidget {
  const PulsingDot({super.key, this.color = Palette.red, this.size = 8});
  final Color color;
  final double size;

  @override
  State<PulsingDot> createState() => _PulsingDotState();
}

class _PulsingDotState extends State<PulsingDot>
    with SingleTickerProviderStateMixin {
  late final AnimationController _ctrl;

  @override
  void initState() {
    super.initState();
    _ctrl = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 1200),
    )..repeat(reverse: true);
  }

  @override
  void dispose() {
    _ctrl.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return FadeTransition(
      opacity: Tween<double>(
        begin: 0.3,
        end: 1.0,
      ).animate(CurvedAnimation(parent: _ctrl, curve: Curves.easeInOut)),
      child: Container(
        width: widget.size,
        height: widget.size,
        decoration: BoxDecoration(shape: BoxShape.circle, color: widget.color),
      ),
    );
  }
}
