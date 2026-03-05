import 'package:flutter/material.dart';
import '../../core/palette.dart';
import '../../core/typography.dart';
import '../../core/constants.dart';
import '../../models/match.dart';

class ArenaCard extends StatelessWidget {
  const ArenaCard({super.key, required this.match, required this.onTap});

  final Match match;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final isLive = match.status == MatchStatus.live;
    final statusText = isLive ? 'Live' : 'Upcoming';
    return Card(
      margin: EdgeInsets.zero,
      color: Colors.transparent,
      elevation: 0,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.zero,
        side: BorderSide(color: Palette.border),
      ),
      child: InkWell(
        onTap: onTap,
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            // Status bar
            Container(
              height: 26,
              decoration: BoxDecoration(
                color: isLive
                    ? Palette.liveStatusBg
                    : Palette.upcomingStatusBg,
              ),
              alignment: Alignment.centerLeft,
              padding: const EdgeInsets.symmetric(horizontal: 16),
              child: Text(
                statusText,
                style: displayStyle(
                  size: 16,
                  color: isLive ? Palette.green : Palette.gold,
                ),
              ),
            ),
            Container(height: 1, color: Palette.border),
            // Content row
            Padding(
              padding: const EdgeInsets.all(10),
              child: Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  // Tile image
                  Container(
                    width: 147,
                    height: 94,
                    decoration: BoxDecoration(
                      border: Border.all(color: Palette.cardBg),
                    ),
                    clipBehavior: Clip.hardEdge,
                    child: Stack(
                      children: [
                        Positioned(
                          left: -28.55,
                          top: 0,
                          child: Image.asset(
                            Assets.arenaTile,
                            width: 208.811,
                            height: 117.456,
                            fit: BoxFit.cover,
                          ),
                        ),
                        Positioned(
                          left: -0.94,
                          top: -2.06,
                          child: Image.asset(
                            Assets.arenaTileAlt,
                            width: 145,
                            height: 98,
                            fit: BoxFit.cover,
                          ),
                        ),
                      ],
                    ),
                  ),
                  const SizedBox(width: 12),
                  // Stats columns
                  Expanded(
                    child: Row(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Expanded(
                          child: _StatsCol(
                            label1: 'Total Volume',
                            value1:
                                '\$${match.totalPool.toStringAsFixed(0)}',
                            label2: 'active Bets',
                            value2: '${match.activeBets}',
                          ),
                        ),
                        const SizedBox(width: 16),
                        Expanded(
                          child: _StatsCol(
                            label1: 'ROI Limit',
                            value1:
                                '${match.odds.fighter1Odds.toStringAsFixed(1)}-${match.odds.fighter2Odds.toStringAsFixed(1)}x',
                            label2: 'Players',
                            value2: '${match.activeBets}',
                          ),
                        ),
                      ],
                    ),
                  ),
                ],
              ),
            ),
            Container(height: 1, color: Palette.border),
            // Footer label
            SizedBox(
              height: 26,
              child: Center(
                child: Text(
                  '${match.fighter1?.character ?? '?'} V/S ${match.fighter2?.character ?? '?'} [${match.label}]',
                  style: bodyStyle(size: 12, color: Palette.white),
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _StatsCol extends StatelessWidget {
  const _StatsCol({
    required this.label1,
    required this.value1,
    required this.label2,
    required this.value2,
  });

  final String label1, value1, label2, value2;

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(label1,
            style: bodyStyle(size: 12, color: Palette.statLabel)),
        Text(value1, style: bodyStyle(size: 12)),
        const SizedBox(height: 10),
        Text(label2,
            style: bodyStyle(size: 12, color: Palette.statLabel)),
        Text(value2, style: bodyStyle(size: 12)),
      ],
    );
  }
}
