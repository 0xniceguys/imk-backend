import 'dart:async';
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
import '../widgets/shared/app_shell.dart';
import '../widgets/shared/ornate_button.dart';
import '../widgets/shared/ik_loader.dart';
import '../widgets/betting/bet_bottom_sheet.dart';

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

  String? _lastConnectedMatchId;
  // Prevents double-navigation when WS event + REST poll both fire at once
  bool _navigatedToPostMatch = false;
  Timer? _fastPollTimer;

  // Audio player — plays the HLS audio-only stream for the current match
  VideoPlayerController? _audioController;
  String? _audioMatchId; // guard against double-init for the same match

  // FPS counter — track timestamps of the last N frames in a 1s window
  final List<int> _frameTimes = []; // milliseconds since epoch
  int _fps = 0;

  void _recordFrame() {
    final now = DateTime.now().millisecondsSinceEpoch;
    _frameTimes.add(now);
    // Remove timestamps older than 1 second
    _frameTimes.removeWhere((t) => now - t > 1000);
    final newFps = _frameTimes.length;
    if (newFps != _fps) {
      setState(() => _fps = newFps);
    }
  }

  @override
  void initState() {
    super.initState();
    _pulseCtrl = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 1200),
    )..repeat(reverse: true);

    // Speed up match status polling to 3s while on this screen
    // so a missed WS match_ended event is caught quickly.
    _fastPollTimer = Timer.periodic(const Duration(seconds: 3), (_) {
      if (mounted) ref.read(matchProvider.notifier).refresh();
    });

    // Connect after first frame — at this point providers may already have data.
    WidgetsBinding.instance.addPostFrameCallback((_) {
      _connectToMatch();
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

  void _connectToMatch() {
    final matchId = widget.matchId ?? _findLiveMatchId();
    if (matchId == null) return;
    // Don't reconnect if already on the right match
    if (matchId == _lastConnectedMatchId) return;

    // If the match is already completed/cancelled, skip WS and go to post-match
    final match = ref.read(matchProvider).matches.cast<Match?>().firstWhere(
      (m) => m?.id == matchId,
      orElse: () => null,
    );
    if (match != null &&
        (match.status == MatchStatus.completed ||
            match.status == MatchStatus.cancelled)) {
      debugPrint('[LiveMatch] Match $matchId already ended — going to post-match');
      Future.microtask(() => _navigateToPostMatch(matchId));
      return;
    }

    _lastConnectedMatchId = matchId;
    ref.read(matchStreamServiceProvider).connect(matchId);
    _startAudio(matchId);
  }

  /// Returns the ID of the first truly LIVE match, or null.
  /// Never falls back to non-live matches to avoid connecting a WS
  /// that the backend will immediately close with 4004.
  String? _findLiveMatchId() {
    final matches = ref.read(matchProvider).matches;
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
    _stopAudio();
    super.dispose();
  }

  // ── Audio helpers ──
  void _startAudio(String matchId) {
    // Guard: don't reinitialise the same match (matchProvider listener can fire
    // multiple times on the same matchId).
    if (_audioMatchId == matchId) return;
    _audioMatchId = matchId;
    _stopAudio(); // dispose any previous controller for a different match
    _initAudio(matchId, attempt: 1);
  }

  Future<void> _initAudio(String matchId, {required int attempt}) async {
    if (!mounted || _audioMatchId != matchId) return;

    // Wait for FFmpeg to write the first HLS segment (~1s after capture starts)
    // The first request returning 404 would cause ExoPlayer to error-loop.
    if (attempt == 1) await Future.delayed(const Duration(seconds: 2));
    if (!mounted || _audioMatchId != matchId) return;

    final url = '$kStreamBaseUrl/stream/audio/$matchId/stream.m3u8';
    final controller = VideoPlayerController.networkUrl(
      Uri.parse(url),
      videoPlayerOptions: VideoPlayerOptions(mixWithOthers: true),
    );
    _audioController = controller;
    try {
      await controller.initialize();
      if (!mounted || _audioMatchId != matchId) {
        controller.dispose();
        return;
      }
      controller.setVolume(1.0);
      controller.play();
      debugPrint('[Audio] Playing HLS stream (attempt $attempt): $url');
    } catch (e) {
      debugPrint('[Audio] Init failed (attempt $attempt): $e');
      controller.dispose();
      if (_audioMatchId != matchId) return; // navigated away
      // Retry up to 3 times with growing delay
      if (attempt < 3) {
        await Future.delayed(Duration(seconds: attempt * 2));
        _initAudio(matchId, attempt: attempt + 1);
      } else {
        debugPrint('[Audio] Giving up after $attempt attempts for match $matchId');
      }
    }
  }

  void _stopAudio() {
    _audioMatchId = null;
    final ctrl = _audioController;
    _audioController = null;
    ctrl?.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final matchState = ref.watch(matchProvider);
    final matches = matchState.matches;

    // FPS counter — fires every time a new binary frame arrives from WebSocket
    ref.listen<AsyncValue<Uint8List>>(frameProvider, (prev, next) {
      if (next.hasValue) _recordFrame();
    });

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
        ? matches.cast<Match?>().firstWhere((m) => m?.id == matchId,
            orElse: () => null)
        : null;

    // Listen for match end → navigate to post-match (deduped)
    ref.listen<AsyncValue<void>>(matchEndProvider, (_, next) {
      if (next.hasValue) {
        _navigateToPostMatch(matchId ?? '');
      }
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
                    Text('Match not found',
                        style: bodyStyle(color: Palette.muted)),
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

    final frameAsync = ref.watch(frameProvider);
    final gameStateAsync = ref.watch(gameStateProvider);
    final viewerAsync = ref.watch(viewerCountProvider);

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
                      opacity: Tween<double>(begin: 0.3, end: 1.0)
                          .animate(CurvedAnimation(
                        parent: _pulseCtrl,
                        curve: Curves.easeInOut,
                      )),
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
                    Text('LIVE',
                        style: bodyStyle(size: 14, color: Palette.red)),
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
                Text(match.label,
                    style: bodyStyle(size: 14, color: Palette.muted)),
              ],
            ),
          ),
          const SizedBox(height: 8),

          // Game frame from WebSocket + FPS badge overlay (debug only)
          Stack(
            children: [
              frameAsync.when(
                data: (frameBytes) => _GameFrame(frameBytes: frameBytes),
                loading: () => const _GameFramePlaceholder(
                  child: IKLoader(size: 44),
                ),
                error: (e, s) => const _GameFramePlaceholder(
                  child: Column(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      IKLoader(size: 36),
                      SizedBox(height: 12),
                      Text('Connecting...',
                          style: TextStyle(color: Palette.muted, fontSize: 13)),
                    ],
                  ),
                ),
              ),
              // Connection-lost banner — shows when WS has given up reconnecting
              Builder(builder: (ctx) {
                final svc = ref.watch(matchStreamServiceProvider);
                if (!svc.hasGivenUp) return const SizedBox.shrink();
                return Positioned(
                  bottom: 0,
                  left: 0,
                  right: 0,
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
                                  fontWeight: FontWeight.w600)),
                        ],
                      ),
                    ),
                  ),
                );
              }),
              if (kDebugMode)
                Positioned(
                  top: 6,
                  right: 8,
                  child: Container(
                    padding:
                        const EdgeInsets.symmetric(horizontal: 6, vertical: 3),
                    decoration: BoxDecoration(
                      color: Colors.black.withValues(alpha: 0.65),
                      borderRadius: BorderRadius.circular(6),
                      border: Border.all(
                        color: _fps >= 50
                            ? Colors.greenAccent
                            : _fps >= 30
                                ? Colors.yellow
                                : Colors.redAccent,
                        width: 1,
                      ),
                    ),
                    child: Text(
                      '$_fps fps',
                      style: TextStyle(
                        fontSize: 11,
                        fontWeight: FontWeight.bold,
                        color: _fps >= 50
                            ? Colors.greenAccent
                            : _fps >= 30
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
          gameStateAsync.when(
            data: (gs) => _HealthOverlay(
              gameState: gs,
              fighter1Name: match.fighter1?.name ?? 'Fighter 1',
              fighter2Name: match.fighter2?.name ?? 'Fighter 2',
            ),
            loading: () => const SizedBox(height: 40),
            error: (e, s) => const SizedBox(height: 40),
          ),
          const SizedBox(height: 8),

          // Round score dots
          gameStateAsync.when(
            data: (gs) => gs.bestOf > 1
                ? _RoundDots(
                    bestOf: gs.bestOf,
                    roundsWonP1: gs.roundsWonP1,
                    roundsWonP2: gs.roundsWonP2,
                    currentRound: gs.currentRound,
                  )
                : const SizedBox.shrink(),
            loading: () => const SizedBox.shrink(),
            error: (e, s) => const SizedBox.shrink(),
          ),
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

/// Displays the latest PNG frame from the emulator.
class _GameFrame extends StatelessWidget {
  const _GameFrame({required this.frameBytes});
  final Uint8List frameBytes;

  @override
  Widget build(BuildContext context) {
    return AspectRatio(
      aspectRatio: 4 / 3,
      child: Container(
        color: Palette.black,
        child: Image.memory(
          frameBytes,
          fit: BoxFit.contain,
          gaplessPlayback: true,
        ),
      ),
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
                Text(fighter1Name,
                    style: bodyStyle(size: 11, color: Palette.secondary)),
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
                Text(fighter2Name,
                    style: bodyStyle(size: 11, color: Palette.secondary)),
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
      opacity: Tween<double>(begin: 0.3, end: 1.0).animate(
        CurvedAnimation(parent: _ctrl, curve: Curves.easeInOut),
      ),
      child: Container(
        width: widget.size,
        height: widget.size,
        decoration: BoxDecoration(
          shape: BoxShape.circle,
          color: widget.color,
        ),
      ),
    );
  }
}
