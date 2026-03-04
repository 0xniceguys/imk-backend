import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../core/palette.dart';
import '../core/typography.dart';
import '../core/constants.dart';
import '../models/fighter.dart';
import '../providers/fighter_provider.dart';
import '../widgets/shared/pressable.dart';
import '../widgets/shared/ik_loader.dart';

class FighterDetailsScreen extends ConsumerWidget {
  const FighterDetailsScreen({
    super.key,
    required this.onNavigate,
    this.fighterId,
  });

  final void Function(String) onNavigate;
  final String? fighterId;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final fighters = ref.watch(fighterProvider);
    if (fighters.isEmpty) {
      return const Center(child: IKLoader(size: 40));
    }

    final initialIndex =
        fighterId != null ? fighters.indexWhere((f) => f.id == fighterId) : 0;
    final currentIndex = initialIndex >= 0 ? initialIndex : 0;
    final fighter = fighters[currentIndex];
    final prevIndex = currentIndex > 0 ? currentIndex - 1 : fighters.length - 1;
    final nextIndex = currentIndex < fighters.length - 1 ? currentIndex + 1 : 0;
    final bottom = MediaQuery.of(context).padding.bottom;

    return SafeArea(
      bottom: false,
      child: SingleChildScrollView(
        padding: EdgeInsets.fromLTRB(24, 8, 24, bottom + 36),
        child: Column(
          children: [
            Align(
              alignment: Alignment.centerLeft,
              child: Pressable(
                onTap: () {
                  if (Navigator.of(context).canPop()) {
                    Navigator.of(context).pop();
                  } else {
                    onNavigate('/fighter-overview');
                  }
                },
                child: Row(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    const Icon(
                      Icons.arrow_back_ios,
                      size: 16,
                      color: Palette.muted,
                    ),
                    const SizedBox(width: 4),
                    Text(
                      'Back',
                      style: displayStyle(size: 22, color: Palette.muted),
                    ),
                  ],
                ),
              ),
            ),
            const SizedBox(height: 20),
            SizedBox(
              height: 350,
              child: Stack(
                alignment: Alignment.bottomCenter,
                children: [
                  Align(
                    alignment: Alignment.bottomCenter,
                    child: SizedBox(
                      height: 290,
                      child: _FighterDetailArtwork(fighter: fighter),
                    ),
                  ),
                  Positioned(
                    left: 0,
                    right: 0,
                    bottom: 0,
                    child: Column(
                      children: [
                        Text(
                          fighter.name,
                          textAlign: TextAlign.center,
                          maxLines: 1,
                          overflow: TextOverflow.ellipsis,
                          style: displayStyle(
                            size: 50,
                            color: Palette.gold,
                            height: 0.92,
                          ),
                        ),
                        const SizedBox(height: 8),
                        Text(
                          fighter.llmModel,
                          textAlign: TextAlign.center,
                          maxLines: 1,
                          overflow: TextOverflow.ellipsis,
                          style: bodyStyle(size: 22, color: Palette.secondary),
                        ),
                      ],
                    ),
                  ),
                ],
              ),
            ),
            Container(height: 1, color: Palette.darkGold),
            const SizedBox(height: 38),
            _DetailsSection(
              title: 'Overall Stats',
              children: [
                _StatsGrid(
                  items: [
                    _InfoItem('Win Rate', '${(fighter.winRate * 100).toStringAsFixed(0)}%'),
                    _InfoItem('Rank', fighter.rank > 0 ? '#${fighter.rank}' : 'Unranked'),
                    _InfoItem('Matches Played', '${fighter.matchesPlayed}'),
                    _InfoItem('Matches Won', '${fighter.matchesWon}'),
                    _InfoItem('Losses', '${fighter.matchesLost}'),
                    _InfoItem(
                      'Fight Style',
                      fighter.fightStyle.isEmpty ? 'Unknown' : fighter.fightStyle,
                    ),
                  ],
                ),
              ],
            ),
            const SizedBox(height: 42),
            _DetailsSection(
              title: 'Fighter Profile',
              children: [
                _ProfileLine(label: 'Character', value: fighter.character),
                _ProfileLine(
                  label: 'Architecture',
                  value: fighter.agentArchitecture?.toUpperCase() ?? 'Unknown',
                ),
                _ProfileLine(
                  label: 'Origin',
                  value: fighter.origin.isEmpty ? 'Unknown' : fighter.origin,
                ),
                _ProfileLine(
                  label: 'Slug',
                  value: fighter.slug.isEmpty ? 'Unknown' : fighter.slug,
                ),
              ],
            ),
            const SizedBox(height: 42),
            _DetailsSection(
              title: 'Special Move',
              children: [
                Text(
                  fighter.specialMove.isEmpty ? 'No special move listed.' : fighter.specialMove,
                  textAlign: TextAlign.center,
                  style: bodyStyle(
                    size: 18,
                    color: Palette.white,
                    height: 1.4,
                  ),
                ),
              ],
            ),
            const SizedBox(height: 42),
            _DetailsSection(
              title: 'Lore',
              children: [
                Text(
                  fighter.description.isEmpty ? 'No description available.' : fighter.description,
                  textAlign: TextAlign.center,
                  style: bodyStyle(
                    size: 18,
                    color: Palette.secondary,
                    height: 1.45,
                  ),
                ),
              ],
            ),
            const SizedBox(height: 48),
            Container(height: 1, color: Palette.darkGold),
            const SizedBox(height: 28),
            Row(
              mainAxisAlignment: MainAxisAlignment.spaceBetween,
              children: [
                Flexible(
                  child: Pressable(
                    onTap: () => onNavigate('/fighter-details/${fighters[prevIndex].id}'),
                    child: Text(
                      '← ${fighters[prevIndex].name}',
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: displayStyle(size: 22, color: Palette.muted),
                    ),
                  ),
                ),
                const SizedBox(width: 16),
                Flexible(
                  child: Pressable(
                    onTap: () => onNavigate('/fighter-details/${fighters[nextIndex].id}'),
                    child: Text(
                      '${fighters[nextIndex].name} →',
                      textAlign: TextAlign.right,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: displayStyle(size: 22, color: Palette.muted),
                    ),
                  ),
                ),
              ],
            ),
          ],
        ),
      ),
    );
  }
}

