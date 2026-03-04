import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../core/palette.dart';
import '../core/typography.dart';
import '../core/constants.dart';
import '../providers/fighter_provider.dart';
import '../providers/fighter_stats_provider.dart';
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

    // Watch stats and match history from API
    final statsAsync = ref.watch(fighterStatsProvider(fighter.id));
    final matchesAsync = ref.watch(fighterMatchesProvider(fighter.id));

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
                // Tags: fight style + origin
                if (fighter.fightStyle != null || fighter.origin != null)
                  Padding(
                    padding: const EdgeInsets.only(top: 6),
                    child: Row(
                      mainAxisAlignment: MainAxisAlignment.center,
                      children: [
                        if (fighter.fightStyle != null)
                          _Tag(label: fighter.fightStyle!),
                        if (fighter.fightStyle != null &&
                            fighter.origin != null)
                          const SizedBox(width: 6),
                        if (fighter.origin != null)
                          _Tag(label: '📍 ${fighter.origin!}'),
                      ],
                    ),
                  ),
                const SizedBox(height: 20),

                // ── Section 1: Overall Stats (from fighter model — already on API) ──
                _DetailSection(
                  title: 'Overall Stats',
                  child: StatsColumnsWidget(
                      fighter: fighter, fontSize: 14, gap: 16),
                ),
                const SizedBox(height: 24),

                // ── Section 2: Model Stats (computed from /stats endpoint) ──
                _DetailSection(
                  title: 'Model Stats',
                  child: statsAsync.when(
                    loading: () => const _LoadingRow(),
                    error: (_, __) => _statRow('Stats unavailable', '—'),
                    data: (stats) {
                      if (stats == null) {
                        return _statRow('No data', '—');
                      }
                      final p1Rate =
                          ((stats['p1_win_rate'] as num? ?? 0) * 100)
                              .toStringAsFixed(1);
                      final p2Rate =
                          ((stats['p2_win_rate'] as num? ?? 0) * 100)
                              .toStringAsFixed(1);
                      final totalVol =
                          (stats['total_bet_volume'] as num? ?? 0)
                              .toStringAsFixed(2);
                      final fightingSince =
                          _formatDate(stats['fighting_since'] as String?);
                      final lastMatch =
                          _formatDate(stats['last_match_date'] as String?);
                      return Column(
                        children: [
                          _TwoColStats(items: [
                            _StatItem(
                                label: 'P1 (Left) Win Rate',
                                value: '$p1Rate%'),
                            _StatItem(
                                label: 'P2 (Right) Win Rate',
                                value: '$p2Rate%'),
                            _StatItem(
                                label: 'Flawless Rounds',
                                value:
                                    '${stats['flawless_matches'] ?? 0}'),
                            _StatItem(
                                label: 'Total Bets Won',
                                value:
                                    '${stats['total_bets_won'] ?? 0}'),
                            _StatItem(
                                label: 'Bet Volume (SOL)',
                                value: totalVol),
                            _StatItem(
                                label: 'Fighting Since',
                                value: fightingSince),
                            _StatItem(
                                label: 'Last Match',
                                value: lastMatch),
                          ]),
                        ],
                      );
                    },
                  ),
                ),
                const SizedBox(height: 24),

                // ── Section 3: Fight Style / Bio ──
                _DetailSection(
                  title: 'Fight Style',
                  child: Column(
                    children: [
                      if (fighter.fightStyle != null)
                        Padding(
                          padding: const EdgeInsets.only(bottom: 8),
                          child: Text(fighter.fightStyle!,
                              style: displayStyle(
                                  size: 18, color: Palette.gold)),
                        ),
                      Text(
                        fighter.description ?? 'No description available.',
                        style:
                            bodyStyle(size: 13, color: Palette.secondary),
                        textAlign: TextAlign.center,
                      ),
                      if (fighter.specialMove != null) ...[
                        const SizedBox(height: 10),
                        Row(
                          mainAxisAlignment: MainAxisAlignment.center,
                          children: [
                            const Icon(Icons.flash_on,
                                color: Palette.gold, size: 16),
                            const SizedBox(width: 4),
                            Flexible(
                              child: Text(fighter.specialMove!,
                                  style: bodyStyle(
                                      size: 13, color: Palette.white),
                                  textAlign: TextAlign.center),
                            ),
                          ],
                        ),
                      ],
                    ],
                  ),
                ),
                const SizedBox(height: 24),

                // ── Section 4: Match History (from /matches endpoint) ──
                _DetailSection(
                  title: 'Match History',
                  child: matchesAsync.when(
                    loading: () => const _LoadingRow(),
                    error: (_, __) =>
                        _statRow('History unavailable', '—'),
                    data: (matches) {
                      if (matches.isEmpty) {
                        return Text('No completed matches yet.',
                            style: bodyStyle(
                                size: 13, color: Palette.secondary));
                      }
                      return Column(
                        children: matches
                            .take(8)
                            .map((m) => _MatchHistoryRow(match: m))
                            .toList(),
                      );
                    },
                  ),
                ),
                const SizedBox(height: 32),

                // Prev/Next navigation
                Padding(
                  padding: const EdgeInsets.symmetric(horizontal: 24),
                  child: Container(
                    padding: const EdgeInsets.only(top: 18, bottom: 24),
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

  static String _formatDate(String? iso) {
    if (iso == null) return '—';
    try {
      final dt = DateTime.parse(iso).toLocal();
      return '${dt.year}-${dt.month.toString().padLeft(2, '0')}-${dt.day.toString().padLeft(2, '0')}';
    } catch (_) {
      return '—';
    }
  }

  static Widget _statRow(String label, String value) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 3),
      child: Row(
        mainAxisAlignment: MainAxisAlignment.spaceBetween,
        children: [
          Text(label,
              style: bodyStyle(size: 13, color: Palette.statLabel)),
          Text(value, style: bodyStyle(size: 13, color: Palette.white)),
        ],
      ),
    );
  }
}

