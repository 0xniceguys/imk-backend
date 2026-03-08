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
import '../providers/global_events_provider.dart';
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

  String? _lastConnectedMatchId;
  bool _navigatedToPostMatch = false;
  Timer? _fastPollTimer;

  // Combined HLS player: video + audio in one stream
  VideoPlayerController? _hlsController;
  String? _hlsMatchId; // guard against double-init for the same match
  bool _hlsInitializing = false;

  // FPS readout driven by VideoPlayerController listener
  int _fps = 0;
  int _lastFpsCheck = 0;
  int _fpsFrameCount = 0;

  void _onPlayerUpdate() {
    // Track FPS from VideoPlayer positions advancing
    final now = DateTime.now().millisecondsSinceEpoch;
    _fpsFrameCount++;
    if (now - _lastFpsCheck >= 1000) {
      final newFps = _fpsFrameCount;
      _fpsFrameCount = 0;
      _lastFpsCheck = now;
      if (mounted) setState(() => _fps = newFps);
    }
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

    // Connect after first frame — at this point providers may already have data.
    WidgetsBinding.instance.addPostFrameCallback((_) {
      _connectToMatch();
      _listenForGlobalEvents();
      // If the WS was pre-connected from battle_detail_screen and the backend
      // already signalled 'ready', kick off HLS immediately without waiting.
      _checkEarlyStreamReady();
    });
  }

  /// Eagerly starts HLS if the pre-connected WebSocket already received a
  /// 'ready' signal before the user navigated to this screen.
  void _checkEarlyStreamReady() {
    final matchId = widget.matchId ?? _findLiveMatchId();
    if (matchId == null) return;
    final currentState = ref.read(streamingStateProvider);
    currentState.whenData((data) {
      final state = data['state'] as String?;
      final hlsUrl = data['hls_url'] as String?;
      if (state == 'ready' && hlsUrl != null) {
        debugPrint('[LiveMatch] Early HLS start — stream was already ready on screen entry');
        _startHls(matchId);
      }
    });
  }

  void _listenForGlobalEvents() {
    // Listen for match status changes from global events
    ref.listen<AsyncValue<Map<String, dynamic>>>(
      matchStatusEventsProvider,
      (previous, next) {
        next.whenData((event) {
          final eventMatchId = event['match_id'] as String?;
          final currentMatchId = widget.matchId ?? _lastConnectedMatchId;

          // Check if this event is for our current match
          if (eventMatchId == currentMatchId) {
            if (event['type'] == 'match_status_changed') {
              final status = event['status'] as String?;

              if (status == 'completed' || status == 'cancelled') {
                debugPrint('[LiveMatch] Global event: match $eventMatchId ended ($status)');
                _stopHls();
                if (eventMatchId != null) {
                  _navigateToPostMatch(eventMatchId);
                }
              }
            }
          }
        });
      },
    );
  }

  void _navigateToPostMatch(String matchId) {
    if (_navigatedToPostMatch || !mounted) return;
    _navigatedToPostMatch = true;
    _fastPollTimer?.cancel();
    ref.read(betProvider.notifier).refresh();
    ref.read(matchProvider.notifier).refresh();
    Future.delayed(const Duration(milliseconds: 400), () {
      if (mounted) widget.onNavigate('/post-match/$matchId');
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

    _lastConnectedMatchId = matchId;
    ref.read(matchStreamServiceProvider).connect(matchId);
    // Don't auto-start HLS here — wait for streaming_state: ready from backend
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
    _fastPollTimer?.cancel();
    _pulseCtrl.dispose();
    _stopHls();
    super.dispose();
  }

  // ── HLS player helpers ──────────────────────────────────────────────────────

  void _startHls(String matchId) {
    if (_hlsMatchId == matchId) return; // already initing/playing this match
    _hlsMatchId = matchId;
    _stopHls();
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
  }

  @override
  Widget build(BuildContext context) {
    final matchState = ref.watch(matchProvider);
    final matches = matchState.matches;

    // Retry connecting when match list loads or updates (handles the race
    // where matchProvider is still empty when initState fires).
    // Also catches the case where REST polling flips the match to completed
    // after we missed the WS match_ended event (e.g. cold-start onto ended match).
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
          debugPrint('[LiveMatch] Poll detected match $id ended — navigating to post-match');
          _navigateToPostMatch(id);
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

    // Listen for match end → stop player then navigate (deduped)
    ref.listen<AsyncValue<void>>(matchEndProvider, (_, next) {
      if (next.hasValue) {
        _stopHls();
        _navigateToPostMatch(matchId ?? '');
      }
    });

    // Listen for streaming state changes → start HLS when ready
    ref.listen<AsyncValue<Map<String, dynamic>>>(streamingStateProvider, (_, next) {
      next.whenData((data) {
        final state = data['state'] as String?;
        final hlsUrl = data['hls_url'] as String?;
        final error = data['error'] as String?;

        debugPrint('[LiveMatch] Streaming state: $state');

        if (state == 'ready' && matchId != null && hlsUrl != null) {
          // Backend confirmed HLS is ready — start player now
          debugPrint('[LiveMatch] HLS ready signal received — starting player');
          _startHls(matchId);
        } else if (state == 'error') {
          debugPrint('[LiveMatch] HLS error: $error');
          // Could show error UI here
        }
      });
    });

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

    final isHlsReady = _hlsController != null &&
        _hlsController!.value.isInitialized;
    final isHlsLoading = _hlsInitializing && !isHlsReady;
    final gameStateAsync = ref.watch(gameStateProvider);
    final viewerAsync = ref.watch(viewerCountProvider);
    final streamingStateAsync = ref.watch(streamingStateProvider);

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

          // Combined HLS video+audio player
          Stack(
            children: [
              AspectRatio(
                aspectRatio: 4 / 3,
                child: Container(
                  color: Palette.black,
                  child: isHlsReady
                      ? VideoPlayer(_hlsController!)
                      : Center(
                          child: Column(
                            mainAxisSize: MainAxisSize.min,
                            children: [
                              if (isHlsLoading) const IKLoader(size: 44)
                              else const Icon(Icons.videocam_off,
                                  color: Palette.muted, size: 36),
                              const SizedBox(height: 12),
                              Text(
                                isHlsLoading
                                    ? streamStatusMessage
                                    : 'Stream unavailable',
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
              if (kDebugMode && matchId != null)
                Positioned(
                  top: 6, right: 8,
                  child: Container(
                    padding: const EdgeInsets.symmetric(
                        horizontal: 6, vertical: 3),
                    decoration: BoxDecoration(
                      color: Colors.black.withValues(alpha: 0.65),
                      borderRadius: BorderRadius.circular(6),
                      border: Border.all(
                        color: _fps >= 20
                            ? Colors.greenAccent
                            : _fps >= 10
                                ? Colors.yellow
                                : Colors.redAccent,
                        width: 1,
                      ),
                    ),
                    child: Text(
                      _streamState.name,
                      style: TextStyle(
                        fontSize: 11,
                        fontWeight: FontWeight.bold,
                        color: _fps >= 20
                            ? Colors.greenAccent
                            : _fps >= 10
                                ? Colors.yellow
                                : Colors.redAccent,
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
