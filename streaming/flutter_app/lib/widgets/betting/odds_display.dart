import 'package:flutter/material.dart';
import '../../core/palette.dart';
import '../../core/typography.dart';
import '../../models/odds.dart';
import '../../models/match.dart';

class OddsDisplay extends StatelessWidget {
  const OddsDisplay({super.key, required this.odds, required this.match});

  final Odds odds;
  final Match match;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 8),
      child: Column(
        children: [
          // Pool bar
          Container(
            height: 6,
            decoration: BoxDecoration(
              borderRadius: BorderRadius.circular(3),
              color: Palette.border,
            ),
            child: Row(
              children: [
                Expanded(
                  flex: (odds.fighter1PoolPct * 100).round(),
                  child: Container(
                    decoration: const BoxDecoration(
                      borderRadius: BorderRadius.horizontal(
                          left: Radius.circular(3)),
                      color: Palette.gold,
                    ),
                  ),
                ),
                Expanded(
                  flex: (odds.fighter2PoolPct * 100).round(),
                  child: const SizedBox(),
                ),
              ],
            ),
          ),
          const SizedBox(height: 8),
          // Odds row
          Row(
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            children: [
              Column(
                children: [
                  Text(match.fighter1?.name ?? '?',
                      style: bodyStyle(size: 11, color: Palette.secondary)),
                  Text('${odds.fighter1Odds.toStringAsFixed(1)}x',
                      style: displayStyle(size: 16, color: Palette.gold)),
                ],
              ),
              Column(
                children: [
                  Text(match.fighter2?.name ?? '?',
                      style: bodyStyle(size: 11, color: Palette.secondary)),
                  Text('${odds.fighter2Odds.toStringAsFixed(1)}x',
                      style: displayStyle(size: 16, color: Palette.gold)),
                ],
              ),
            ],
          ),
          const SizedBox(height: 6),
          Text('Pool: ${match.totalPool.toStringAsFixed(1)} SOL',
              style: bodyStyle(size: 11, color: Palette.muted)),
        ],
      ),
    );
  }
}
