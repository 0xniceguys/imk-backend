import 'package:flutter/material.dart';
import '../../core/palette.dart';
import '../../core/runtime_client_config.dart';
import '../../core/typography.dart';
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
    final queueLabel = _queueText(match);
    final fighterVs =
        '${match.fighter1?.name ?? '?'} VS ${match.fighter2?.name ?? '?'}';
    final tokenSymbol = RuntimeClientConfig.instance.tokenSymbol;

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
                height: 32,
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
                      const _StatusPill(label: 'LIVE', live: true)
                    else
                      Text(
                        queueLabel,
                        style: displayStyle(
                          size: 13,
                          color: Palette.gold,
                          letterSpacing: 0.6,
                        ),
                      ),
                    const SizedBox(width: 8),
                    Expanded(
                      child: Text(
                        fighterVs,
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        textAlign: TextAlign.right,
                        style: bodyStyle(size: 11, color: Palette.secondary),
                      ),
                    ),
                  ],
                ),
              ),
              Padding(
                padding: const EdgeInsets.fromLTRB(10, 10, 10, 8),
                child: Row(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    SizedBox(
                      width: 140,
                      height: 98,
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
                          : const SizedBox.shrink(),
                    ),
                    const SizedBox(width: 10),
                    Expanded(
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          _StatCell(
                            label: 'Total Pool',
                            value:
                                '${match.totalPool.toStringAsFixed(2)} $tokenSymbol',
                          ),
                          const SizedBox(height: 8),
                          Row(
                            children: [
                              Expanded(
                                child: _StatCell(
                                  label: 'Active Bets',
                                  value: '${match.activeBets}',
                                ),
                              ),
                              const SizedBox(width: 10),
                              Expanded(
                                child: _StatCell(
                                  label: 'Best Of',
                                  value: '${match.bestOf}',
                                ),
                              ),
                            ],
                          ),
                        ],
                      ),
                    ),
                  ],
                ),
              ),
              const GoldGradientDivider(
                margin: EdgeInsets.symmetric(horizontal: 10),
              ),
              const SizedBox(height: 10),
            ],
          ),
        ),
      ),
    );
  }

  static String _queueText(Match match) {
    if (match.status == MatchStatus.live) {
      return 'LIVE';
    }
    if (match.status == MatchStatus.upcoming) {
      final q = match.queuePosition;
      if (q == 1) {
        final startsAt = match.queueStartsAt;
        if (startsAt == null) return 'NEXT MATCH';
        final remain = startsAt.difference(DateTime.now()).inSeconds;
        if (remain <= 0) return 'NEXT MATCH';
        final safe = remain;
        final mm = (safe ~/ 60).toString().padLeft(2, '0');
        final ss = (safe % 60).toString().padLeft(2, '0');
        return 'NEXT MATCH  $mm:$ss';
      }
      if (q != null && q >= 2) return '#$q IN-QUEUE';
      return 'IN-QUEUE';
    }
    if (match.status == MatchStatus.completed) return 'COMPLETED';
    return 'CANCELLED';
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
    return Column(
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
    );
  }
}
