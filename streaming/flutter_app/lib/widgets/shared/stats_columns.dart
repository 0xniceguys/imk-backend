import 'package:flutter/material.dart';
import '../../core/palette.dart';
import '../../core/typography.dart';
import '../../models/fighter.dart';

class StatsColumnsWidget extends StatelessWidget {
  const StatsColumnsWidget({
    super.key,
    required this.fighter,
    this.fontSize = 16,
    this.gap = 20,
  });

  final Fighter fighter;
  final double fontSize;
  final double gap;

  @override
  Widget build(BuildContext context) {
    final losses = fighter.matchesPlayed - fighter.matchesWon;
    return Column(
      children: [
        Container(height: 1, color: Palette.darkGold),
        const SizedBox(height: 20),
        Row(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            _col(
              fontSize,
              label1: 'Win Rate',
              value1: '${(fighter.winRate * 100).toStringAsFixed(0)}%',
              label2: 'Matches Won',
              value2: '${fighter.matchesWon}',
            ),
            SizedBox(width: gap),
            _col(
              fontSize,
              label1: 'Played',
              value1: '${fighter.matchesPlayed}',
              label2: 'Losses',
              value2: '$losses',
            ),
          ],
        ),
      ],
    );
  }

  Widget _col(double s,
      {required String label1,
      required String value1,
      required String label2,
      required String value2}) {
    return SizedBox(
      width: 102,
      child: Column(
        children: [
          Text(label1,
              style: bodyStyle(size: s, color: Palette.statLabel)),
          const SizedBox(height: 8),
          Text(value1, style: bodyStyle(size: s)),
          const SizedBox(height: 8),
          Text(label2,
              style: bodyStyle(size: s, color: Palette.statLabel)),
          const SizedBox(height: 8),
          Text(value2, style: bodyStyle(size: s)),
        ],
      ),
    );
  }
}

class DetailStatsSection extends StatelessWidget {
  const DetailStatsSection({
    super.key,
    required this.title,
    required this.fighter,
  });

  final String title;
  final Fighter fighter;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 24),
      child: Column(
        children: [
          Container(height: 1, color: Palette.darkGold),
          const SizedBox(height: 16),
          Text(
            title,
            style: displayStyle(size: 22, color: Palette.statLabel),
          ),
          const SizedBox(height: 16),
          StatsColumnsWidget(fighter: fighter, fontSize: 14, gap: 16),
        ],
      ),
    );
  }
}
