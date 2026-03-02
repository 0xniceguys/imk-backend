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
  late final PageController _pageCtrl;
  int _tab = 0;
  bool _tabInitialized = false;

  @override
  void initState() {
    super.initState();
    _pageCtrl = PageController();
  }

  @override
  void dispose() {
    _pageCtrl.dispose();
    super.dispose();
  }

  void _goToTab(int index) {
    setState(() => _tab = index);
    _pageCtrl.animateToPage(
      index,
      duration: const Duration(milliseconds: 300),
      curve: Curves.easeOutCubic,
    );
  }

  @override
  Widget build(BuildContext context) {
    final allMatches = ref.watch(matchProvider);
    final live = allMatches.where((m) => m.status == MatchStatus.live).toList();
    final upcoming = allMatches.where((m) => m.status == MatchStatus.upcoming).toList();

    // Auto-select Upcoming if no live matches (only once)
    if (!_tabInitialized && allMatches.isNotEmpty) {
      _tabInitialized = true;
      if (live.isEmpty && _tab == 0) {
        WidgetsBinding.instance.addPostFrameCallback((_) {
          if (mounted) _goToTab(1);
        });
      }
    }

    return AppShell(
      activeTab: NavTab.arena,
      scrollable: false, // each page scrolls independently
      onNavigate: (slug) => widget.onNavigate(routeFor(slug)),
      content: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          // ── Tab chips ──────────────────────────────────────────────────
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 16),
            child: Row(
              children: [
                _TabChip(
                  label: 'LIVE',
                  count: live.length,
                  active: _tab == 0,
                  onTap: () => _goToTab(0),
                ),
                const SizedBox(width: 10),
                _TabChip(
                  label: 'UPCOMING',
                  count: upcoming.length,
                  active: _tab == 1,
                  onTap: () => _goToTab(1),
                ),
              ],
            ),
          ),
          const SizedBox(height: 16),

          // ── Swipeable page content ─────────────────────────────────────
          Expanded(
            child: PageView(
              controller: _pageCtrl,
              physics: const BouncingScrollPhysics(),
              onPageChanged: (index) => setState(() => _tab = index),
              children: [
                _MatchList(
                  matches: live,
                  allLoaded: allMatches.isNotEmpty,
                  emptyLabel: 'No live matches',
                  onTap: (m) => widget.onNavigate('/live-match/${m.id}'),
                ),
                _MatchList(
                  matches: upcoming,
                  allLoaded: allMatches.isNotEmpty,
                  emptyLabel: 'No upcoming matches',
                  onTap: (m) => widget.onNavigate('/battle-detail/${m.id}'),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

// ── Scrollable list for one page ───────────────────────────────────────────────

class _MatchList extends StatelessWidget {
  const _MatchList({
    required this.matches,
    required this.allLoaded,
    required this.emptyLabel,
    required this.onTap,
  });

  final List<Match> matches;
  final bool allLoaded;
  final String emptyLabel;
  final void Function(Match) onTap;

  @override
  Widget build(BuildContext context) {
    // Still loading
    if (!allLoaded) {
      return ListView.separated(
        padding: const EdgeInsets.fromLTRB(0, 0, 0, 20),
        itemCount: 3,
        separatorBuilder: (context, i) => const SizedBox(height: 28),
        itemBuilder: (_, i) => TweenAnimationBuilder<double>(
          key: ValueKey('skeleton_$i'),
          tween: Tween(begin: 0.0, end: 1.0),
          duration: Duration(milliseconds: 300 + i * 100),
          curve: Curves.easeOut,
          builder: (context, v, child) => Opacity(opacity: v, child: child),
          child: const ArenaCardSkeleton(),
        ),
      );
    }

    // Empty state
    if (matches.isEmpty) {
      return Center(
        child: Text(emptyLabel, style: bodyStyle(size: 16, color: Palette.muted)),
      );
    }

    return ListView.separated(
      padding: const EdgeInsets.fromLTRB(0, 0, 0, 20),
      itemCount: matches.length,
      separatorBuilder: (context, idx) => const SizedBox(height: 28),
      itemBuilder: (_, i) => TweenAnimationBuilder<double>(
        key: ValueKey(matches[i].id),
        tween: Tween(begin: 0.0, end: 1.0),
        duration: Duration(milliseconds: 300 + i * 80),
        curve: Curves.easeOut,
        builder: (context, v, child) => Opacity(
          opacity: v,
          child: Transform.translate(offset: Offset(0, 12 * (1 - v)), child: child),
        ),
        child: ArenaCard(
          match: matches[i],
          onTap: () => onTap(matches[i]),
        ),
      ),
    );
  }
}

// ── Tab chip ────────────────────────────────────────────────────────────────────

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
          border: Border.all(color: active ? Palette.gold : Palette.border),
          borderRadius: BorderRadius.circular(4),
          color: active ? Palette.gold.withValues(alpha: 0.12) : Colors.transparent,
        ),
        child: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            AnimatedDefaultTextStyle(
              duration: const Duration(milliseconds: 200),
              style: displayStyle(size: 14, color: active ? Palette.gold : Palette.muted),
              child: Text(label),
            ),
            const SizedBox(width: 6),
            AnimatedContainer(
              duration: const Duration(milliseconds: 200),
              padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 1),
              decoration: BoxDecoration(
                color: active ? Palette.gold : Palette.muted,
                borderRadius: BorderRadius.circular(8),
              ),
              child: Text('$count', style: bodyStyle(size: 11, color: Palette.black)),
            ),
          ],
        ),
      ),
    );
  }
}

