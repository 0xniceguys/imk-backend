import 'dart:async';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../core/palette.dart';
import '../core/typography.dart';
import '../core/constants.dart';
import '../router.dart';
import '../models/match.dart';
import '../models/bet.dart';
import '../models/fighter.dart';
import '../providers/match_provider.dart';
import '../providers/bet_provider.dart';
import '../widgets/shared/app_shell.dart';
import '../widgets/shared/ik_loader.dart';
import '../widgets/shared/ornate_button.dart';
import '../widgets/shared/gold_gradient_divider.dart';
import '../widgets/fighter/fighter_image.dart';

class PostMatchScreen extends ConsumerStatefulWidget {
  const PostMatchScreen({super.key, required this.onNavigate, this.matchId});
  final void Function(String) onNavigate;
  final String? matchId;

  @override
  ConsumerState<PostMatchScreen> createState() => _PostMatchScreenState();
}

class _PostMatchScreenState extends ConsumerState<PostMatchScreen> {
  Timer? _settlementPoller;

  @override
  void dispose() {
    _settlementPoller?.cancel();
    super.dispose();
  }

  /// Starts polling every 1.5s until the match is settled.
  /// Called when the screen shows "Preparing Result".
  void _startSettlementPolling() {
    if (_settlementPoller?.isActive ?? false) return;
    _settlementPoller = Timer.periodic(const Duration(milliseconds: 1500), (_) {
      if (!mounted) { _settlementPoller?.cancel(); return; }
      ref.read(matchProvider.notifier).refresh();
    });
  }

  void _stopSettlementPolling() {
    _settlementPoller?.cancel();
    _settlementPoller = null;
  }

  @override
  Widget build(BuildContext context) {
    final matchState = ref.watch(matchProvider);
    final matches = matchState.matches;
    final bets = ref.watch(betProvider);

    Match? match;
    Match? requestedMatch;
    if (widget.matchId != null) {
      requestedMatch = matches.cast<Match?>().firstWhere(
        (m) => m?.id == widget.matchId,
        orElse: () => null,
      );
      if (requestedMatch == null) {
        _stopSettlementPolling();
        return _statusShell(
          title: 'Match not found',
          subtitle: 'The requested match is no longer available.',
        );
      }
      match = requestedMatch;
    }
    match ??= _latestSettledMatch(matches);

    if (match == null) {
      _stopSettlementPolling();
      return _statusShell(
        title: 'Result not available',
        subtitle: 'No completed matches yet.',
      );
    }
    final Match resolvedMatch = match;

    final isSettled =
        resolvedMatch.status == MatchStatus.completed ||
        resolvedMatch.status == MatchStatus.cancelled;
    if (!isSettled) {
      // Match exists but isn't settled yet — poll aggressively.
      _startSettlementPolling();
      return _statusShell(
        title: 'Preparing Result',
        subtitle: 'Settling the final result for this match.',
        showLoader: true,
        showBackButton: false, // no Back to Arena while waiting for result
      );
    }

    // Match is settled — stop polling.
    _stopSettlementPolling();

    final winner = _resolveWinner(resolvedMatch);

    // Find the user's bet for this match (if any)
    final userBet = bets.cast<Bet?>().firstWhere(
      (b) => b?.matchId == resolvedMatch.id,
      orElse: () => null,
    );

    return AppShell(
      activeTab: NavTab.arena,
      onNavigate: (slug) => widget.onNavigate(routeFor(slug)),
      scrollable: true,
      contentBottomPadding: 180,
      content: Column(
        children: [
          const SizedBox(height: 8),
          Text(
            resolvedMatch.status == MatchStatus.cancelled
                ? 'MATCH CANCELLED'
                : 'MATCH RESULT',
            style: bodyStyle(size: 13, color: Palette.secondary),
          ),
          const SizedBox(height: 12),
          // Winner portrait (reframed to avoid aggressive face/body crop)
          Container(
            width: 176,
            height: 204,
            decoration: const BoxDecoration(
              gradient: LinearGradient(
                begin: Alignment.topCenter,
                end: Alignment.bottomCenter,
                colors: [Color(0x80121212), Colors.transparent],
              ),
            ),
            child: Padding(
              padding: const EdgeInsets.all(8),
              child: winner != null
                  ? FighterImage(
                      fighter: winner,
                      fit: BoxFit.contain,
                      alignment: Alignment.topCenter,
                    )
                  : Image.asset(
                      Assets.detailsHero,
                      fit: BoxFit.contain,
                      alignment: Alignment.topCenter,
                    ),
            ),
          ),
          const SizedBox(height: 16),
          Text(
            winner != null ? 'WINNER' : 'NO WINNER',
            style: displayStyle(size: 18, color: Palette.gold),
          ),
          const SizedBox(height: 4),
          Text(
            winner?.name ??
                (resolvedMatch.status == MatchStatus.cancelled
                    ? 'Match Cancelled'
                    : 'Pending'),
            style: displayStyle(size: 36, color: Palette.gold),
          ),
          Text(
            winner?.llmModel ?? '',
            style: bodyStyle(size: 15, color: Palette.secondary),
          ),
          const GoldGradientDivider(
            margin: EdgeInsets.fromLTRB(20, 18, 20, 14),
          ),
          // Match stats
          Container(
            width: 260,
            padding: const EdgeInsets.all(16),
            decoration: BoxDecoration(
              border: Border.all(color: Palette.border),
              gradient: LinearGradient(
                begin: Alignment.topCenter,
                end: Alignment.bottomCenter,
                colors: [
                  Palette.sheetBg.withValues(alpha: 0.45),
                  Colors.transparent,
                ],
              ),
            ),
            child: Column(
              children: [
                _StatRow(label: 'Match', value: resolvedMatch.label),
                const SizedBox(height: 8),
                _StatRow(
                  label: 'Fighters',
                  value:
                      '${resolvedMatch.fighter1?.name ?? '?'} vs ${resolvedMatch.fighter2?.name ?? '?'}',
                ),
                const SizedBox(height: 8),
                _StatRow(
                  label: 'Total Pool',
                  value: '${resolvedMatch.totalPool.toStringAsFixed(1)} SKR',
                ),
              ],
            ),
          ),
          const SizedBox(height: 14),
          // Bet result
          if (userBet != null) _BetResult(bet: userBet) else _NoBetPlaced(),
          const SizedBox(height: 20),
          // Primary CTA: Claim Rewards if the user won and can claim, else Back to Arena
          if (userBet != null && userBet.isClaimable)
            OrnateButton(
              label: 'Claim Rewards',
              onTap: () => widget.onNavigate('/profile'),
            )
          else
            OrnateButton(
              label: 'Back to Arena',
              onTap: () => widget.onNavigate('/arena-list'),
            ),
          const SizedBox(height: 16),
        ],
      ),
    );
  }

