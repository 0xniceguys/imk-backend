import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../core/palette.dart';
import '../core/typography.dart';
import '../router.dart';
import '../models/match.dart';
import '../providers/clock_provider.dart';
import '../providers/match_provider.dart';
import '../widgets/shared/app_shell.dart';
import '../widgets/shared/arena_card.dart';
import '../widgets/shared/ik_shimmer.dart';

class ArenaListScreen extends ConsumerWidget {
  const ArenaListScreen({super.key, required this.onNavigate});
  final void Function(String) onNavigate;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    ref.watch(clockTickProvider);
    final matchState = ref.watch(matchProvider);
    final allMatches = matchState.matches;
    final feed = _sortedFeed(allMatches);

    return AppShell(
      activeTab: NavTab.arena,
      scrollable: false,
      onNavigate: (slug) => onNavigate(routeFor(slug)),
      content: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 16),
            child: Row(
              children: [
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        'ARENA',
                        style: displayStyle(size: 24, color: Palette.gold),
                      ),
                      // const SizedBox(height: 2),
                      // Text(
                      //   'One s2tream. Next fights in queue.',
                      //   style: bodyStyle(size: 13, color: Palette.muted),
                      // ),
                    ],
                  ),
                ),
                Text(
                  '${feed.length} MATCHES',
                  style: bodyStyle(size: 12, color: Palette.secondary),
                ),
                const SizedBox(width: 6),
                Text(
                  '•',
                  style: displayStyle(size: 14, color: Palette.statLabel),
                ),
                const SizedBox(width: 6),
                Text(
                  '${feed.where((m) => m.status == MatchStatus.live).length} LIVE',
                  style: bodyStyle(size: 12, color: Palette.gold),
                ),
              ],
            ),
          ),
          // const GoldGradientDivider(
          //   margin: EdgeInsets.fromLTRB(16, 12, 16, 12),
          // ),
          Expanded(
            child: _MatchList(
              matches: feed,
              allLoaded: matchState.hasLoaded,
              emptyLabel: 'No matches available',
              onTap: (m) {
                if (m.status == MatchStatus.live) {
                  onNavigate('/live-match/${m.id}');
                } else {
                  onNavigate('/battle-detail/${m.id}');
                }
              },
            ),
          ),
        ],
      ),
    );
  }

  static List<Match> _sortedFeed(List<Match> matches) {
    final sorted = matches
        .where(
          (m) =>
              m.status == MatchStatus.live || m.status == MatchStatus.upcoming,
        )
        .toList();
    sorted.sort((a, b) {
      final aQueue = a.queuePosition;
      final bQueue = b.queuePosition;
      if (aQueue != null && bQueue != null && aQueue != bQueue) {
        return aQueue.compareTo(bQueue);
      }
      if (aQueue != null && bQueue == null) return -1;
      if (aQueue == null && bQueue != null) return 1;

      final aRank = _statusRank(a.status);
      final bRank = _statusRank(b.status);
      if (aRank != bRank) return aRank.compareTo(bRank);
      return a.scheduledAt.compareTo(b.scheduledAt);
    });
    return sorted;
  }

  static int _statusRank(MatchStatus status) {
    return switch (status) {
      MatchStatus.live => 0,
      MatchStatus.upcoming => 1,
      MatchStatus.completed => 99,
      MatchStatus.cancelled => 100,
    };
  }
}

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
        padding: const EdgeInsets.fromLTRB(16, 0, 16, 20),
        itemCount: 3,
        separatorBuilder: (context, i) => const SizedBox(height: 18),
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
        child: Text(
          emptyLabel,
          style: bodyStyle(size: 16, color: Palette.muted),
        ),
      );
    }

    return ListView.separated(
      padding: const EdgeInsets.fromLTRB(16, 0, 16, 20),
      itemCount: matches.length,
      separatorBuilder: (context, idx) => const SizedBox(height: 18),
      itemBuilder: (_, i) => TweenAnimationBuilder<double>(
        key: ValueKey(matches[i].id),
        tween: Tween(begin: 0.0, end: 1.0),
        duration: Duration(milliseconds: 300 + i * 80),
        curve: Curves.easeOut,
        builder: (context, v, child) => Opacity(
          opacity: v,
          child: Transform.translate(
            offset: Offset(0, 12 * (1 - v)),
            child: child,
          ),
        ),
        child: ArenaCard(match: matches[i], onTap: () => onTap(matches[i])),
      ),
    );
  }
}
