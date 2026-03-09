import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../core/constants.dart';
import '../core/palette.dart';
import '../core/runtime_client_config.dart';
import '../core/typography.dart';
import '../models/match.dart';
import '../models/match_bet_feed_item.dart';
import '../providers/clock_provider.dart';
import '../providers/match_provider.dart';
import '../providers/match_stream_provider.dart';
import '../providers/global_events_provider.dart';
import '../router.dart';
import '../widgets/betting/bet_bottom_sheet.dart';
import '../widgets/fighter/fighter_image.dart';
import '../widgets/shared/app_shell.dart';
import '../widgets/shared/gold_gradient_divider.dart';
import '../widgets/shared/ik_loader.dart';
import '../widgets/shared/pressable.dart';

class BattleDetailScreen extends ConsumerStatefulWidget {
  const BattleDetailScreen({super.key, required this.onNavigate, this.matchId});

  final void Function(String) onNavigate;
  final String? matchId;

  @override
  ConsumerState<BattleDetailScreen> createState() => _BattleDetailScreenState();
}

class _BattleDetailScreenState extends ConsumerState<BattleDetailScreen> {
  ProviderSubscription<MatchState>? _matchStateSub;
  int? _selectedFighter;
  Future<List<MatchBetFeedItem>>? _betFeedFuture;
  String? _betFeedMatchId;
  bool _navigatedToLive = false;

  // Pre-connect WebSocket when match is #1 in queue so HLS starts
  // loading in the background before the user navigates to the live screen.
  String? _preConnectedMatchId;

  // Rapid-poll timer activated at T≤2s so we don't depend on clockTickProvider
  // to trigger _maybeRefreshAroundGoLive after the countdown hits zero.
  Timer? _rapidPollTimer;

  @override
  void initState() {
    super.initState();
    // Wire match-state changes (REST poll) → go-live redirect
    _matchStateSub = ref.listenManual<MatchState>(
      matchProvider,
      (prev, next) => _onMatchStateChanged(prev, next),
      fireImmediately: false,
    );
    // Wire global WS events → instant go-live redirect (no poll delay)
    ref.listenManual<AsyncValue<Map<String, dynamic>>>(
      matchStatusEventsProvider,
      (_, next) {
        next.whenData((event) {
          if (_navigatedToLive || !mounted) return;
          if (event['type'] != 'match_status_changed') return;
          if (event['status'] != 'live') return;
          final eventMatchId = event['match_id'] as String?;
          final ourMatchId = widget.matchId ?? _resolveMatch(
            ref.read(matchProvider).matches,
          )?.id;
          if (eventMatchId == null || eventMatchId != ourMatchId) return;
          _navigatedToLive = true;
          debugPrint('[BattleDetail] Global event: match $eventMatchId went live — instant redirect');
          widget.onNavigate('/live-match/$eventMatchId');
        });
      },
    );
  }

  @override
  void dispose() {
    _matchStateSub?.close();
    _rapidPollTimer?.cancel();
    super.dispose();
  }

  void _startRapidPoll() {
    if (_rapidPollTimer?.isActive ?? false) return;
    debugPrint('[BattleDetail] ⏱️ Starting rapid poll (500ms) for go-live detection');
    _rapidPollTimer = Timer.periodic(const Duration(milliseconds: 500), (_) {
      if (!mounted) { _rapidPollTimer?.cancel(); return; }
      ref.read(matchProvider.notifier).refresh();
    });
  }

  void _stopRapidPoll() {
    if (_rapidPollTimer?.isActive ?? false) {
      debugPrint('[BattleDetail] ⏹️ Stopping rapid poll');
    }
    _rapidPollTimer?.cancel();
    _rapidPollTimer = null;
  }
  void _maybePreConnect(Match match) {
    if (match.status != MatchStatus.upcoming) return;
    if (match.queuePosition != 1) return;
    if (_preConnectedMatchId == match.id) return;
    _preConnectedMatchId = match.id;
    debugPrint('[BattleDetail] 🔌 Pre-connecting WS for match ${match.id} (queue #1, scheduled at ${match.queueStartsAt})');
    ref.read(matchStreamServiceProvider).connect(match.id);
  }

