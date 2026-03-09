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
    _silenceNow();
  }

  /// Another screen pushed on top — mute immediately.
  @override
  void didPushNext() {
    _silenceNow();
  }

  /// Screen came back into view — restore audio.
  @override
  void didPopNext() {
    try {
      ref.read(hlsPlayerServiceProvider).requestAudio();
    } catch (_) {}
  }

  @override
  void didPush() {} // no-op

  void _silenceNow() {
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
        if (state != 'ready' && state != 'playing') return;
        if (_activeMatchId == null) return;

        // Always route through HlsPlayerService — never create a local controller.
        // This eliminates the dual-controller bug where two ExoPlayer instances
        // could be active simultaneously with conflicting audio.
        final hlsSvc = ref.read(hlsPlayerServiceProvider);
        final svcHasThisMatch = hlsSvc.activeMatchId == _activeMatchId &&
            (hlsSvc.state == HlsPreloadState.playing ||
             hlsSvc.state == HlsPreloadState.initializing);
        if (svcHasThisMatch) {
          // Already handled by global preloader — just ensure it's unmuted
          hlsSvc.unmute();
          return;
        }
        // Global service not active for this match — trigger it directly
        final url = '$kStreamBaseUrl/stream/$_activeMatchId/stream.m3u8';
        debugPrint('[LiveMatch] Triggering hlsService.preload() via streaming_state=ready');
        hlsSvc.preload(_activeMatchId!, url).then((_) => hlsSvc.unmute());
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

  /// Lets the final KO clip play for 2.5 seconds before stopping HLS and
  /// navigating to the post-match screen. Guard flag prevents double-fire.
  void _handleMatchEnded() {
    if (_matchEndScheduled) return;
    _matchEndScheduled = true;
    final matchId = _activeMatchId ?? widget.matchId;
    Future.delayed(const Duration(milliseconds: 2500), () {
      if (!mounted) return;
      _stopHls();
      if (matchId != null) _navigateToPostMatch(matchId);
    });
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
      hlsSvc.preload(matchId, url).then((_) => hlsSvc.unmute());
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
    _fastPollTimer?.cancel();
    _pulseCtrl.dispose();
    _stopHls(); // stops local _hlsController

    // Kill audio immediately (sync) then fully dispose async.
    // silenceAndReset() clears wantsAudio flag so a background preload restart
    // won't auto-unmute. stop() disposes ExoPlayer fire-and-forget.
    try {
      final hlsSvc = ref.read(hlsPlayerServiceProvider);
      hlsSvc.silenceAndReset(); // synchronous — instant audio cut
      hlsSvc.stop();            // async fire-and-forget — full disposal
    } catch (_) {
      // ref may be invalidated on hot restart — ignore
    }

    routeObserver.unsubscribe(this);
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
    final isAnyHlsLoading = !isGlobalHlsReady && !isHlsError;

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

  // ── Smooth timer interpolation ──────────────────────────────────────────
  // The WS sends timer updates at ~5-10Hz which causes visible jumps.
  // We keep a local _displayTimer that ticks down every second, and
  // reconcile with backend values when they arrive.
  int _displayTimer = 99;
  Timer? _countdownTimer;

  @override
  void initState() {
    super.initState();
    _displayTimer = widget.gameState.timer;
    _startCountdown();
  }

  void _startCountdown() {
    _countdownTimer?.cancel();
    _countdownTimer = Timer.periodic(const Duration(seconds: 1), (_) {
      if (!mounted) {
        _countdownTimer?.cancel();
        return;
      }
      setState(() {
        if (_displayTimer > 0) _displayTimer--;
      });
    });
  }

  @override
  void didUpdateWidget(_HealthOverlay oldWidget) {
    super.didUpdateWidget(oldWidget);

    // Health: only allow decreases (monotonic clamp)
    final newP1 = widget.gameState.p1HealthPct.clamp(0.0, 1.0);
    final newP2 = widget.gameState.p2HealthPct.clamp(0.0, 1.0);
    if (newP1 < _p1Pct) _p1Pct = newP1;
    if (newP2 < _p2Pct) _p2Pct = newP2;

    // Timer reconciliation:
    // - If backend value is lower → snap down (backend is authoritative)
    // - If backend value is much higher (>20 diff) → round reset, snap up + restart ticker
    final backendTimer = widget.gameState.timer;
    if (backendTimer < _displayTimer) {
      // Backend is behind our local tick — snap to backend (authoritative)
      setState(() => _displayTimer = backendTimer);
    } else if (backendTimer > _displayTimer + 20) {
      // New round started — health also resets
      _p1Pct = 1.0;
      _p2Pct = 1.0;
      setState(() => _displayTimer = backendTimer);
      _startCountdown(); // restart 1s ticker from new value
    }
  }

  @override
  void dispose() {
    _countdownTimer?.cancel();
    super.dispose();
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
          // Timer — shows local interpolated value, not raw WS value
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 12),
            child: Text(
              '$_displayTimer',
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
