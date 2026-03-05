import 'package:flutter/material.dart';
import '../../core/palette.dart';
import '../../core/typography.dart';
import '../../core/constants.dart';
import '../../models/match.dart';
import '../fighter/fighter_image.dart';
import 'gold_gradient_divider.dart';

class ArenaCard extends StatelessWidget {
  const ArenaCard({super.key, required this.match, required this.onTap});

  final Match match;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final isLive = match.status == MatchStatus.live;
    final statusText = _statusText(match.status);
    return Material(
      color: Colors.transparent,
      child: InkWell(
        onTap: onTap,
        borderRadius: BorderRadius.circular(2),
        child: Container(
          decoration: BoxDecoration(
            gradient: LinearGradient(
              begin: Alignment.topCenter,
              end: Alignment.bottomCenter,
              colors: [
                Palette.sheetBg.withValues(alpha: 0.22),
                Colors.transparent,
              ],
            ),
          ),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              const GoldGradientDivider(),
              Container(
                height: 30,
                padding: const EdgeInsets.symmetric(horizontal: 12),
                decoration: BoxDecoration(
                  gradient: LinearGradient(
                    colors: [
                      (isLive ? Palette.liveStatusBg : Palette.upcomingStatusBg)
                          .withValues(alpha: 0.95),
                      Palette.black.withValues(alpha: 0.8),
                    ],
                  ),
                ),
                child: Row(
                  children: [
                    if (isLive)
                      _StatusPill(label: statusText, live: true)
                    else
                      Text(
                        statusText,
                        style: displayStyle(
                          size: 14,
                          color: Palette.gold,
                          letterSpacing: 0.8,
                        ),
                      ),
                    const Spacer(),
                    Text(
                      _timeLabel(match.scheduledAt),
                      style: bodyStyle(size: 11, color: Palette.secondary),
                    ),
                  ],
                ),
              ),
              Padding(
                padding: const EdgeInsets.fromLTRB(10, 10, 10, 8),
                child: Row(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Container(
                      width: 140,
                      height: 98,
                      clipBehavior: Clip.hardEdge,
                      decoration: const BoxDecoration(),
                      child: match.fighter1 != null && match.fighter2 != null
                          ? Row(
                              children: [
                                Expanded(
                                  child: FighterImage(
                                    fighter: match.fighter1!,
                                    fit: BoxFit.cover,
                                    alignment: Alignment.topCenter,
                                  ),
                                ),
                                Expanded(
                                  child: FighterImage(
                                    fighter: match.fighter2!,
                                    fit: BoxFit.cover,
                                    alignment: Alignment.topCenter,
                                  ),
                                ),
                              ],
                            )
                          : Stack(
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
                    const SizedBox(width: 10),
                    Expanded(
                      child: Wrap(
                        spacing: 8,
                        runSpacing: 8,
                        children: [
                          _StatCell(
                            label: 'Volume',
                            value: '\$${match.totalPool.toStringAsFixed(0)}',
                          ),
                          _StatCell(
                            label: 'Bets',
                            value: '${match.activeBets}',
                          ),
                          _StatCell(
                            label: 'ROI',
                            value:
                                '${match.odds.fighter1Odds.toStringAsFixed(1)}-${match.odds.fighter2Odds.toStringAsFixed(1)}x',
                          ),
                          _StatCell(label: 'Best Of', value: '${match.bestOf}'),
                        ],
                      ),
                    ),
                  ],
                ),
              ),
              const GoldGradientDivider(
                margin: EdgeInsets.symmetric(horizontal: 10),
              ),
              Padding(
                padding: const EdgeInsets.fromLTRB(12, 8, 12, 10),
                child: Row(
                  children: [
                    Expanded(
                      child: Text(
                        '${match.fighter1?.character ?? '?'} VS ${match.fighter2?.character ?? '?'}',
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        style: bodyStyle(size: 13, color: Palette.white),
                      ),
                    ),
                    Text(
                      '[${match.label}]',
                      style: bodyStyle(size: 11, color: Palette.secondary),
                    ),
                  ],
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }

  static String _timeLabel(DateTime at) {
    final local = at.toLocal();
    final hh = local.hour.toString().padLeft(2, '0');
    final mm = local.minute.toString().padLeft(2, '0');
    return '${local.day}/${local.month} $hh:$mm';
  }

  static String _statusText(MatchStatus status) {
    return switch (status) {
      MatchStatus.live => 'LIVE',
      MatchStatus.upcoming => 'COMING',
      MatchStatus.completed => 'COMPLETED',
      MatchStatus.cancelled => 'CANCELLED',
    };
  }
}

class _StatusPill extends StatelessWidget {
  const _StatusPill({required this.label, required this.live});

  final String label;
  final bool live;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
      decoration: BoxDecoration(
        borderRadius: BorderRadius.circular(10),
        color: (live ? Palette.green : Palette.gold).withValues(alpha: 0.15),
        border: Border.all(
          color: live ? Palette.green : Palette.gold,
          width: 0.8,
        ),
      ),
      child: Text(
        label.toUpperCase(),
        style: bodyStyle(
          size: 10,
          color: live ? Palette.green : Palette.gold,
          letterSpacing: 0.6,
        ),
      ),
    );
  }
}

class _StatCell extends StatelessWidget {
  const _StatCell({required this.label, required this.value});

  final String label;
  final String value;

  @override
  Widget build(BuildContext context) {
    return SizedBox(
      width: 72,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(label, style: bodyStyle(size: 11, color: Palette.statLabel)),
          const SizedBox(height: 2),
          Text(
            value,
            maxLines: 2,
            overflow: TextOverflow.ellipsis,
            style: bodyStyle(size: 14, color: Palette.white),
          ),
        ],
      ),
    );
  }
}
