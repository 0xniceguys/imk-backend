import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../core/palette.dart';
import '../core/typography.dart';
import '../core/constants.dart';
import '../router.dart';
import '../models/match.dart';
import '../models/bet.dart';
import '../providers/match_provider.dart';
import '../providers/bet_provider.dart';
import '../widgets/shared/app_shell.dart';
import '../widgets/shared/ornate_button.dart';
import '../widgets/shared/gold_gradient_divider.dart';
import '../widgets/fighter/fighter_image.dart';

class PostMatchScreen extends ConsumerWidget {
  const PostMatchScreen({super.key, required this.onNavigate, this.matchId});
  final void Function(String) onNavigate;
  final String? matchId;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final matchState = ref.watch(matchProvider);
    final matches = matchState.matches;
    final bets = ref.watch(betProvider);

    // Find the specific match or first completed match
    Match? match;
    if (matchId != null) {
      match = matches.cast<Match?>().firstWhere(
        (m) => m?.id == matchId,
        orElse: () => null,
      );
    }
    match ??= matches.cast<Match?>().firstWhere(
      (m) => m?.status == MatchStatus.completed,
      orElse: () => matches.isNotEmpty ? matches.first : null,
    );

    if (match == null) {
      return AppShell(
        activeTab: NavTab.arena,
        onNavigate: (slug) => onNavigate(routeFor(slug)),
        content: Center(
          child: Text('No match data', style: bodyStyle(color: Palette.muted)),
        ),
      );
    }

    final winner = match.winnerId == match.fighter1?.id
        ? match.fighter1
        : match.fighter2;

    // Find the user's bet for this match (if any)
    final userBet = bets.cast<Bet?>().firstWhere(
      (b) => b?.matchId == match!.id,
      orElse: () => null,
    );

    return AppShell(
      activeTab: NavTab.arena,
      onNavigate: (slug) => onNavigate(routeFor(slug)),
      scrollable: true,
      contentBottomPadding: 180,
      content: Column(
        children: [
          const SizedBox(height: 8),
          Text(
            'MATCH RESULT',
            style: bodyStyle(size: 13, color: Palette.secondary),
          ),
          const SizedBox(height: 12),
          // Winner portrait (reframed to avoid aggressive face/body crop)
          Container(
            width: 176,
            height: 204,
            decoration: BoxDecoration(
              border: Border.all(color: Palette.gold, width: 3),
              borderRadius: BorderRadius.circular(10),
              gradient: LinearGradient(
                begin: Alignment.topCenter,
                end: Alignment.bottomCenter,
                colors: [
                  Palette.sheetBg.withValues(alpha: 0.5),
                  Colors.transparent,
                ],
              ),
            ),
            clipBehavior: Clip.antiAlias,
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
          Text('WINNER', style: displayStyle(size: 18, color: Palette.gold)),
          const SizedBox(height: 4),
          Text(
            winner?.name ?? 'Unknown',
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
              borderRadius: BorderRadius.circular(8),
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
                _StatRow(label: 'Match', value: match.label),
                const SizedBox(height: 8),
                _StatRow(
                  label: 'Fighters',
                  value:
                      '${match.fighter1?.name ?? '?'} vs ${match.fighter2?.name ?? '?'}',
                ),
                const SizedBox(height: 8),
                _StatRow(
                  label: 'Total Pool',
                  value: '${match.totalPool.toStringAsFixed(1)} SKR',
                ),
              ],
            ),
          ),
          const SizedBox(height: 14),
          // Bet result
          if (userBet != null) _BetResult(bet: userBet) else _NoBetPlaced(),
          const SizedBox(height: 20),
          OrnateButton(
            label: 'Back to Arena',
            onTap: () => onNavigate('/arena-list'),
          ),
          const SizedBox(height: 16),
        ],
      ),
    );
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
        borderRadius: BorderRadius.circular(4),
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
        borderRadius: BorderRadius.circular(4),
      ),
      child: Text(
        'No bet(S) placed on this match',
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
