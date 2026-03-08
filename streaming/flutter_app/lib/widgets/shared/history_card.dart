import 'package:flutter/material.dart';
import 'package:intl/intl.dart';
import '../../core/palette.dart';
import '../../core/typography.dart';
import '../../models/bet.dart';
import 'pressable.dart';

class HistoryCardWidget extends StatelessWidget {
  const HistoryCardWidget({
    super.key,
    required this.bet,
    required this.onTap,
  });

  final Bet bet;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final _HistoryVisualState visual = _resolveVisualState();
    final dateStr = DateFormat('dd/MM/yy- h:mma').format(bet.placedAt.toLocal());
    final stakeText = _formatCurrencyAmount(bet.amount);
    final outcomeText = _formatOutcomeAmount(visual.valueAmount);

    return Pressable(
      onTap: onTap,
      scaleTo: 0.98,
      opacityTo: 0.82,
      child: Padding(
        padding: const EdgeInsets.symmetric(vertical: 8),
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    '${bet.fighterName} V/S ${bet.opponentName}',
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: bodyStyle(
                      size: 16,
                      color: Palette.white,
                      weight: FontWeight.w300,
                    ),
                  ),
                  const SizedBox(height: 4),
                  Text(
                    'Bet - $stakeText',
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: bodyStyle(
                      size: 12,
                      color: Palette.white,
                      weight: FontWeight.w300,
                    ),
                  ),
                  const SizedBox(height: 4),
                  Text(
                    dateStr,
                    style: bodyStyle(
                      size: 12,
                      color: const Color(0xFF848484),
                      weight: FontWeight.w300,
                    ),
                  ),
                ],
              ),
            ),
            const SizedBox(width: 18),
            ConstrainedBox(
              constraints: const BoxConstraints(minWidth: 104, maxWidth: 132),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.end,
                children: [
                  Text(
                    outcomeText,
                    textAlign: TextAlign.right,
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: bodyStyle(
                      size: 16,
                      color: visual.valueColor,
                      weight: FontWeight.w300,
                    ),
                  ),
                  const SizedBox(height: 4),
                  Text(
                    visual.primaryStatus,
                    textAlign: TextAlign.right,
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: bodyStyle(
                      size: 12,
                      color: visual.statusColor,
                      weight: FontWeight.w300,
                    ),
                  ),
                  if (visual.secondaryStatus != null) ...[
                    const SizedBox(height: 2),
                    Text(
                      visual.secondaryStatus!,
                      textAlign: TextAlign.right,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: bodyStyle(
                        size: 12,
                        color: visual.statusColor,
                        weight: FontWeight.w300,
                      ),
                    ),
                  ],
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }

  _HistoryVisualState _resolveVisualState() {
    switch (bet.status) {
      case BetStatus.active:
        return _HistoryVisualState(
          primaryStatus: 'ACTIVE',
          statusColor: const Color(0xFF848484),
          valueColor: Palette.gold,
          valueAmount: bet.amount,
        );
      case BetStatus.won:
        final valueAmount = (bet.payout ?? 0) - bet.amount;
        return _HistoryVisualState(
          primaryStatus: 'WON',
          statusColor: const Color(0xFF848484),
          valueColor: _resolveOutcomeColor(valueAmount),
          valueAmount: valueAmount,
        );
      case BetStatus.lost:
        return _HistoryVisualState(
          primaryStatus: 'LOST',
          statusColor: const Color(0xFF848484),
          valueColor: Palette.red,
          valueAmount: -bet.amount,
        );
      case BetStatus.claimed:
        final valueAmount = (bet.payout ?? 0) - bet.amount;
        return _HistoryVisualState(
          primaryStatus: 'WON',
          secondaryStatus: 'CLAIMED',
          statusColor: const Color(0xFF848484),
          valueColor: _resolveOutcomeColor(valueAmount),
          valueAmount: valueAmount,
        );
      case BetStatus.cancelled:
        return _HistoryVisualState(
          primaryStatus: 'CANCELLED',
          statusColor: const Color(0xFF848484),
          valueColor: Palette.gold,
          valueAmount: 0,
        );
    }
  }

  Color _resolveOutcomeColor(double valueAmount) {
    if (valueAmount < 0) return Palette.red;
    if (valueAmount > 0) return Palette.green;
    return Palette.gold;
  }

  String _formatCurrencyAmount(double value) {
    final amount = _trimAmount(value.abs());
    return '$amount ${bet.currency}';
  }

  String _formatOutcomeAmount(double value) {
    final amount = _trimAmount(value.abs());
    if (bet.status == BetStatus.active || bet.status == BetStatus.cancelled) {
      return '$amount ${bet.currency}';
    }
    final sign = value < 0 ? '- ' : '+ ';
    return '$sign$amount ${bet.currency}';
  }

  String _trimAmount(double value) {
    final text = value.toStringAsFixed(2);
    return text.replaceFirst(RegExp(r'\.?0+$'), '');
  }
}

class _HistoryVisualState {
  const _HistoryVisualState({
    required this.primaryStatus,
    required this.statusColor,
    required this.valueColor,
    required this.valueAmount,
    this.secondaryStatus,
  });

  final String primaryStatus;
  final String? secondaryStatus;
  final Color statusColor;
  final Color valueColor;
  final double valueAmount;
}
