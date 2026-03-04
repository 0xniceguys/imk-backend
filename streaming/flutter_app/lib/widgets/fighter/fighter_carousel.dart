import 'package:flutter/material.dart';
import '../../core/palette.dart';
import '../../core/typography.dart';
import '../../core/constants.dart';
import '../../models/fighter.dart';
import '../shared/pressable.dart';

class FighterCarousel extends StatefulWidget {
  const FighterCarousel({
    super.key,
    required this.fighters,
    required this.onMoreDetails,
  });

  final List<Fighter> fighters;
  final void Function(String fighterId) onMoreDetails;

  @override
  State<FighterCarousel> createState() => _FighterCarouselState();
}

class _FighterCarouselState extends State<FighterCarousel> {
  late final PageController _controller;
  int _current = 0;

  @override
  void initState() {
    super.initState();
    _controller = PageController(viewportFraction: 0.62);
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final fighter = widget.fighters[_current];

    return Column(
      children: [
        SizedBox(
          height: 560,
          child: Stack(
            alignment: Alignment.bottomCenter,
            children: [
              PageView.builder(
                controller: _controller,
                itemCount: widget.fighters.length,
                onPageChanged: (index) => setState(() => _current = index),
                itemBuilder: (context, index) {
                  final active = index == _current;
                  final item = widget.fighters[index];
                  final fallback = active
                      ? Assets.fighterCenter
                      : index < _current
                          ? Assets.fighterLeft
                          : Assets.fighterRight;

                  return Align(
                    alignment: Alignment.bottomCenter,
                    child: AnimatedContainer(
                      duration: const Duration(milliseconds: 260),
                      curve: Curves.easeOutCubic,
                      height: active ? 470 : 350,
                      margin: EdgeInsets.only(
                        top: active ? 24 : 88,
                        bottom: active ? 0 : 18,
                      ),
                      child: Pressable(
                        onTap: active
                            ? () => widget.onMoreDetails(item.id)
                            : null,
                        scaleTo: 0.97,
                        opacityTo: 0.92,
                        child: AnimatedOpacity(
                          duration: const Duration(milliseconds: 260),
                          opacity: active ? 1 : 0.28,
                          child: _FighterArtwork(
                            fighter: item,
                            fallbackAsset: fallback,
                          ),
                        ),
                      ),
                    ),
                  );
                },
              ),
              Positioned(
                left: 20,
                right: 20,
                bottom: 8,
                child: IgnorePointer(
                  child: Column(
                    children: [
                      AnimatedSwitcher(
                        duration: const Duration(milliseconds: 220),
                        child: Text(
                          fighter.name,
                          key: ValueKey('fighter_name_${fighter.id}'),
                          textAlign: TextAlign.center,
                          maxLines: 1,
                          overflow: TextOverflow.ellipsis,
                          style: displayStyle(
                            size: 54,
                            color: Palette.gold,
                            height: 0.92,
                          ),
                        ),
                      ),
                      const SizedBox(height: 10),
                      AnimatedSwitcher(
                        duration: const Duration(milliseconds: 220),
                        child: Text(
                          fighter.llmModel,
                          key: ValueKey('fighter_model_${fighter.id}'),
                          textAlign: TextAlign.center,
                          maxLines: 1,
                          overflow: TextOverflow.ellipsis,
                          style: bodyStyle(
                            size: 22,
                            color: Palette.secondary,
                          ),
                        ),
                      ),
                    ],
                  ),
                ),
              ),
            ],
          ),
        ),
        Container(
          height: 1,
          margin: const EdgeInsets.symmetric(horizontal: 20),
          color: Palette.darkGold,
        ),
        const SizedBox(height: 30),
        Padding(
          padding: const EdgeInsets.symmetric(horizontal: 28),
          child: _FighterOverviewStats(fighter: fighter),
        ),
        const SizedBox(height: 28),
        Pressable(
          onTap: () => widget.onMoreDetails(fighter.id),
          scaleTo: 0.96,
          opacityTo: 0.8,
          child: Text(
            'MORE DETAILS',
            style: displayStyle(
              size: 26,
              color: Palette.secondary,
              decoration: TextDecoration.underline,
            ),
          ),
        ),
        const SizedBox(height: 20),
      ],
    );
  }
}

class _FighterArtwork extends StatelessWidget {
  const _FighterArtwork({
    required this.fighter,
    required this.fallbackAsset,
  });

  final Fighter fighter;
  final String fallbackAsset;

  @override
  Widget build(BuildContext context) {
    final imageUrl = fighter.resolvedImageUrl;
    if (imageUrl != null) {
      return Image.network(
        imageUrl,
        fit: BoxFit.contain,
        errorBuilder: (_, error, stackTrace) =>
            Image.asset(fallbackAsset, fit: BoxFit.contain),
      );
    }
    return Image.asset(fallbackAsset, fit: BoxFit.contain);
  }
}

class _FighterOverviewStats extends StatelessWidget {
  const _FighterOverviewStats({required this.fighter});

  final Fighter fighter;

  @override
  Widget build(BuildContext context) {
    return Row(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Expanded(
          child: _StatColumn(
            stats: [
              _StatItem('Win Rate', '${(fighter.winRate * 100).toStringAsFixed(0)}%'),
              _StatItem('Matches Played', '${fighter.matchesPlayed}'),
              _StatItem('Matches Won', '${fighter.matchesWon}'),
            ],
          ),
        ),
        const SizedBox(width: 20),
        Expanded(
          child: _StatColumn(
            stats: [
              _StatItem('Rank', fighter.rank > 0 ? '#${fighter.rank}' : 'Unranked'),
              _StatItem(
                'Fight Style',
                fighter.fightStyle.isEmpty ? 'Unknown' : fighter.fightStyle,
              ),
              _StatItem(
                'Origin',
                fighter.origin.isEmpty ? 'Unknown' : fighter.origin,
              ),
            ],
          ),
        ),
      ],
    );
  }
}

class _StatColumn extends StatelessWidget {
  const _StatColumn({required this.stats});

  final List<_StatItem> stats;

  @override
  Widget build(BuildContext context) {
    return Column(
      children: stats
          .map(
            (stat) => Padding(
              padding: const EdgeInsets.only(bottom: 26),
              child: Column(
                children: [
                  Text(
                    stat.label,
                    textAlign: TextAlign.center,
                    style: bodyStyle(size: 18, color: Palette.statLabel),
                  ),
                  const SizedBox(height: 10),
                  Text(
                    stat.value,
                    textAlign: TextAlign.center,
                    maxLines: 2,
                    overflow: TextOverflow.ellipsis,
                    style: bodyStyle(size: 20, color: Palette.white),
                  ),
                ],
              ),
            ),
          )
          .toList(),
    );
  }
}

class _StatItem {
  const _StatItem(this.label, this.value);

  final String label;
  final String value;
}
