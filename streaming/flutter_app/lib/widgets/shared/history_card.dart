import 'package:flutter/material.dart';
import '../../core/palette.dart';
import '../../core/typography.dart';
import '../../models/bet.dart';

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
    final Color amountColor;
    final String statusText;
    switch (bet.status) {
      case BetStatus.active:
        amountColor = Palette.white;
        statusText = 'Active';
      case BetStatus.won:
        amountColor = Palette.green;
        statusText = 'Won';
      case BetStatus.lost:
        amountColor = Palette.red;
        statusText = 'Lost';
      case BetStatus.cancelled:
        amountColor = Palette.muted;
        statusText = 'Cancelled';
      case BetStatus.claimed:
        amountColor = Palette.green;
        statusText = 'Claimed';
    }

    return SizedBox(
      width: 260,
      child: Card(
        margin: EdgeInsets.zero,
        color: Colors.transparent,
        elevation: 0,
        shape: const RoundedRectangleBorder(
          borderRadius: BorderRadius.zero,
          side: BorderSide(color: Palette.muted, width: 2),
        ),
        child: InkWell(
          onTap: onTap,
          child: Padding(
            padding:
                const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
            child: Row(
              children: [
                SizedBox(
                  width: 70,
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text('\$${bet.amount.toStringAsFixed(0)}',
                          style: bodyStyle(size: 16, color: amountColor)),
                      Text(statusText,
                          style: bodyStyle(
                              size: 12, color: Palette.statLabel)),
                    ],
                  ),
                ),
                const SizedBox(width: 16),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                          '${bet.fighterName} V/S ${bet.opponentName}',
                          style: bodyStyle(size: 16)),
                      Text('Singles Battle',
                          style: bodyStyle(
                              size: 12, color: Palette.statLabel)),
                    ],
                  ),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}