  void _ensureBetFeedFuture(String matchId) {
    if (_betFeedFuture == null || _betFeedMatchId != matchId) {
      _betFeedMatchId = matchId;
      _betFeedFuture = ref
          .read(apiServiceProvider)
          .fetchMatchBetFeed(matchId, limit: 20);
    }
  }

  void _refreshBetFeed(String matchId) {
    setState(() {
      _betFeedMatchId = matchId;
      _betFeedFuture = ref
          .read(apiServiceProvider)
          .fetchMatchBetFeed(matchId, limit: 20);
    });
  }

  void _onMatchStateChanged(MatchState? prev, MatchState next) {
    if (!mounted) return;
    final match = _resolveMatch(next.matches);
    if (match == null || match.status != MatchStatus.live) return;

    debugPrint('[BattleDetail] 🟢 Match ${match.id} went LIVE via REST poll — ensuring WS connected');

    // Match went live — stop the rapid poll timer immediately.
    _stopRapidPoll();

    final streamSvc = ref.read(matchStreamServiceProvider);
    if (streamSvc.matchId != match.id) {
      debugPrint('[BattleDetail] 🔌 Connecting WS to newly-live match ${match.id}');
      streamSvc.connect(match.id);
    } else if (!streamSvc.isConnected && !streamSvc.isConnecting) {
      debugPrint('[BattleDetail] 🔄 WS for ${match.id} exists but disconnected — reconnecting');
      streamSvc.connect(match.id);
    } else {
      debugPrint('[BattleDetail] ✅ WS already connected to ${match.id} (isConnected=${streamSvc.isConnected})');
    }

    if (_navigatedToLive) return;
    final isCurrentRoute = ModalRoute.of(context)?.isCurrent ?? true;
    if (!isCurrentRoute) {
      debugPrint('[BattleDetail] ⚠️ Not current route — skipping navigation to live (isCurrentRoute=$isCurrentRoute)');
      return;
    }

    debugPrint('[BattleDetail] 🚦 Navigating to /live-match/${match.id} (triggered by REST poll)');
    _navigatedToLive = true;
    Future.microtask(() => widget.onNavigate('/live-match/${match.id}'));
  }

