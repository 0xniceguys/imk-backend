import 'package:flutter/material.dart';
import '../../core/palette.dart';
import '../../core/typography.dart';

class ProfileStatsWidget extends StatelessWidget {
  const ProfileStatsWidget({
    super.key,
    this.winRate = '10%',
    this.plOverall = '+\$5150',
    this.totalBets = '51',
    this.bettingFor = '41days',
  });

  final String winRate;
  final String plOverall;
  final String totalBets;
  final String bettingFor;

  @override
  Widget build(BuildContext context) {
    return Column(
      children: [
        const SizedBox(height: 22),
        Row(
          mainAxisAlignment: MainAxisAlignment.spaceBetween,
          children: [
            _Stat(title: 'Win Rate', value: winRate),
            _Stat(
              title: 'P/L Overall',
              value: plOverall,
              valueColor: Palette.green,
            ),
          ],
        ),
        const SizedBox(height: 17),
        Row(
          mainAxisAlignment: MainAxisAlignment.spaceBetween,
          children: [
            _Stat(title: 'Total Bets', value: totalBets),
            _Stat(title: 'Betting For', value: bettingFor),
          ],
        ),
        const SizedBox(height: 22),
        const _ProfileDivider(),
      ],
    );
  }
}

class _ProfileDivider extends StatelessWidget {
  const _ProfileDivider();

  @override
  Widget build(BuildContext context) {
    return Container(
      height: 1,
      width: 1000,
      margin: const EdgeInsets.only(bottom: 12),
      decoration: const BoxDecoration(
        gradient: LinearGradient(
          colors: [
            Colors.transparent,
            Color(0xFFFFC500),
            Colors.transparent,
          ],
        ),
      ),
    );
  }
}

class _Stat extends StatelessWidget {
  const _Stat({
    required this.title,
    required this.value,
    this.valueColor = Palette.white,
  });

  final String title;
  final String value;
  final Color valueColor;

  @override
  Widget build(BuildContext context) {
    return SizedBox(
      width: 102,
      child: Column(
        children: [
          Text(title,
              style: bodyStyle(size: 16, color: Palette.statLabel)),
          const SizedBox(height: 8),
          Text(value, style: bodyStyle(size: 16, color: valueColor)),
        ],
      ),
    );
  }
}
