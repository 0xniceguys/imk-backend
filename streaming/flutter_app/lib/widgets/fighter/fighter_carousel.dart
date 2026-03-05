import 'package:flutter/material.dart';
import '../../core/palette.dart';
import '../../core/typography.dart';
import '../../core/constants.dart';
import '../../models/fighter.dart';
import '../shared/stats_columns.dart';
import '../shared/ornate_button.dart';

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
    _controller = PageController(viewportFraction: 0.55, initialPage: 0);
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
        // Carousel — takes flexible space
        Expanded(
          child: PageView.builder(
            controller: _controller,
            itemCount: widget.fighters.length,
            onPageChanged: (i) => setState(() => _current = i),
            itemBuilder: (context, index) {
              final isActive = index == _current;
              final f = widget.fighters[index];
              // Resolve relative backend image paths
              final resolvedUrl = f.resolvedImageUrl(kStreamBaseUrl);
              Widget image;
              if (resolvedUrl != null) {
                image = Image.network(
                  resolvedUrl,
                  fit: BoxFit.contain,
                  errorBuilder: (_, __, ___) =>
                      Image.asset(Assets.fighterCenter, fit: BoxFit.contain),
                );
              } else {
                String img;
                if (isActive) {
                  img = Assets.fighterCenter;
                } else if (index < _current) {
                  img = Assets.fighterLeft;
                } else {
                  img = Assets.fighterRight;
                }
                image = Image.asset(img, fit: BoxFit.contain);
              }
              return AnimatedScale(
                scale: isActive ? 1.0 : 0.8,
                duration: const Duration(milliseconds: 250),
                child: AnimatedOpacity(
                  opacity: isActive ? 1.0 : 0.5,
                  duration: const Duration(milliseconds: 250),
                  child: image,
                ),
              );
            },
          ),
        ),
        const SizedBox(height: 8),
        // Animated dot indicators
        Row(
          mainAxisAlignment: MainAxisAlignment.center,
          children: List.generate(widget.fighters.length, (i) {
            return AnimatedContainer(
              duration: const Duration(milliseconds: 250),
              width: i == _current ? 20 : 8,
              height: 8,
              margin: const EdgeInsets.symmetric(horizontal: 4),
              decoration: BoxDecoration(
                borderRadius: BorderRadius.circular(4),
                color: i == _current ? Palette.gold : Palette.muted,
              ),
            );
          }),
        ),
        const SizedBox(height: 8),
        // Fighter name crossfades on page change
        AnimatedSwitcher(
          duration: const Duration(milliseconds: 250),
          child: Text(fighter.name,
              key: ValueKey('name_${fighter.id}'),
              style: displayStyle(size: 36, color: Palette.gold)),
        ),
        AnimatedSwitcher(
          duration: const Duration(milliseconds: 250),
          child: Text(fighter.llmModel,
              key: ValueKey('model_${fighter.id}'),
              style: bodyStyle(size: 16, color: Palette.secondary)),
        ),
        // Tags row: fight style + origin
        if (fighter.fightStyle != null || fighter.origin != null)
          Padding(
            padding: const EdgeInsets.only(top: 4),
            child: Row(
              mainAxisAlignment: MainAxisAlignment.center,
              children: [
                if (fighter.fightStyle != null)
                  _FighterTag(label: fighter.fightStyle!),
                if (fighter.fightStyle != null && fighter.origin != null)
                  const SizedBox(width: 6),
                if (fighter.origin != null)
                  _FighterTag(label: '📍 ${fighter.origin!}'),
              ],
            ),
          ),
        const SizedBox(height: 10),
        SizedBox(
          width: 280,
          child: StatsColumnsWidget(fighter: fighter),
        ),
        const SizedBox(height: 14),
        OrnateButton(
          label: 'MORE DETAILS',
          onTap: () => widget.onMoreDetails(fighter.id),
        ),
        const SizedBox(height: 8),
      ],
    );
  }
}

class _FighterTag extends StatelessWidget {
  const _FighterTag({required this.label});
  final String label;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
      decoration: BoxDecoration(
        border: Border.all(color: Palette.gold.withValues(alpha: 0.4)),
        borderRadius: BorderRadius.circular(4),
        color: Palette.gold.withValues(alpha: 0.08),
      ),
      child: Text(label,
          style: bodyStyle(size: 11, color: Palette.gold)),
    );
  }
}