  @override
  Widget build(BuildContext context) {
    final matchState = ref.watch(matchProvider);
    final matches = matchState.matches;
    final tokenSymbol = RuntimeClientConfig.instance.tokenSymbol;

    // Subscribe to the 1 Hz clock tick so _queueLabel()'s countdown
    // updates every second for queue-#1 upcoming matches, not just on the
    // REST poll (~2 s). Only watch when actually needed to avoid wasted rebuilds.
    final needsClock = matches.isNotEmpty &&
        matches.first.status == MatchStatus.upcoming &&
        matches.first.queuePosition == 1 &&
        matches.first.queueStartsAt != null;
    if (needsClock) ref.watch(clockTickProvider);

    if (!matchState.hasLoaded) {
      return const Scaffold(
        backgroundColor: Palette.black,
        body: Center(child: IKLoader(size: 40)),
      );
    }

    if (matches.isEmpty) {
      return const Scaffold(
        backgroundColor: Palette.black,
        body: Center(
          child: Text(
            'No matches available',
            style: TextStyle(color: Palette.muted, fontSize: 16),
          ),
        ),
      );
    }

    final match = _resolveMatch(matches);
    if (match == null) {
      return const Scaffold(
        backgroundColor: Palette.black,
        body: Center(
          child: Text(
            'No matches available',
            style: TextStyle(color: Palette.muted, fontSize: 16),
          ),
        ),
      );
    }

    _maybeRefreshAroundGoLive(match);

    _ensureBetFeedFuture(match.id);
    _maybePreConnect(match);

    final totalPool = match.totalPool;
    final sideAPool = match.odds.fighter1Pool > 0
        ? match.odds.fighter1Pool
        : totalPool * match.odds.fighter1PoolPct;
    final sideBPool = match.odds.fighter2Pool > 0
        ? match.odds.fighter2Pool
        : totalPool * match.odds.fighter2PoolPct;
    final sideAPct = totalPool > 0 ? sideAPool / totalPool : 0.0;
    final sideBPct = totalPool > 0 ? sideBPool / totalPool : 0.0;

    return AppShell(
      activeTab: NavTab.arena,
      scrollable: true,
      contentBottomPadding: 140,
      onNavigate: (slug) => widget.onNavigate(routeFor(slug)),
      content: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          const SizedBox(height: 6),
          Center(
            child: Text(
              match.label,
              style: bodyStyle(size: 14, color: Palette.secondary),
            ),
          ),
          const SizedBox(height: 6),
          Center(
            child: Text(
              _queueLabel(match),
              style: displayStyle(
                size: 15,
                color: match.status == MatchStatus.live
                    ? Palette.green
                    : Palette.gold,
                letterSpacing: 0.8,
              ),
            ),
          ),
          const SizedBox(height: 10),
          SizedBox(
            height: 220,
            child: Row(
              crossAxisAlignment: CrossAxisAlignment.end,
              children: [
                Expanded(
                  child: _PortraitTap(
                    onTap: () => widget.onNavigate(
                      '/fighter-details/${match.fighter1?.id}',
                    ),
                    child: match.fighter1 != null
                        ? FighterImage(
                            fighter: match.fighter1!,
                            fit: BoxFit.contain,
                          )
                        : Image.asset(Assets.battleLeft, fit: BoxFit.contain),
                  ),
                ),
                Padding(
                  padding: const EdgeInsets.only(bottom: 24),
                  child: Text(
                    'VS',
                    style: displayStyle(size: 30, color: Palette.gold),
                  ),
                ),
                Expanded(
                  child: _PortraitTap(
                    onTap: () => widget.onNavigate(
                      '/fighter-details/${match.fighter2?.id}',
                    ),
                    child: match.fighter2 != null
                        ? FighterImage(
                            fighter: match.fighter2!,
                            fit: BoxFit.contain,
                          )
                        : Image.asset(Assets.battleRight, fit: BoxFit.contain),
                  ),
                ),
              ],
            ),
          ),
          Row(
            children: [
              Expanded(
                child: _FighterLabel(
                  name: match.fighter1?.name ?? '?',
                  model: match.fighter1?.llmModel ?? '',
                  onTap: () => widget.onNavigate(
                    '/fighter-details/${match.fighter1?.id}',
                  ),
                ),
              ),
              Expanded(
                child: _FighterLabel(
                  name: match.fighter2?.name ?? '?',
                  model: match.fighter2?.llmModel ?? '',
                  onTap: () => widget.onNavigate(
                    '/fighter-details/${match.fighter2?.id}',
                  ),
                ),
              ),
            ],
          ),

          const GoldGradientDivider(
            margin: EdgeInsets.fromLTRB(18, 24, 18, 24),
          ),
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 16),
            child: Row(
              children: [
                Expanded(
                  child: _BetActionCard(
                    selected: _selectedFighter == 0,
                    name: match.fighter1?.name ?? 'Fighter 1',
                    onTap: () => _pickSideAndOpenBet(0, match),
                  ),
                ),
                const SizedBox(width: 10),
                Expanded(
                  child: _BetActionCard(
                    selected: _selectedFighter == 1,
                    name: match.fighter2?.name ?? 'Fighter 2',
                    onTap: () => _pickSideAndOpenBet(1, match),
                  ),
                ),
              ],
            ),
          ),
          if (!match.bettingOpen) ...[
            const SizedBox(height: 10),
            Center(
              child: Text(
                'Betting closed',
                style: bodyStyle(size: 13, color: Palette.muted),
              ),
            ),
          ],
          const SizedBox(height: 14),
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 16),
            child: _MarketPanel(
              totalPool: totalPool,
              sideAPool: sideAPool,
              sideBPool: sideBPool,
              sideAPct: sideAPct,
              sideBPct: sideBPct,
              sideAName: match.fighter1?.name ?? 'Fighter 1',
              sideBName: match.fighter2?.name ?? 'Fighter 2',
              activeBets: match.activeBets,
              tokenSymbol: tokenSymbol,
            ),
          ),
          const SizedBox(height: 12),
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 16),
            child: _RecentBetsPanel(
              tokenSymbol: tokenSymbol,
              future: _betFeedFuture,
            ),
          ),
          const SizedBox(height: 24),
          const GoldGradientDivider(margin: EdgeInsets.fromLTRB(18, 14, 18, 0)),
          const SizedBox(height: 60),
        ],
      ),
    );
  }

  Match? _resolveMatch(List<Match> matches) {
    if (matches.isEmpty) return null;
    // If a specific matchId was requested but not found, return null rather
    // than silently showing a different match.
    if (widget.matchId != null) {
      return matches.cast<Match?>().firstWhere(
        (m) => m?.id == widget.matchId,
        orElse: () => null,
      );
    }
    return matches.first;
  }

  void _maybeRefreshAroundGoLive(Match match) {
    if (match.status != MatchStatus.upcoming || match.queuePosition != 1) {
      _stopRapidPoll();
      return;
    }
    final startsAt = match.queueStartsAt;
    if (startsAt == null) { _stopRapidPoll(); return; }
    final remain = startsAt.difference(DateTime.now()).inSeconds;

    if (remain > 2) return; // not yet — don't start timer

    // T≤2: activate the 500ms rapid-poll Timer (idempotent start).
    debugPrint('[BattleDetail] ⏳ ${remain}s to go-live — activating rapid poll (match=${match.id})');
    _startRapidPoll();
  }

  Future<void> _pickSideAndOpenBet(int side, Match match) async {
    setState(() => _selectedFighter = side);
    if (!match.bettingOpen) return;
    await _openBetSheet(match);
  }

  Future<void> _openBetSheet(Match match) async {
    debugPrint('[BattleDetail] 💸 Opening bet sheet for match ${match.id} (bettingOpen=${match.bettingOpen})');
    await showModalBottomSheet<void>(
      context: context,
      isScrollControlled: true,
      backgroundColor: Colors.transparent,
      builder: (_) => BetBottomSheet(
        match: match,
        initialSelectedFighter: _selectedFighter ?? 0,
      ),
    );
    if (!mounted) return;
    debugPrint('[BattleDetail] 💸 Bet sheet closed — refreshing match state');
    await ref.read(matchProvider.notifier).refresh();
    _refreshBetFeed(match.id);
  }

  String _queueLabel(Match match) {
    if (match.status == MatchStatus.live) {
      return 'LIVE';
    }
    if (match.status == MatchStatus.upcoming) {
      final q = match.queuePosition;
      if (q == 1) {
        final startsAt = match.queueStartsAt;
        if (startsAt == null) return 'NEXT MATCH';
        final remain = startsAt.difference(DateTime.now()).inSeconds;
        if (remain <= 0) return 'NEXT MATCH';
        final mm = (remain ~/ 60).toString().padLeft(2, '0');
        final ss = (remain % 60).toString().padLeft(2, '0');
        return 'NEXT MATCH  $mm:$ss';
      }
      if (q != null && q >= 2) return '#$q IN-QUEUE';
      return 'IN-QUEUE';
    }
    if (match.status == MatchStatus.completed) return 'COMPLETED';
    return 'CANCELLED';
  }
}