// ── Data classes ──

class _StatItem {
  const _StatItem({required this.label, required this.value});
  final String label;
  final String value;
}

// ── Sub-widgets ──

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
          Text(title,
              style: displayStyle(size: 22, color: Palette.statLabel)),
          const SizedBox(height: 12),
          child,
        ],
      ),
    );
  }
}

class _TwoColStats extends StatelessWidget {
  const _TwoColStats({required this.items});
  final List<_StatItem> items;

  @override
  Widget build(BuildContext context) {
    return Column(
      children: items
          .map((item) => Padding(
                padding: const EdgeInsets.symmetric(vertical: 5),
                child: Row(
                  mainAxisAlignment: MainAxisAlignment.spaceBetween,
                  children: [
                    Text(item.label,
                        style:
                            bodyStyle(size: 13, color: Palette.statLabel)),
                    Text(item.value,
                        style:
                            bodyStyle(size: 13, color: Palette.white)),
                  ],
                ),
              ))
          .toList(),
    );
  }
}

class _MatchHistoryRow extends StatelessWidget {
  const _MatchHistoryRow({required this.match});
  final Map<String, dynamic> match;

  @override
  Widget build(BuildContext context) {
    final isWin = match['result'] == 'WIN';
    final opponent = match['opponent_name'] as String? ?? 'Unknown';
    final side = match['side'] as String? ?? '';
    final rWon = match['rounds_won'] as int? ?? 0;
    final rLost = match['rounds_lost'] as int? ?? 0;
    final date = _fmtDate(match['completed_at'] as String?);

    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 4),
      child: Row(
        children: [
          Container(
            width: 42,
            padding:
                const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
            decoration: BoxDecoration(
              borderRadius: BorderRadius.circular(4),
              color: isWin
                  ? Palette.green.withOpacity(0.15)
                  : Palette.red.withOpacity(0.15),
              border: Border.all(
                  color: isWin ? Palette.green : Palette.red,
                  width: 0.8),
            ),
            child: Text(
              isWin ? 'WIN' : 'LOSS',
              style: bodyStyle(
                  size: 10,
                  color: isWin ? Palette.green : Palette.red),
              textAlign: TextAlign.center,
            ),
          ),
          const SizedBox(width: 8),
          Expanded(
            child: Text('vs $opponent',
                style: bodyStyle(size: 13, color: Palette.white),
                overflow: TextOverflow.ellipsis),
          ),
          Text('$rWon-$rLost  $side  $date',
              style: bodyStyle(size: 11, color: Palette.statLabel)),
        ],
      ),
    );
  }

  static String _fmtDate(String? iso) {
    if (iso == null) return '';
    try {
      final dt = DateTime.parse(iso).toLocal();
      return '${dt.month}/${dt.day}';
    } catch (_) {
      return '';
    }
  }
}

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

class _LoadingRow extends StatelessWidget {
  const _LoadingRow();

  @override
  Widget build(BuildContext context) {
    return const Padding(
      padding: EdgeInsets.symmetric(vertical: 12),
      child: Center(
        child: SizedBox(
          width: 20,
          height: 20,
          child: CircularProgressIndicator(strokeWidth: 2),
        ),
      ),
    );
  }
}
