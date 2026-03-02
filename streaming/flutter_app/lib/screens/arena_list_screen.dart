import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../core/palette.dart';
import '../core/typography.dart';
import '../router.dart';
import '../models/match.dart';
import '../providers/match_provider.dart';
import '../widgets/shared/app_shell.dart';
import '../widgets/shared/arena_card.dart';
import '../widgets/shared/pressable.dart';
import '../widgets/shared/ik_shimmer.dart';

class ArenaListScreen extends ConsumerStatefulWidget {
  const ArenaListScreen({super.key, required this.onNavigate});
  final void Function(String) onNavigate;

  @override
  ConsumerState<ArenaListScreen> createState() => _ArenaListScreenState();
}

class _ArenaListScreenState extends ConsumerState<ArenaListScreen> {
  // 0 = Live, 1 = Upcoming. Starts on Live — switches to Upcoming if no live.
  int _tab = 0;
  bool _tabInitialized = false;

  @override
  Widget build(BuildContext context) {
    final allMatches = ref.watch(matchProvider);
    final live = allMatches.where((m) => m.status == MatchStatus.live).toList();
    final upcoming = allMatches
        .where((m) => m.status == MatchStatus.upcoming)
        .toList();

    // Set initial tab based on data (only once)
    if (!_tabInitialized && allMatches.isNotEmpty) {
      _tabInitialized = true;
      WidgetsBinding.instance.addPostFrameCallback((_) {
        if (mounted) setState(() => _tab = live.isNotEmpty ? 0 : 1);
      });
    }

    final filtered = _tab == 0 ? live : upcoming;

    return AppShell(
      activeTab: NavTab.arena,
      scrollable: true,
      onNavigate: (slug) => widget.onNavigate(routeFor(slug)),
      content: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          // Tab row
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 16),
            child: Row(
              children: [
                _TabChip(
                  label: 'Live',
                  count: live.length,
                  active: _tab == 0,
                  onTap: () => setState(() => _tab = 0),
                ),
                const SizedBox(width: 10),
                _TabChip(
                  label: 'Upcoming',
                  count: upcoming.length,
                  active: _tab == 1,
                  onTap: () => setState(() => _tab = 1),
                ),
              ],
            ),
          ),
          const SizedBox(height: 16),
          // Match cards with staggered fade-in
          for (int i = 0; i < filtered.length; i++) ...[
            TweenAnimationBuilder<double>(
              key: ValueKey('${_tab}_${filtered[i].id}'),
              tween: Tween(begin: 0.0, end: 1.0),
              duration: Duration(milliseconds: 300 + (i * 80)),
              curve: Curves.easeOut,
              builder: (context, value, child) => Opacity(
                opacity: value,
                child: Transform.translate(
                  offset: Offset(0, 12 * (1 - value)),
                  child: child,
                ),
              ),
              child: ArenaCard(
                match: filtered[i],
                onTap: () {
                  final m = filtered[i];
                  if (m.status == MatchStatus.live) {
                    widget.onNavigate('/live-match/${m.id}');
                  } else {
                    widget.onNavigate('/battle-detail/${m.id}');
                  }
                },
              ),
            ),
            if (i < filtered.length - 1) const SizedBox(height: 28),
          ],
          if (filtered.isEmpty && allMatches.isNotEmpty)
            Padding(
              padding: const EdgeInsets.symmetric(vertical: 40),
              child: Center(
                child: Text(
                  _tab == 0 ? 'No live matches' : 'No upcoming matches',
                  style: bodyStyle(size: 16, color: Palette.muted),
                ),
              ),
            ),
          // Shimmer skeleton while data hasn't loaded yet
          if (allMatches.isEmpty)
            for (int i = 0; i < 3; i++) ...[
              TweenAnimationBuilder<double>(
                key: ValueKey('skeleton_$i'),
                tween: Tween(begin: 0.0, end: 1.0),
                duration: Duration(milliseconds: 300 + (i * 100)),
                curve: Curves.easeOut,
                builder: (context, value, child) => Opacity(
                  opacity: value,
                  child: child,
                ),
                child: const ArenaCardSkeleton(),
              ),
              const SizedBox(height: 28),
            ],
          const SizedBox(height: 20),
        ],
      ),
    );
  }
}

class _TabChip extends StatelessWidget {
  const _TabChip({
    required this.label,
    required this.count,
    required this.active,
    required this.onTap,
  });

  final String label;
  final int count;
  final bool active;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return Pressable(
      onTap: onTap,
      scaleTo: 0.95,
      child: AnimatedContainer(
        duration: const Duration(milliseconds: 200),
        curve: Curves.easeOut,
        padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 6),
        decoration: BoxDecoration(
          border: Border.all(
              color: active ? Palette.gold : Palette.border),
          borderRadius: BorderRadius.circular(4),
          color: active
              ? Palette.gold.withValues(alpha: 0.12)
              : Colors.transparent,
        ),
        child: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            AnimatedDefaultTextStyle(
              duration: const Duration(milliseconds: 200),
              style: displayStyle(
                size: 14,
                color: active ? Palette.gold : Palette.muted,
              ),
              child: Text(label),
            ),
            const SizedBox(width: 6),
            AnimatedContainer(
              duration: const Duration(milliseconds: 200),
              padding:
                  const EdgeInsets.symmetric(horizontal: 6, vertical: 1),
              decoration: BoxDecoration(
                color: active ? Palette.gold : Palette.muted,
                borderRadius: BorderRadius.circular(8),
              ),
              child: Text(
                '$count',
                style: bodyStyle(size: 11, color: Palette.black),
              ),
            ),
          ],
        ),
      ),
    );
  }
}