class _PortraitTap extends StatelessWidget {
  const _PortraitTap({required this.onTap, required this.child});

  final VoidCallback onTap;
  final Widget child;

  @override
  Widget build(BuildContext context) {
    return Pressable(
      onTap: onTap,
      scaleTo: 0.97,
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 2),
        child: child,
      ),
    );
  }
}

class _FighterLabel extends StatelessWidget {
  const _FighterLabel({
    required this.name,
    required this.model,
    required this.onTap,
  });

  final String name;
  final String model;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return Pressable(
      onTap: onTap,
      scaleTo: 0.98,
      child: Column(
        children: [
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 10),
            child: Text(
              name,
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              textAlign: TextAlign.center,
              style: displayStyle(size: 21, color: Palette.gold),
            ),
          ),
          const SizedBox(height: 1),
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 10),
            child: Text(
              model,
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              textAlign: TextAlign.center,
              style: bodyStyle(size: 12, color: Palette.secondary),
            ),
          ),
        ],
      ),
    );
  }
}

class _BetActionCard extends StatelessWidget {
  const _BetActionCard({
    required this.selected,
    required this.name,
    required this.onTap,
  });

  final bool selected;
  final String name;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return Pressable(
      onTap: onTap,
      scaleTo: 0.97,
      haptic: true,
      child: AnimatedContainer(
        duration: const Duration(milliseconds: 180),
        curve: Curves.easeOut,
        padding: const EdgeInsets.fromLTRB(10, 12, 10, 10),
        decoration: BoxDecoration(
          border: Border.all(
            color: selected ? Palette.gold : Palette.border,
            width: selected ? 1.4 : 1,
          ),
          color: selected
              ? Palette.darkGold.withValues(alpha: 0.55)
              : Colors.transparent,
          borderRadius: BorderRadius.circular(4),
        ),
        child: Text(
          'BET ON ${name.toUpperCase()}',
          maxLines: 2,
          overflow: TextOverflow.ellipsis,
          textAlign: TextAlign.center,
          style: bodyStyle(
            size: 13,
            color: selected ? Palette.gold : Palette.white,
            weight: FontWeight.w700,
          ),
        ),
      ),
    );
  }
}