  AppShell _statusShell({
    required String title,
    required String subtitle,
    bool showLoader = false,
    bool showBackButton = true,
  }) {
    return AppShell(
      activeTab: NavTab.arena,
      onNavigate: (slug) => widget.onNavigate(routeFor(slug)),
      content: Center(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            if (showLoader) ...[
              const IKLoader(size: 42),
              const SizedBox(height: 18),
            ],
            Text(title, style: displayStyle(size: 20, color: Palette.gold)),
            const SizedBox(height: 8),
            Text(subtitle, style: bodyStyle(color: Palette.muted)),
            if (showBackButton) ...[
              const SizedBox(height: 16),
              OrnateButton(
                label: 'Back to Arena',
                onTap: () => widget.onNavigate('/arena-list'),
              ),
            ],
          ],
        ),
      ),
    );
  }

  Match? _latestSettledMatch(List<Match> matches) {
    final settled = matches
        .where(
          (m) =>
              m.status == MatchStatus.completed ||
              m.status == MatchStatus.cancelled,
        )
        .toList();
    if (settled.isEmpty) return null;
    settled.sort((a, b) {
      final at = a.completedAt ?? a.scheduledAt;
      final bt = b.completedAt ?? b.scheduledAt;
      return bt.compareTo(at);
    });
    return settled.first;
  }

  static Fighter? _resolveWinner(Match match) {
    final winnerId = match.winnerId;
    if (winnerId == null) return null;
    if (winnerId == match.fighter1?.id) return match.fighter1;
    if (winnerId == match.fighter2?.id) return match.fighter2;
    return null;
  }
}

class _BetResult extends StatelessWidget {
  const _BetResult({required this.bet});
  final Bet bet;

  @override
  Widget build(BuildContext context) {
    final isWon = bet.status == BetStatus.won;
    final isLost = bet.status == BetStatus.lost;
    final color = isWon
        ? Palette.green
        : (isLost ? Palette.red : Palette.muted);
    final label = isWon
        ? 'You Won!'
        : isLost
        ? 'You Lost'
        : 'Bet ${bet.status.name}';
    final payoutText = isWon && bet.payout != null
        ? '+${bet.payout!.toStringAsFixed(2)} SKR'
        : isLost
        ? '-${bet.amount.toStringAsFixed(2)} SKR'
        : '${bet.amount.toStringAsFixed(2)} SKR';

    return Container(
      width: 260,
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        border: Border.all(color: color),
      ),
      child: Column(
        children: [
          Text(label, style: displayStyle(size: 24, color: color)),
          const SizedBox(height: 4),
          Text(payoutText, style: bodyStyle(size: 18, color: color)),
          const SizedBox(height: 4),
          Text(
            'Bet on ${bet.fighterName}',
            style: bodyStyle(size: 12, color: Palette.muted),
          ),
        ],
      ),
    );
  }
}

class _NoBetPlaced extends StatelessWidget {
  @override
  Widget build(BuildContext context) {
    return Container(
      width: 260,
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        border: Border.all(color: Palette.border),
      ),
      child: Text(
        'No bets placed on this match',
        style: bodyStyle(size: 14, color: Palette.muted),
        textAlign: TextAlign.center,
      ),
    );
  }
}

class _StatRow extends StatelessWidget {
  const _StatRow({required this.label, required this.value});

  final String label;
  final String value;

  @override
  Widget build(BuildContext context) {
    return Row(
      mainAxisAlignment: MainAxisAlignment.spaceBetween,
      children: [
        Text(label, style: bodyStyle(size: 14, color: Palette.muted)),
        Flexible(
          child: Text(
            value,
            style: bodyStyle(size: 14),
            textAlign: TextAlign.right,
          ),
        ),
      ],
    );
  }
}
