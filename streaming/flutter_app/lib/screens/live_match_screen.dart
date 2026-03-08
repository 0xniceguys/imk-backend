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
import '../services/hls_player_service.dart';
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
  ProviderSubscription<AsyncValue<Map<String, dynamic>>>? _streamingStateSub;
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
    _streamState = LiveStreamClientState.idle;
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
      _handleMatchEnded();
    });
    _streamingStateSub = ref.listenManual<AsyncValue<Map<String, dynamic>>>(
      streamingStateProvider,
      (_, next) {
        final data = next.valueOrNull;
        if (data == null) return;
        final state = data['state'] as String?;
        if (state != 'ready' && state != 'playing') return;
        if (_activeMatchId == null) return;

        // Defer to HlsPlayerService if it already has a controller for this match
        // (globalHlsPreloaderProvider may have already handled this event).
        // Only fall back to the local _startHls() if the service isn't active.
        final hlsSvc = ref.read(hlsPlayerServiceProvider);
        final svcHasThisMatch = hlsSvc.activeMatchId == _activeMatchId &&
            (hlsSvc.state == HlsPreloadState.playing ||
             hlsSvc.state == HlsPreloadState.initializing);
        if (svcHasThisMatch) {
          debugPrint('[LiveMatch] HlsPlayerService already handling $_activeMatchId — skipping local _startHls');
          return;
        }
        _startHls(_activeMatchId!);
      },
    );
    _gameStateSub = ref.listenManual<AsyncValue<GameState>>(gameStateProvider, (
      _,
      next,
    ) {
      // Force rebuild when new game state arrives so HUD updates
      if (next.hasValue && mounted) setState(() {});
    });
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

  /// Stops any live HLS player and navigates to post-match screen.
  void _handleMatchEnded() {
    _stopHls();
    final matchId = _activeMatchId ?? widget.matchId;
    if (matchId != null) _navigateToPostMatch(matchId);
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
    final matchId = widget.matchId ?? _findLiveMatchId();
    if (matchId == null) return;
    if (_hlsMatchId == matchId) return; // already running for this match
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

    final isHlsReady = _hlsController != null &&
        _hlsController!.value.isInitialized;
    final isHlsLoading = _hlsInitializing && !isHlsReady;
    final gameStateAsync = ref.watch(gameStateProvider);
    final gameState = gameStateAsync.valueOrNull;
    final viewerAsync = ref.watch(viewerCountProvider);
    final streamingStateAsync = ref.watch(streamingStateProvider);

    // Reactive: rebuilds automatically when pre-loaded controller appears or changes
    final hlsCtrlAsync = ref.watch(hlsControllerProvider);
    final hlsStateAsync = ref.watch(hlsPreloadStateProvider);
    final preloadedCtrl = hlsCtrlAsync.valueOrNull;
    // Prefer pre-loaded global controller; fall back to screen-local one
    final hlsCtrl = (preloadedCtrl != null && preloadedCtrl.value.isInitialized)
        ? preloadedCtrl
        : (_hlsController != null && _hlsController!.value.isInitialized ? _hlsController : null);
    final isGlobalHlsReady = hlsCtrl != null;
    final isAnyHlsLoading = _hlsInitializing ||
        hlsStateAsync.valueOrNull == HlsPreloadState.initializing;

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
                  child: isGlobalHlsReady
                      ? VideoPlayer(hlsCtrl!)
                      : Center(
                          child: Column(
                            mainAxisSize: MainAxisSize.min,
                            children: [
                              if (isAnyHlsLoading) const IKLoader(size: 44)
                              else const Icon(Icons.videocam_off,
                                  color: Palette.muted, size: 36),
                              const SizedBox(height: 12),
                              Text(
                                isAnyHlsLoading
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
            ),
          const SizedBox(height: 8),
        ],
      ),
    );
  }
}


/// Health bars and timer overlay.
/// StatefulWidget so it can clamp health monotonically downwards —
/// if a new value is higher than the last seen, the old value is kept.
/// This prevents visual glitches from occasional bad reads.
class _HealthOverlay extends StatefulWidget {
  const _HealthOverlay({
    required this.gameState,
    required this.fighter1Name,
    required this.fighter2Name,
  });

  final GameState gameState;
  final String fighter1Name;
  final String fighter2Name;

  @override
  State<_HealthOverlay> createState() => _HealthOverlayState();
}

class _HealthOverlayState extends State<_HealthOverlay> {
  double _p1Pct = 1.0;
  double _p2Pct = 1.0;

  @override
  void didUpdateWidget(_HealthOverlay oldWidget) {
    super.didUpdateWidget(oldWidget);
    final newP1 = widget.gameState.p1HealthPct.clamp(0.0, 1.0);
    final newP2 = widget.gameState.p2HealthPct.clamp(0.0, 1.0);
    // Only update if health went down — ignore upward glitches
    if (newP1 < _p1Pct) _p1Pct = newP1;
    if (newP2 < _p2Pct) _p2Pct = newP2;
  }

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
                  widget.fighter1Name,
                  style: bodyStyle(size: 11, color: Palette.secondary),
                ),
                const SizedBox(height: 4),
                _HealthBar(
                  pct: _p1Pct,
                  color: const Color(0xFFBB1111), // dark crimson
                  reversed: false,
                ),
              ],
            ),
          ),
          // Timer
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 12),
            child: Text(
              '${widget.gameState.timer}',
              style: displayStyle(size: 24, color: Palette.gold),
            ),
          ),
          // P2 health
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.end,
              children: [
                Text(
                  widget.fighter2Name,
                  style: bodyStyle(size: 11, color: Palette.secondary),
                ),
                const SizedBox(height: 4),
                _HealthBar(
                  pct: _p2Pct,
                  color: const Color(0xFF7A0000), // dark blood red
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
      // No borderRadius — sharp edges match design style
      color: const Color(0xFF1A1410),
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
                child: ColoredBox(color: color),
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