class _FighterDetailArtwork extends StatelessWidget {
  const _FighterDetailArtwork({required this.fighter});

  final Fighter fighter;

  @override
  Widget build(BuildContext context) {
    final imageUrl = fighter.resolvedImageUrl;
    if (imageUrl != null) {
      return Image.network(
        imageUrl,
        fit: BoxFit.contain,
        errorBuilder: (_, error, stackTrace) =>
            Image.asset(Assets.detailsHero, fit: BoxFit.contain),
      );
    }
    return Image.asset(Assets.detailsHero, fit: BoxFit.contain);
  }
}

class _DetailsSection extends StatelessWidget {
  const _DetailsSection({
    required this.title,
    required this.children,
  });

  final String title;
  final List<Widget> children;

  @override
  Widget build(BuildContext context) {
    return Column(
      children: [
        Text(
          title,
          textAlign: TextAlign.center,
          style: displayStyle(size: 28, color: Palette.statLabel),
        ),
        const SizedBox(height: 26),
        ...children,
      ],
    );
  }
}

class _StatsGrid extends StatelessWidget {
  const _StatsGrid({required this.items});

  final List<_InfoItem> items;

  @override
  Widget build(BuildContext context) {
    return Wrap(
      spacing: 18,
      runSpacing: 26,
      alignment: WrapAlignment.center,
      children: items
          .map(
            (item) => SizedBox(
              width: 150,
              child: Column(
                children: [
                  Text(
                    item.label,
                    textAlign: TextAlign.center,
                    style: bodyStyle(size: 18, color: Palette.statLabel),
                  ),
                  const SizedBox(height: 10),
                  Text(
                    item.value,
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

class _ProfileLine extends StatelessWidget {
  const _ProfileLine({
    required this.label,
    required this.value,
  });

  final String label;
  final String value;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 18),
      child: Column(
        children: [
          Text(
            label,
            textAlign: TextAlign.center,
            style: bodyStyle(size: 18, color: Palette.statLabel),
          ),
          const SizedBox(height: 8),
          Text(
            value,
            textAlign: TextAlign.center,
            style: bodyStyle(size: 20, color: Palette.white),
          ),
        ],
      ),
    );
  }
}

class _InfoItem {
  const _InfoItem(this.label, this.value);

  final String label;
  final String value;
}
