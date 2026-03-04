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
    if (fighters.isEmpty) {
      return const Center(child: CircularProgressIndicator());
    }
    final idx = fighterId != null
        ? fighters.indexWhere((f) => f.id == fighterId)
        : 0;
    final safeIdx = idx >= 0 ? idx : 0;
    final fighter = fighters[safeIdx];
    final prevIdx = safeIdx > 0 ? safeIdx - 1 : fighters.length - 1;
    final nextIdx = safeIdx < fighters.length - 1 ? safeIdx + 1 : 0;
    final top = MediaQuery.of(context).padding.top;
    final bottom = MediaQuery.of(context).padding.bottom;

    // Resolve image URL for relative paths
    final resolvedUrl = fighter.resolvedImageUrl(kStreamBaseUrl);

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
                // Fighter image
                ConstrainedBox(
                  constraints: const BoxConstraints(maxHeight: 320),
                  child: resolvedUrl != null
                      ? Image.network(
                          resolvedUrl,
                          width: 148,
                          fit: BoxFit.contain,
                          errorBuilder: (_, __, ___) => Image.asset(
                              Assets.detailsHero,
                              width: 148,
                              fit: BoxFit.contain),
                        )
                      : Image.asset(Assets.detailsHero,
                          width: 148, fit: BoxFit.contain),
                ),
                Text(fighter.name,
                    style: displayStyle(size: 40, color: Palette.gold)),
                Text(fighter.llmModel,
                    style: bodyStyle(size: 18, color: Palette.secondary)),
                // Tags
                if (fighter.fightStyle != null || fighter.origin != null)
                  Padding(
                    padding: const EdgeInsets.only(top: 6),
                    child: Row(
                      mainAxisAlignment: MainAxisAlignment.center,
                      children: [
                        if (fighter.fightStyle != null)
                          _Tag(label: fighter.fightStyle!),
                        if (fighter.fightStyle != null && fighter.origin != null)
                          const SizedBox(width: 6),
                        if (fighter.origin != null)
                          _Tag(label: '📍 ${fighter.origin!}'),
                      ],
                    ),
                  ),
                const SizedBox(height: 20),

                // ── Section 1: Overall Stats ──
                _DetailSection(
                  title: 'Overall Stats',
                  child: StatsColumnsWidget(fighter: fighter, fontSize: 14, gap: 16),
                ),
                const SizedBox(height: 24),

                // ── Section 2: Fight Style ──
                _DetailSection(
                  title: 'Fight Style',
                  child: Column(
                    children: [
                      if (fighter.fightStyle != null)
                        Padding(
                          padding: const EdgeInsets.only(bottom: 8),
                          child: Text(fighter.fightStyle!,
                              style: displayStyle(size: 18, color: Palette.gold)),
                        ),
                      Text(
                        fighter.description ?? 'No description available.',
                        style: bodyStyle(size: 13, color: Palette.secondary),
                        textAlign: TextAlign.center,
                      ),
                    ],
                  ),
                ),
                const SizedBox(height: 24),

                // ── Section 3: Special Move ──
                _DetailSection(
                  title: 'Special Move',
                  child: fighter.specialMove != null
                      ? Column(
                          children: [
                            Icon(Icons.flash_on, color: Palette.gold, size: 24),
                            const SizedBox(height: 6),
                            Text(
                              fighter.specialMove!,
                              style: bodyStyle(size: 14, color: Palette.white),
                              textAlign: TextAlign.center,
                            ),
                          ],
                        )
                      : Text('No special move defined.',
                          style: bodyStyle(size: 13, color: Palette.secondary)),
                ),
                const SizedBox(height: 24),

                // ── Section 4: Agent Info ──
                _DetailSection(
                  title: 'AI Agent',
                  child: Column(
                    children: [
                      _InfoRow(
                        label: 'Architecture',
                        value: fighter.agentArchitecture ?? 'Unknown',
                      ),
                      if (fighter.rank != null)
                        _InfoRow(
                          label: 'Global Rank',
                          value: '#${fighter.rank}',
                          valueColor: Palette.gold,
                        ),
                      _InfoRow(
                        label: 'Losses',
                        value: '${fighter.losses}',
                      ),
                    ],
                  ),
                ),
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

/// A section divider block with a title and arbitrary content.
class _DetailSection extends StatelessWidget {
  const _DetailSection({required this.title, required this.child});
  final String title;
  final Widget child;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 24),
      child: Column(
        children: [
          Container(height: 1, color: Palette.darkGold),
          const SizedBox(height: 16),
          Text(title, style: displayStyle(size: 22, color: Palette.statLabel)),
          const SizedBox(height: 12),
          child,
        ],
      ),
    );
  }
}

/// A tag pill — shared with carousel.
class _Tag extends StatelessWidget {
  const _Tag({required this.label});
  final String label;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
      decoration: BoxDecoration(
        border: Border.all(color: Palette.gold.withOpacity(0.4)),
        borderRadius: BorderRadius.circular(4),
        color: Palette.gold.withOpacity(0.08),
      ),
      child: Text(label, style: bodyStyle(size: 12, color: Palette.gold)),
    );
  }
}

/// A label/value row for the agent info section.
class _InfoRow extends StatelessWidget {
  const _InfoRow({required this.label, required this.value, this.valueColor});
  final String label;
  final String value;
  final Color? valueColor;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 4),
      child: Row(
        mainAxisAlignment: MainAxisAlignment.spaceBetween,
        children: [
          Text(label, style: bodyStyle(size: 13, color: Palette.statLabel)),
          Text(value,
              style: bodyStyle(
                  size: 13, color: valueColor ?? Palette.white)),
        ],
      ),
    );
  }
}
