import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../core/palette.dart';
import '../core/typography.dart';
import '../core/constants.dart';
import '../router.dart';
import '../providers/fighter_provider.dart';
import '../widgets/shared/bottom_nav.dart';
import '../widgets/shared/pressable.dart';
import '../widgets/shared/stats_columns.dart';

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
    final idx = fighterId != null
        ? fighters.indexWhere((f) => f.id == fighterId)
        : 0;
    final safeIdx = idx >= 0 ? idx : 0;
    final fighter = fighters[safeIdx];
    final prevIdx = safeIdx > 0 ? safeIdx - 1 : fighters.length - 1;
    final nextIdx = safeIdx < fighters.length - 1 ? safeIdx + 1 : 0;
    final top = MediaQuery.of(context).padding.top;
    final bottom = MediaQuery.of(context).padding.bottom;

    return Column(
      children: [
        SizedBox(height: top + 8),
        Align(
          alignment: Alignment.centerLeft,
          child: Padding(
            padding: const EdgeInsets.only(left: 26),
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
                  const Icon(Icons.arrow_back_ios,
                      size: 16, color: Palette.muted),
                  const SizedBox(width: 4),
                  Text('Back',
                      style: displayStyle(size: 22, color: Palette.muted)),
                ],
              ),
            ),
          ),
        ),
        const SizedBox(height: 8),
        Expanded(
          child: SingleChildScrollView(
            child: Column(
              children: [
                ConstrainedBox(
                  constraints: const BoxConstraints(maxHeight: 320),
                  child: Image.asset(Assets.detailsHero,
                      width: 148, fit: BoxFit.contain),
                ),
                Text(fighter.name,
                    style: displayStyle(size: 40, color: Palette.gold)),
                Text(fighter.llmModel,
                    style:
                        bodyStyle(size: 18, color: Palette.secondary)),
                const SizedBox(height: 20),
                DetailStatsSection(
                    title: 'Overall Stats', fighter: fighter),
                const SizedBox(height: 32),
                DetailStatsSection(
                    title: 'Training Stats', fighter: fighter),
                const SizedBox(height: 32),
                DetailStatsSection(
                    title: 'Skills Tree', fighter: fighter),
                const SizedBox(height: 32),
                DetailStatsSection(
                    title: 'Match History', fighter: fighter),
                const SizedBox(height: 32),
                // Prev/Next navigation
                Padding(
                  padding: const EdgeInsets.symmetric(horizontal: 24),
                  child: Container(
                    padding:
                        const EdgeInsets.only(top: 18, bottom: 24),
                    decoration: const BoxDecoration(
                      border: Border(
                          top: BorderSide(color: Palette.darkGold)),
                    ),
                    child: Row(
                      mainAxisAlignment: MainAxisAlignment.spaceBetween,
                      children: [
                        Pressable(
                          onTap: () => onNavigate(
                              '/fighter-details/${fighters[prevIdx].id}'),
                          child: Row(
                            mainAxisSize: MainAxisSize.min,
                            children: [
                              const Icon(Icons.arrow_back_ios,
                                  size: 14, color: Palette.muted),
                              Text(fighters[prevIdx].name,
                                  style: displayStyle(
                                      size: 18, color: Palette.muted)),
                            ],
                          ),
                        ),
                        Pressable(
                          onTap: () => onNavigate(
                              '/fighter-details/${fighters[nextIdx].id}'),
                          child: Row(
                            mainAxisSize: MainAxisSize.min,
                            children: [
                              Text(fighters[nextIdx].name,
                                  style: displayStyle(
                                      size: 18, color: Palette.muted)),
                              const Icon(Icons.arrow_forward_ios,
                                  size: 14, color: Palette.muted),
                            ],
                          ),
                        ),
                      ],
                    ),
                  ),
                ),
              ],
            ),
          ),
        ),
        const SizedBox(height: 12),
        BottomNavWidget(
          active: NavTab.fighters,
          onTapArena: () => onNavigate('/arena-list'),
          onTapFighters: () => onNavigate('/fighter-overview'),
          onTapProfile: () => onNavigate('/profile'),
        ),
        SizedBox(height: bottom > 0 ? bottom : 12),
      ],
    );
  }
}
