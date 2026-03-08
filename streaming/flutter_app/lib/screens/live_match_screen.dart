import 'dart:async';
import 'dart:collection';
import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:video_player/video_player.dart';
import '../core/constants.dart';
import '../core/palette.dart';
import '../core/typography.dart';
import '../router.dart';
import '../models/match.dart';
import '../models/game_state.dart';
import '../providers/bet_provider.dart';
import '../providers/match_provider.dart';
import '../providers/match_stream_provider.dart';
import '../services/match_stream_service.dart';
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

class _ScheduledHudState {
  const _ScheduledHudState(this.state, this.dueAtMs);
  final GameState state;
  final int dueAtMs;
}

class LiveMatchScreen extends ConsumerStatefulWidget {
  const LiveMatchScreen({super.key, required this.onNavigate, this.matchId});
  final void Function(String) onNavigate;
  final String? matchId;

  @override
  ConsumerState<LiveMatchScreen> createState() => _LiveMatchScreenState();
}

class _LiveMatchScreenState extends ConsumerState<LiveMatchScreen>
    with SingleTickerProviderStateMixin {
  late final AnimationController _pulseCtrl;
  static const _hlsFallbackDelay = Duration(seconds: 8);
  static const _hlsRetryBackoff = <Duration>[
    Duration(seconds: 1),
    Duration(seconds: 2),
    Duration(seconds: 4),
  ];
  static const _maxHlsInitAttempts = 4;

  ProviderSubscription<MatchState>? _matchStateSub;
  ProviderSubscription<AsyncValue<void>>? _matchEndSub;
  ProviderSubscription<AsyncValue<StreamingStateEvent>>? _streamingStateSub;
  ProviderSubscription<AsyncValue<GameState>>? _gameStateSub;
  ProviderSubscription<AsyncValue<bool>>? _wsConnectedSub;

  String? _activeMatchId;
  LiveStreamClientState _streamState = LiveStreamClientState.idle;

  VideoPlayerController? _videoController;
  String? _videoMatchId;
  String? _lastHlsUrl;
  int _hlsInitAttempts = 0;
  int _hlsInitToken = 0;
  bool _playerInitializing = false;
  bool _lastIsBuffering = false;
  int _rebufferCount = 0;
  int _playerInitCount = 0;
  int _playerDisposeCount = 0;
  Future<void> _pendingControllerDispose = Future<void>.value();

  final Queue<_ScheduledHudState> _hudQueue = Queue<_ScheduledHudState>();
  Timer? _hudPumpTimer;
  Timer? _hlsFallbackTimer;
  Timer? _hlsRetryTimer;
  GameState? _displayGameState;

  bool _disposed = false;
  bool _terminal = false;
  bool _navigatedToPostMatch = false;

  void _log(String tag, String message, {String? matchId}) {
    if (!kDebugMode) return;
    final id = matchId ?? _activeMatchId ?? '-';
    debugPrint('[$tag][$id] $message');
  }

  @override
  void initState() {
    super.initState();
    _pulseCtrl = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 1200),
    )..repeat(reverse: true);
    _setStreamState(LiveStreamClientState.idle, reason: 'screen-init');
    ref.read(matchProvider.notifier).startFastPolling();
    _setupListeners();
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
      _handleMatchEnded(reason: 'ws-match_ended');
    });
    _streamingStateSub = ref.listenManual<AsyncValue<StreamingStateEvent>>(
      streamingStateProvider,
      (_, next) {
        final event = next.valueOrNull;
        if (event == null) return;
        _onStreamingState(event);
      },
    );
    _gameStateSub = ref.listenManual<AsyncValue<GameState>>(gameStateProvider, (
      _,
      next,
    ) {
      final gs = next.valueOrNull;
      if (gs == null) return;
      _enqueueHudState(gs);
    });
    _wsConnectedSub = ref.listenManual<AsyncValue<bool>>(wsConnectedProvider, (
      _,
      next,
    ) {
      final connected = next.valueOrNull;
      if (connected == true &&
          !_terminal &&
          (_streamState == LiveStreamClientState.idle ||
              _streamState == LiveStreamClientState.error)) {
        _setStreamState(
          LiveStreamClientState.wsConnected,
          reason: 'websocket-connected',
        );
      }
    });
  }

  void _onMatchState(MatchState state) {
    if (_disposed) return;
    final matchId = _resolveTargetMatchId(state.matches);
    if (matchId == null) return;

    if (_activeMatchId != matchId) {
      _switchMatch(matchId);
    }

    final match = ref.read(matchProvider.notifier).matchById(matchId);
    if (match == null) return;

    if (match.status == MatchStatus.completed ||
        match.status == MatchStatus.cancelled) {
      _handleMatchEnded(reason: 'match-status-${match.status.name}');
      return;
    }

    if (match.status != MatchStatus.live) {
      return;
    }

    _ensureWsConnected(matchId);
    _scheduleHlsFallback(matchId);
    if (_streamState == LiveStreamClientState.wsConnected ||
        _streamState == LiveStreamClientState.idle ||
        _streamState == LiveStreamClientState.error) {
      _setStreamState(
        LiveStreamClientState.streamInitializing,
        reason: 'match-live-awaiting-stream-ready',
      );
    }
  }

  void _switchMatch(String matchId) {
    _log('MATCH', 'Switching to match', matchId: matchId);
    _activeMatchId = matchId;
    _terminal = false;
    _navigatedToPostMatch = false;
    _cancelHlsTimers();
    _disposeVideoController();
    _clearHudQueue();
    if (mounted) {
      setState(() => _displayGameState = null);
    } else {
      _displayGameState = null;
    }
    _hlsInitAttempts = 0;
    _setStreamState(LiveStreamClientState.idle, reason: 'match-switch');
  }

  void _ensureWsConnected(String matchId) {
    final streamSvc = ref.read(matchStreamServiceProvider);
    if (streamSvc.matchId == matchId &&
        (streamSvc.isConnected || streamSvc.isConnecting)) {
      return;
    }
    _log('WS', 'Connect requested', matchId: matchId);
    streamSvc.connect(matchId);
  }

  void _onStreamingState(StreamingStateEvent event) {
    final matchId = _activeMatchId;
    if (_disposed || _terminal || matchId == null) return;
    if (event.matchId != null && event.matchId != matchId) return;

    _log(
      'WS',
      'streaming_state=${event.status} hls_url=${event.hlsUrl}',
      matchId: matchId,
    );
    if (event.isReady) {
      _setStreamState(LiveStreamClientState.streamReady, reason: 'ws-ready');
      _startHls(matchId, reason: 'ws-ready', hintedHlsUrl: event.hlsUrl);
    } else if (event.status == 'initializing' || event.status == 'starting') {
      _setStreamState(
        LiveStreamClientState.streamInitializing,
        reason: 'ws-${event.status}',
      );
    }
  }

  void _enqueueHudState(GameState state) {
    if (_disposed || _terminal) return;
    final nowMs = DateTime.now().millisecondsSinceEpoch;
    final dueMs = state.timestamp == null
        ? nowMs
        : state.timestamp!.millisecondsSinceEpoch + kHudDisplayDelayMs;
    _hudQueue.add(_ScheduledHudState(state, dueMs));
    _startHudPump();
  }

  void _startHudPump() {
    _hudPumpTimer ??= Timer.periodic(const Duration(milliseconds: 40), (_) {
      if (_disposed || _terminal) {
        _clearHudQueue();
        return;
      }
      final nowMs = DateTime.now().millisecondsSinceEpoch;
      GameState? latestDue;
      while (_hudQueue.isNotEmpty && _hudQueue.first.dueAtMs <= nowMs) {
        latestDue = _hudQueue.removeFirst().state;
      }
      if (latestDue != null) {
        _log(
          'SYNC',
          'HUD frame=${latestDue.frameId} applied (delayMs=$kHudDisplayDelayMs)',
        );
        if (mounted) {
          setState(() => _displayGameState = latestDue);
        }
      }
      if (_hudQueue.isEmpty) {
        _hudPumpTimer?.cancel();
        _hudPumpTimer = null;
      }
    });
  }

  void _clearHudQueue() {
    _hudQueue.clear();
    _hudPumpTimer?.cancel();
    _hudPumpTimer = null;
  }

  void _scheduleHlsFallback(String matchId) {
    if (_hlsFallbackTimer?.isActive ?? false) return;
    if (!_canStartHls(matchId)) return;
    _hlsFallbackTimer = Timer(_hlsFallbackDelay, () {
      if (_disposed || _terminal || _activeMatchId != matchId) return;
      final match = ref.read(matchProvider.notifier).matchById(matchId);
      final isLive = match?.status == MatchStatus.live;
      if (!isLive || !_canStartHls(matchId)) {
        _log(
          'HLS',
          'Fallback start skipped (isLive=$isLive, canStart=${_canStartHls(matchId)})',
          matchId: matchId,
        );
        return;
      }
      _log('HLS', 'Fallback start after 8s (no ws ready)', matchId: matchId);
      _startHls(matchId, reason: 'fallback-timeout');
    });
  }

  bool _canStartHls(String matchId) {
    if (_playerInitializing) return false;
    final ctrl = _videoController;
    if (ctrl == null || _videoMatchId != matchId) return true;
    return !(ctrl.value.isInitialized || ctrl.value.isPlaying);
  }

  String _canonicalHlsUrl(String matchId) =>
      '$kStreamBaseUrl/stream/$matchId/stream.m3u8';

  String _resolveHlsUrl(String matchId, String? hintedHlsUrl) {
    final canonical = _canonicalHlsUrl(matchId);
    if (hintedHlsUrl == null || hintedHlsUrl.trim().isEmpty) return canonical;

    final raw = hintedHlsUrl.trim();
    if (raw.contains('/stream/audio/')) return canonical;

    final uri = Uri.tryParse(raw);
    if (uri == null) return canonical;

    if (uri.hasScheme) {
      if (uri.path.contains('/stream/audio/')) return canonical;
      return uri.toString();
    }

    if (raw.startsWith('/')) {
      if (raw.contains('/stream/audio/')) return canonical;
      return '$kStreamBaseUrl$raw';
    }

    return canonical;
  }

  Future<void> _startHls(
    String matchId, {
    required String reason,
    String? hintedHlsUrl,
  }) async {
    if (_disposed || _terminal || _activeMatchId != matchId) return;
    if (!_canStartHls(matchId)) {
      _log(
        'PLAYER',
        'Start skipped (already initializing/playing)',
        matchId: matchId,
      );
      return;
    }

    final match = ref.read(matchProvider.notifier).matchById(matchId);
    if (match?.status != MatchStatus.live) {
      _log(
        'HLS',
        'Start skipped because match not live (${match?.status.name})',
        matchId: matchId,
      );
      return;
    }

    _hlsRetryTimer?.cancel();
    _hlsRetryTimer = null;
    _hlsFallbackTimer?.cancel();
    _hlsFallbackTimer = null;

    _playerInitializing = true;
    _hlsInitAttempts += 1;
    _playerInitCount += 1;
    final attempt = _hlsInitAttempts;
    final initToken = ++_hlsInitToken;
    final hlsUrl = _resolveHlsUrl(matchId, hintedHlsUrl);
    _lastHlsUrl = hlsUrl;

    _setStreamState(
      LiveStreamClientState.playerInitializing,
      reason: 'hls-start-$reason-attempt-$attempt',
    );
    _log(
      'HLS',
      'Initializing ($reason) attempt=$attempt url=$hlsUrl',
      matchId: matchId,
    );

    await _pendingControllerDispose;
    if (!_isInitStillValid(matchId, initToken)) return;

    final controller = VideoPlayerController.networkUrl(
      Uri.parse(hlsUrl),
      videoPlayerOptions: VideoPlayerOptions(mixWithOthers: false),
    );

    try {
      await controller.initialize().timeout(const Duration(seconds: 12));
      if (!_isInitStillValid(matchId, initToken)) {
        _log('PLAYER', 'Stale initialize completion ignored', matchId: matchId);
        await controller.dispose();
        return;
      }
      await _disposeVideoController(
        invalidateInitToken: false,
        resetInitializing: false,
      );
      _videoController = controller;
      _videoMatchId = matchId;
      controller.addListener(_onVideoValueChanged);
      await controller.setVolume(1.0);
      await controller.play();
      if (!_isInitStillValid(matchId, initToken)) {
        await _disposeVideoController(resetInitializing: false);
        return;
      }
      _hlsInitAttempts = 0;
      _setStreamState(LiveStreamClientState.playing, reason: 'player-playing');
      _log(
        'PLAYER',
        'Playback started (init_count=$_playerInitCount, rebuffer_count=$_rebufferCount)',
        matchId: matchId,
      );
    } catch (e) {
      await controller.dispose();
      if (!_isInitStillValid(matchId, initToken)) return;
      _setStreamState(LiveStreamClientState.error, reason: 'hls-init-failed');
      _log('HLS', 'Initialize failed: $e', matchId: matchId);
      _scheduleHlsRetry(matchId, error: e);
    } finally {
      if (_activeMatchId == matchId) {
        _playerInitializing = false;
      }
    }
  }

  bool _isInitStillValid(String matchId, int initToken) {
    return !_disposed &&
        !_terminal &&
        _activeMatchId == matchId &&
        _hlsInitToken == initToken;
  }

  void _onVideoValueChanged() {
    final controller = _videoController;
    if (controller == null || _disposed || _terminal) return;
    final value = controller.value;
    if (_lastIsBuffering != value.isBuffering) {
      _lastIsBuffering = value.isBuffering;
      if (value.isBuffering) {
        _rebufferCount += 1;
      }
      _log(
        'PLAYER',
        'Buffering=${value.isBuffering} rebuffers=$_rebufferCount pos=${value.position.inMilliseconds}ms',
        matchId: _videoMatchId,
      );
    }
    if (value.hasError) {
      _log(
        'PLAYER',
        'Controller error: ${value.errorDescription}',
        matchId: _videoMatchId,
      );
      _setStreamState(LiveStreamClientState.error, reason: 'player-error');
      if (_videoMatchId != null) {
        final failedMatchId = _videoMatchId!;
        _disposeVideoController();
        _scheduleHlsRetry(
          failedMatchId,
          error: value.errorDescription ?? 'player-error',
        );
      }
      return;
    }
    if (value.isInitialized &&
        value.isPlaying &&
        _streamState != LiveStreamClientState.playing) {
      _setStreamState(
        LiveStreamClientState.playing,
        reason: 'listener-playing',
      );
    }
  }

  void _scheduleHlsRetry(String matchId, {required Object error}) {
    if (_disposed || _terminal || _activeMatchId != matchId) return;
    if (_hlsRetryTimer?.isActive ?? false) return;
    if (_hlsInitAttempts >= _maxHlsInitAttempts) {
      _log('HLS', 'Retry limit reached, giving up', matchId: matchId);
      return;
    }
    final match = ref.read(matchProvider.notifier).matchById(matchId);
    if (match?.status != MatchStatus.live) {
      _log(
        'HLS',
        'Retry blocked because match not live (${match?.status.name})',
        matchId: matchId,
      );
      return;
    }

    final errText = error.toString();
    final is404 = errText.contains('404');
    if (is404 && _terminal) {
      _log('HLS', '404 after terminal state, no retry', matchId: matchId);
      return;
    }

    final delay =
        _hlsRetryBackoff[(_hlsInitAttempts - 1).clamp(
          0,
          _hlsRetryBackoff.length - 1,
        )];
    _log(
      'HLS',
      'Retry scheduled in ${delay.inSeconds}s (attempt ${_hlsInitAttempts + 1})',
      matchId: matchId,
    );
    _hlsRetryTimer = Timer(delay, () {
      if (_disposed || _terminal || _activeMatchId != matchId) return;
      _disposeVideoController();
      unawaited(_startHls(
        matchId,
        reason: is404 ? 'retry-404-transient' : 'retry-init-failure',
        hintedHlsUrl: _lastHlsUrl,
      ));
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

  String? _resolveTargetMatchId([List<Match>? matches]) {
    return widget.matchId ?? _findLiveMatchId(matches);
  }

  void _handleMatchEnded({required String reason}) {
    final matchId = _activeMatchId;
    if (matchId == null) return;
    if (_terminal) return;
    _terminal = true;
    _log('MATCH', 'Terminal state reached ($reason)', matchId: matchId);
    _cancelHlsTimers();
    _clearHudQueue();
    _displayGameState = null;
    _disposeVideoController();
    ref.read(matchStreamServiceProvider).markTerminal(matchId);
    _setStreamState(LiveStreamClientState.ended, reason: reason);
    _navigateToPostMatch(matchId);
  }

  void _cancelHlsTimers() {
    if ((_hlsFallbackTimer?.isActive ?? false) ||
        (_hlsRetryTimer?.isActive ?? false)) {
      _log('HLS', 'Cancelling fallback/retry timers');
    }
    _hlsFallbackTimer?.cancel();
    _hlsFallbackTimer = null;
    _hlsRetryTimer?.cancel();
    _hlsRetryTimer = null;
  }

  Future<void> _disposeVideoController({
    bool invalidateInitToken = true,
    bool resetInitializing = true,
  }) {
    if (invalidateInitToken) _hlsInitToken++;
    if (resetInitializing) _playerInitializing = false;
    final ctrl = _videoController;
    final videoMatchId = _videoMatchId;
    _videoController = null;
    _videoMatchId = null;
    _lastIsBuffering = false;
    if (ctrl == null) return _pendingControllerDispose;

    _playerDisposeCount += 1;
    _log(
      'PLAYER',
      'Disposing controller (init_count=$_playerInitCount, dispose_count=$_playerDisposeCount, rebuffer_count=$_rebufferCount)',
      matchId: videoMatchId,
    );
    ctrl.removeListener(_onVideoValueChanged);
    _pendingControllerDispose = ctrl.dispose().catchError((Object error) {
      _log('PLAYER', 'Controller dispose failed: $error', matchId: videoMatchId);
    });
    return _pendingControllerDispose;
  }

  void _setStreamState(LiveStreamClientState next, {required String reason}) {
    if (_streamState == next) return;
    _log('MATCH', 'State ${_streamState.name} -> ${next.name} ($reason)');
    if (!mounted) return;
    setState(() => _streamState = next);
  }

  void _navigateToPostMatch(String matchId) {
    if (_navigatedToPostMatch || !mounted) return;
    _navigatedToPostMatch = true;
    ref.read(betProvider.notifier).refresh();
    ref.read(matchProvider.notifier).refresh();
    Future.delayed(const Duration(milliseconds: 350), () {
      if (mounted) widget.onNavigate('/post-match/$matchId');
    });
  }

  @override
  void dispose() {
    _disposed = true;
    _matchStateSub?.close();
    _matchStateSub = null;
    _matchEndSub?.close();
    _matchEndSub = null;
    _streamingStateSub?.close();
    _streamingStateSub = null;
    _gameStateSub?.close();
    _gameStateSub = null;
    _wsConnectedSub?.close();
    _wsConnectedSub = null;
    _cancelHlsTimers();
    _clearHudQueue();
    unawaited(_disposeVideoController());
    ref.read(matchStreamServiceProvider).disconnect();
    ref.read(matchProvider.notifier).stopFastPolling();
    _pulseCtrl.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final matchState = ref.watch(matchProvider);
    final matches = matchState.matches;

    final matchId = _resolveTargetMatchId(matches);
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
    final gameState = _displayGameState;
    final controller = _videoController;

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

          // Combined HLS video+audio frame
          Stack(
            children: [
              _LiveVideoFrame(controller: controller, state: _streamState),
              // Connection-lost banner — shows when WS has given up reconnecting
              Builder(
                builder: (ctx) {
                  final svc = ref.watch(matchStreamServiceProvider);
                  if (!svc.hasGivenUp) return const SizedBox.shrink();
                  return Positioned(
                    bottom: 0,
                    left: 0,
                    right: 0,
                    child: GestureDetector(
                      onTap: () {
                        final id = _activeMatchId;
                        if (id != null) {
                          _log('WS', 'Manual reconnect requested', matchId: id);
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
                            Text(
                              'Connection lost — tap to retry',
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
              if (kDebugMode && matchId != null)
                Positioned(
                  top: 6,
                  right: 8,
                  child: Container(
                    padding: const EdgeInsets.symmetric(
                      horizontal: 6,
                      vertical: 3,
                    ),
                    decoration: BoxDecoration(
                      color: Colors.black.withValues(alpha: 0.65),
                      borderRadius: BorderRadius.circular(6),
                    ),
                    child: Text(
                      _streamState.name,
                      style: TextStyle(
                        fontSize: 11,
                        fontWeight: FontWeight.w700,
                        color: Palette.gold,
                      ),
                    ),
                  ),
                ),
            ],
          ),
          const SizedBox(height: 8),

          // Health bars + timer from game state
          if (gameState != null)
            _HealthOverlay(
              gameState: gameState,
              fighter1Name: match.fighter1?.name ?? 'Fighter 1',
              fighter2Name: match.fighter2?.name ?? 'Fighter 2',
            )
          else
            const SizedBox(height: 40),
          const SizedBox(height: 8),

          // Round score dots
          if (gameState != null)
            gameState.bestOf > 1
                ? _RoundDots(
                    bestOf: gameState.bestOf,
                    roundsWonP1: gameState.roundsWonP1,
                    roundsWonP2: gameState.roundsWonP2,
                    currentRound: gameState.currentRound,
                  )
                : const SizedBox.shrink()
          else
            const SizedBox.shrink(),
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
            )
          else
            Container(
              padding: const EdgeInsets.symmetric(vertical: 12, horizontal: 24),
              decoration: BoxDecoration(
                border: Border.all(color: Palette.muted.withValues(alpha: 0.3)),
                borderRadius: BorderRadius.circular(8),
              ),
              child: Text(
                'Betting Closed',
                style: bodyStyle(size: 16, color: Palette.muted),
              ),
            ),
          const SizedBox(height: 8),
        ],
      ),
    );
  }
}

class _LiveVideoFrame extends StatelessWidget {
  const _LiveVideoFrame({required this.controller, required this.state});
  final VideoPlayerController? controller;
  final LiveStreamClientState state;
  static const _targetAspectRatio = 4 / 3;

  @override
  Widget build(BuildContext context) {
    final ctrl = controller;
    if (ctrl == null || !ctrl.value.isInitialized) {
      final label = switch (state) {
        LiveStreamClientState.streamReady ||
        LiveStreamClientState.playerInitializing => 'Loading stream...',
        LiveStreamClientState.error => 'Stream unavailable (retrying)',
        LiveStreamClientState.ended => 'Match ended',
        _ => 'Connecting stream...',
      };
      return _GameFramePlaceholder(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            const IKLoader(size: 36),
            const SizedBox(height: 12),
            Text(
              label,
              style: const TextStyle(color: Palette.muted, fontSize: 13),
            ),
          ],
        ),
      );
    }

    return AspectRatio(
      aspectRatio: ctrl.value.aspectRatio == 0
          ? _targetAspectRatio
          : ctrl.value.aspectRatio,
      child: ColoredBox(color: Palette.black, child: VideoPlayer(ctrl)),
    );
  }
}

/// Placeholder shown while waiting for the first frame.
class _GameFramePlaceholder extends StatelessWidget {
  const _GameFramePlaceholder({required this.child});
  final Widget child;

  @override
  Widget build(BuildContext context) {
    return AspectRatio(
      aspectRatio: 4 / 3,
      child: Container(
        color: Palette.black,
        child: Center(child: child),
      ),
    );
  }
}

/// Health bars and timer overlay.
class _HealthOverlay extends StatelessWidget {
  const _HealthOverlay({
    required this.gameState,
    required this.fighter1Name,
    required this.fighter2Name,
  });

  final GameState gameState;
  final String fighter1Name;
  final String fighter2Name;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 4),
      child: Row(
        children: [
          // P1 health
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  fighter1Name,
                  style: bodyStyle(size: 11, color: Palette.secondary),
                ),
                const SizedBox(height: 4),
                _HealthBar(
                  pct: gameState.p1HealthPct,
                  color: const Color(0xFF4CAF50),
                  reversed: false,
                ),
              ],
            ),
          ),
          // Timer
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 12),
            child: Text(
              '${gameState.timer}',
              style: displayStyle(size: 24, color: Palette.gold),
            ),
          ),
          // P2 health
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.end,
              children: [
                Text(
                  fighter2Name,
                  style: bodyStyle(size: 11, color: Palette.secondary),
                ),
                const SizedBox(height: 4),
                _HealthBar(
                  pct: gameState.p2HealthPct,
                  color: const Color(0xFFF44336),
                  reversed: true,
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

class _HealthBar extends StatelessWidget {
  const _HealthBar({
    required this.pct,
    required this.color,
    required this.reversed,
  });

  final double pct;
  final Color color;
  final bool reversed;

  @override
  Widget build(BuildContext context) {
    return Container(
      height: 8,
      decoration: BoxDecoration(
        color: Palette.cardBg,
        borderRadius: BorderRadius.circular(4),
      ),
      child: LayoutBuilder(
        builder: (context, constraints) {
          return Stack(
            children: [
              AnimatedPositioned(
                duration: const Duration(milliseconds: 300),
                curve: Curves.easeOut,
                left: reversed ? null : 0,
                right: reversed ? 0 : null,
                top: 0,
                bottom: 0,
                width: constraints.maxWidth * pct.clamp(0.0, 1.0),
                child: Container(
                  decoration: BoxDecoration(
                    color: color,
                    borderRadius: BorderRadius.circular(4),
                  ),
                ),
              ),
            ],
          );
        },
      ),
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
