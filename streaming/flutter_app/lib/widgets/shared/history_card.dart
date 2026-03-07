import 'package:flutter/material.dart';
import 'package:intl/intl.dart';
import '../../core/palette.dart';
import '../../core/typography.dart';
import '../../models/bet.dart';

class HistoryCardWidget extends StatelessWidget {
  const HistoryCardWidget({
    super.key,
    required this.bet,
    required this.onTap,
    this.onClaim,
    this.claimLoading = false,
  });

  final Bet bet;
  final VoidCallback onTap;
  final VoidCallback? onClaim;
  final bool claimLoading;

  @override
  Widget build(BuildContext context) {
    final Color statusColor;
    final String statusText;
    switch (bet.status) {
      case BetStatus.active:
        statusColor = Palette.gold;
        statusText = 'ACTIVE';
      case BetStatus.won:
        statusColor = Palette.green;
        statusText = 'WON';
      case BetStatus.lost:
        statusColor = Palette.red;
        statusText = 'LOST';
      case BetStatus.cancelled:
        statusColor = Palette.muted;
        statusText = 'CANCELLED';
      case BetStatus.claimed:
        statusColor = Palette.green;
        statusText = 'CLAIMED';
    }

    // P&L delta
    final double? pnl = switch (bet.status) {
      BetStatus.won || BetStatus.claimed => (bet.payout ?? 0) - bet.amount,
      BetStatus.lost => -bet.amount,
      _ => null,
    };

    final dateStr = DateFormat('MMM d, yy').format(bet.placedAt.toLocal());

    return SizedBox(
      width: double.infinity,
      child: Card(
        margin: const EdgeInsets.symmetric(horizontal: 0),
        color: Colors.transparent,
        elevation: 0,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(4),
          side: BorderSide(
            color: statusColor.withValues(alpha: 0.35),
            width: 1,
          ),
        ),
        child: InkWell(
          onTap: onTap,
          borderRadius: BorderRadius.circular(4),
          child: Padding(
            padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                // Top row: fighters + status badge
                Row(
                  children: [
                    Expanded(
                      child: Text(
                        '${bet.fighterName} V/S ${bet.opponentName}',
                        style: bodyStyle(size: 15, weight: FontWeight.w600),
                        overflow: TextOverflow.ellipsis,
                      ),
                    ),
                    const SizedBox(width: 8),
                    Container(
                      padding: const EdgeInsets.symmetric(
                        horizontal: 8,
                        vertical: 3,
                      ),
                      decoration: BoxDecoration(
                        color: statusColor.withValues(alpha: 0.15),
                        borderRadius: BorderRadius.circular(4),
                        border: Border.all(
                          color: statusColor.withValues(alpha: 0.5),
                        ),
                      ),
                      child: Text(
                        statusText,
                        style: bodyStyle(
                          size: 11,
                          color: statusColor,
                          weight: FontWeight.w700,
                        ),
                      ),
                    ),
                  ],
                ),
                const SizedBox(height: 8),
                // Bottom row: date | wagered | payout/P&L
                Row(
                  children: [
                    // Date
                    Text(
                      dateStr,
                      style: bodyStyle(size: 12, color: Palette.muted),
                    ),
                    const SizedBox(width: 12),
                    // Bet amount
                    Flexible(
                      child: Text(
                        '${bet.amount.toStringAsFixed(2)} ${bet.currency}',
                        style: bodyStyle(size: 12, color: Palette.secondary),
                        overflow: TextOverflow.ellipsis,
                      ),
                    ),
                    if (bet.payout != null) ...[
                      Text(
                        '  →  ',
                        style: bodyStyle(size: 12, color: Palette.muted),
                      ),
                      Flexible(
                        child: Text(
                          '${bet.payout!.toStringAsFixed(2)} ${bet.currency}',
                          style: bodyStyle(size: 12, color: Palette.green),
                          overflow: TextOverflow.ellipsis,
                        ),
                      ),
                    ],
                    const Spacer(),
                    // P&L delta chip
                    if (pnl != null)
                      Text(
                        '${pnl >= 0 ? '+' : ''}${pnl.toStringAsFixed(2)} ${bet.currency}',
                        style: bodyStyle(
                          size: 13,
                          color: pnl >= 0 ? Palette.green : Palette.red,
                          weight: FontWeight.w700,
                        ),
                      ),
                  ],
                ),
                if (bet.isClaimable && onClaim != null) ...[
                  const SizedBox(height: 10),
                  Align(
                    alignment: Alignment.centerRight,
                    child: TextButton(
                      onPressed: claimLoading ? null : onClaim,
                      style: TextButton.styleFrom(
                        foregroundColor: Palette.gold,
                      ),
                      child: claimLoading
                          ? SizedBox(
                              width: 14,
                              height: 14,
                              child: CircularProgressIndicator(
                                strokeWidth: 2,
                                color: Palette.gold,
                              ),
                            )
                          : Text(
                              'Claim Reward',
                              style: bodyStyle(
                                size: 12,
                                color: Palette.gold,
                                weight: FontWeight.w700,
                              ),
                            ),
                    ),
                  ),
                ],
              ],
            ),
          ),
        ),
      ),
    );
  }
}