class _MarketPanel extends StatelessWidget {
  const _MarketPanel({
    required this.totalPool,
    required this.sideAPool,
    required this.sideBPool,
    required this.sideAPct,
    required this.sideBPct,
    required this.sideAName,
    required this.sideBName,
    required this.activeBets,
    required this.tokenSymbol,
  });

  final double totalPool;
  final double sideAPool;
  final double sideBPool;
  final double sideAPct;
  final double sideBPct;
  final String sideAName;
  final String sideBName;
  final int activeBets;
  final String tokenSymbol;

  String _fmt(double value) {
    final s = value.toStringAsFixed(2);
    return s.replaceFirst(RegExp(r'\.?0+$'), '');
  }

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.fromLTRB(12, 12, 12, 10),
      decoration: BoxDecoration(
        // border: Border.all(color: Palette.border),
        borderRadius: BorderRadius.circular(6),
        // color: Palette.sheetBg.withValues(alpha: 0.3),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const GoldGradientDivider(
            margin: EdgeInsets.fromLTRB(18, 12, 18, 12),
          ),
          Center(
            child: Text(
              'BET POOLS',
              style: bodyStyle(
                size: 13,
                color: Palette.gold,
                weight: FontWeight.w700,
                letterSpacing: 0.9,
              ),
            ),
          ),
          const SizedBox(height: 8),
          _MarketRow(
            label: 'Total SKR in Pool',
            value: '${_fmt(totalPool)} $tokenSymbol',
            valueColor: Palette.white,
          ),
          const SizedBox(height: 4),
          _MarketRow(
            label: 'Active Bets',
            value: '$activeBets',
            valueColor: Palette.secondary,
          ),
          const SizedBox(height: 10),
          Row(
            children: [
              Expanded(
                child: _PoolCard(
                  side: 'A',
                  fighterName: sideAName,
                  amount: sideAPool,
                  pct: sideAPct,
                  tokenSymbol: tokenSymbol,
                ),
              ),
              const SizedBox(width: 8),
              Expanded(
                child: _PoolCard(
                  side: 'B',
                  fighterName: sideBName,
                  amount: sideBPool,
                  pct: sideBPct,
                  tokenSymbol: tokenSymbol,
                ),
              ),
            ],
          ),
        ],
      ),
    );
  }
}

class _PoolCard extends StatelessWidget {
  const _PoolCard({
    required this.side,
    required this.fighterName,
    required this.amount,
    required this.pct,
    required this.tokenSymbol,
  });

  final String side;
  final String fighterName;
  final double amount;
  final double pct;
  final String tokenSymbol;

  String _fmt(double value) {
    final s = value.toStringAsFixed(2);
    return s.replaceFirst(RegExp(r'\.?0+$'), '');
  }

  @override
  Widget build(BuildContext context) {
    return Container(
      // padding: const EdgeInsets.fromLTRB(8, 8, 8, 8),
      decoration: BoxDecoration(
        // border: Border.all(color: Palette.border.withValues(alpha: 0.4)),
        borderRadius: BorderRadius.circular(4),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            'SIDE $side',
            style: bodyStyle(
              size: 11,
              color: Palette.muted,
              letterSpacing: 0.6,
            ),
          ),
          const SizedBox(height: 3),
          Text(
            fighterName.toUpperCase(),
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
            style: bodyStyle(
              size: 12,
              color: Palette.secondary,
              weight: FontWeight.w600,
            ),
          ),
          const SizedBox(height: 2),
          Text(
            '${_fmt(amount)} $tokenSymbol',
            style: bodyStyle(
              size: 16,
              color: Palette.white,
              weight: FontWeight.w700,
            ),
          ),
          const SizedBox(height: 1),
          Text(
            '${(pct * 100).toStringAsFixed(1)}%',
            style: bodyStyle(
              size: 12,
              color: Palette.gold,
              weight: FontWeight.w600,
            ),
          ),
        ],
      ),
    );
  }
}

class _MarketRow extends StatelessWidget {
  const _MarketRow({
    required this.label,
    required this.value,
    this.valueColor = Palette.secondary,
  });

  final String label;
  final String value;
  final Color valueColor;

  @override
  Widget build(BuildContext context) {
    return Row(
      mainAxisAlignment: MainAxisAlignment.spaceBetween,
      children: [
        Text(label, style: bodyStyle(size: 13, color: Palette.muted)),
        Text(
          value,
          style: bodyStyle(
            size: 13,
            color: valueColor,
            weight: FontWeight.w600,
          ),
        ),
      ],
    );
  }
}

class _RecentBetsPanel extends StatelessWidget {
  const _RecentBetsPanel({required this.tokenSymbol, required this.future});

  final String tokenSymbol;
  final Future<List<MatchBetFeedItem>>? future;

  String _fmt(double value) {
    final s = value.toStringAsFixed(2);
    return s.replaceFirst(RegExp(r'\.?0+$'), '');
  }

  @override
  Widget build(BuildContext context) {
    if (future == null) {
      return const SizedBox.shrink();
    }
    return Container(
      padding: const EdgeInsets.fromLTRB(12, 12, 12, 10),
      decoration: BoxDecoration(
        // border: Border.all(color: Palette.border),
        borderRadius: BorderRadius.circular(6),
        // color: Palette.sheetBg.withValues(alpha: 0.2),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const GoldGradientDivider(
            margin: EdgeInsets.fromLTRB(18, 24, 18, 24),
          ),
          Center(
            child: Text(
              'BET HISTORY',
              style: bodyStyle(
                size: 13,
                color: Palette.gold,
                weight: FontWeight.w700,
                letterSpacing: 0.9,
              ),
            ),
          ),
          const SizedBox(height: 8),
          FutureBuilder<List<MatchBetFeedItem>>(
            future: future,
            builder: (context, snapshot) {
              if (snapshot.connectionState == ConnectionState.waiting) {
                return SizedBox(
                  width: double.infinity,
                  child: Text(
                    'Loading recent bets...',
                    textAlign: TextAlign.center,
                    style: bodyStyle(size: 13, color: Palette.muted),
                  ),
                );
              }
              final items = snapshot.data ?? const <MatchBetFeedItem>[];
              if (items.isEmpty) {
                return SizedBox(
                  width: double.infinity,
                  child: Text(
                    'No bets placed yet for this match.',
                    textAlign: TextAlign.center,
                    style: bodyStyle(size: 13, color: Palette.muted),
                  ),
                );
              }
              final visible = items.take(8).toList();
              return Column(
                children: [
                  for (final item in visible)
                    Padding(
                      padding: const EdgeInsets.only(bottom: 8),
                      child: Row(
                        children: [
                          Container(
                            width: 20,
                            height: 20,
                            alignment: Alignment.center,
                            decoration: BoxDecoration(
                              border: Border.all(color: Palette.border),
                              borderRadius: BorderRadius.circular(3),
                            ),
                            child: Text(
                              item.side,
                              style: bodyStyle(
                                size: 11,
                                color: Palette.gold,
                                weight: FontWeight.w700,
                              ),
                            ),
                          ),
                          const SizedBox(width: 8),
                          Expanded(
                            child: Text(
                              '${item.walletMasked} • ${item.fighterName}',
                              maxLines: 1,
                              overflow: TextOverflow.ellipsis,
                              style: bodyStyle(
                                size: 13,
                                color: Palette.secondary,
                              ),
                            ),
                          ),
                          const SizedBox(width: 8),
                          Text(
                            '${_fmt(item.amount)} $tokenSymbol',
                            style: bodyStyle(
                              size: 13,
                              color: Palette.white,
                              weight: FontWeight.w600,
                            ),
                          ),
                        ],
                      ),
                    ),
                ],
              );
            },
          ),
        ],
      ),
    );
  }
}
