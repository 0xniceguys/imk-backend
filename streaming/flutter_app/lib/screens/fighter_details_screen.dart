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

class FighterDetailsScreen extends ConsumerStatefulWidget {
  const FighterDetailsScreen({
    super.key,
    required this.onNavigate,
    this.fighterId,
  });
  final void Function(String) onNavigate;
  final String? fighterId;

  @override
  ConsumerState<FighterDetailsScreen> createState() =>
      _FighterDetailsScreenState();
}

class _FighterDetailsScreenState extends ConsumerState<FighterDetailsScreen> {
  String? _vsOpponentId;

  @override
  Widget build(BuildContext context) {
    final fighters = ref.watch(fighterProvider);
    if (fighters.isEmpty) {
      return const Center(child: CircularProgressIndicator());
    }
    final idx = widget.fighterId != null
        ? fighters.indexWhere((f) => f.id == widget.fighterId)
        : 0;
    final safeIdx = idx >= 0 ? idx : 0;
    final fighter = fighters[safeIdx];
    final prevIdx = safeIdx > 0 ? safeIdx - 1 : fighters.length - 1;
    final nextIdx = safeIdx < fighters.length - 1 ? safeIdx + 1 : 0;
    final top = MediaQuery.of(context).padding.top;
    final bottom = MediaQuery.of(context).padding.bottom;

    // API-driven providers
    final statsAsync = ref.watch(fighterStatsProvider(fighter.id));
    final matchesAsync = ref.watch(fighterMatchesProvider(fighter.id));
    final vsAsync = _vsOpponentId != null
        ? ref.watch(fighterVsProvider(
            FighterVsParams(fighter.id, _vsOpponentId!)))
        : null;

    final resolvedUrl = fighter.resolvedImageUrl(kStreamBaseUrl);

    // Default VS opponent: first other fighter
    if (_vsOpponentId == null && fighters.length > 1) {
      final other = fighters.firstWhere((f) => f.id != fighter.id,
          orElse: () => fighters[0]);
      WidgetsBinding.instance.addPostFrameCallback((_) {
        if (mounted) setState(() => _vsOpponentId = other.id);
      });
    }

    return Column(
      children: [
        SizedBox(height: top + 8),
        // Back button
        Align(
          alignment: Alignment.centerLeft,
          child: Padding(
            padding: const EdgeInsets.only(left: 26),
            child: Pressable(
              onTap: () {
                if (Navigator.of(context).canPop()) {
                  Navigator.of(context).pop();
                } else {
                  widget.onNavigate('/fighter-overview');
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
                // ── Fighter hero ──
                ConstrainedBox(
                  constraints: const BoxConstraints(maxHeight: 280),
                  child: resolvedUrl != null
                      ? Image.network(resolvedUrl,
                          width: 140,
                          fit: BoxFit.contain,
                          errorBuilder: (_, __, ___) => Image.asset(
                              Assets.detailsHero,
                              width: 140,
                              fit: BoxFit.contain))
                      : Image.asset(Assets.detailsHero,
                          width: 140, fit: BoxFit.contain),
                ),
                Text(fighter.name,
                    style: displayStyle(size: 40, color: Palette.gold)),
                Text(fighter.llmModel,
                    style: bodyStyle(size: 16, color: Palette.secondary)),
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

                // ── Section 1: Overall Stats ──
                _Section(
                  title: 'Overall Stats',
                  child: StatsColumnsWidget(
                      fighter: fighter, fontSize: 14, gap: 16),
                ),
                const SizedBox(height: 24),

                // ── Section 2: Detailed Stats (10 items from API) ──
                _Section(
                  title: 'Fighter Stats',
                  child: statsAsync.when(
                    loading: () => const _Loader(),
                    error: (_, __) =>
                        _emptyMsg('Stats unavailable'),
                    data: (stats) {
                      if (stats == null) return _emptyMsg('No data yet');
                      final wr = ((stats['win_rate'] as num? ?? 0) * 100)
                          .toStringAsFixed(1);
                      final p1r = ((stats['p1_win_rate'] as num? ?? 0) * 100)
                          .toStringAsFixed(1);
                      final p2r = ((stats['p2_win_rate'] as num? ?? 0) * 100)
                          .toStringAsFixed(1);
                      final vol =
                          (stats['total_bet_volume'] as num? ?? 0)
                              .toStringAsFixed(2);
                      final since =
                          _fmtDate(stats['fighting_since'] as String?);
                      final last =
                          _fmtDateTime(stats['last_match_date'] as String?);
                      return _StatList(items: [
                        _SI('Win Rate', '$wr%'),
                        _SI('Matches Played',
                            '${stats['matches_played'] ?? fighter.matchesPlayed}'),
                        _SI('Matches Won',
                            '${stats['matches_won'] ?? fighter.matchesWon}'),
                        _SI('Total Bet Volume (SOL)', vol),
                        _SI('Total Bets Won',
                            '${stats['total_bets_won'] ?? 0}'),
                        _SI('Fighting Since', since),
                        _SI('Last Match', last),
                        _SI('Flawless Matches',
                            '${stats['flawless_matches'] ?? 0}'),
                        _SI('Left Side (P1) Win Rate', '$p1r%'),
                        _SI('Right Side (P2) Win Rate', '$p2r%'),
                      ]),
                    },
                  ),
                ),
                const SizedBox(height: 24),

                // ── Section 3: Match History ──
                _Section(
                  title: 'Match History',
                  child: matchesAsync.when(
                    loading: () => const _Loader(),
                    error: (_, __) => _emptyMsg('History unavailable'),
                    data: (matches) {
                      if (matches.isEmpty) {
                        return _emptyMsg('No completed matches yet');
                      }
                      return Column(
                        children: matches
                            .take(10)
                            .map((m) => _MatchRow(match: m))
                            .toList(),
                      );
                    },
                  ),
                ),
                const SizedBox(height: 24),

                // ── Section 4: VS Stats ──
                _Section(
                  title: 'Head-to-Head Stats',
                  child: Column(
                    children: [
                      // Opponent picker
                      if (fighters.length > 1)
                        Padding(
                          padding: const EdgeInsets.only(bottom: 12),
                          child: DropdownButton<String>(
                            value: _vsOpponentId,
                            dropdownColor: Palette.cardBg,
                            style: bodyStyle(size: 13, color: Palette.white),
                            isExpanded: true,
                            onChanged: (v) =>
                                setState(() => _vsOpponentId = v),
                            items: fighters
                                .where((f) => f.id != fighter.id)
                                .map((f) => DropdownMenuItem(
                                      value: f.id,
                                      child: Text(f.name),
                                    ))
                                .toList(),
                          ),
                        ),
                      if (vsAsync != null)
                        vsAsync.when(
                          loading: () => const _Loader(),
                          error: (_, __) =>
                              _emptyMsg('VS stats unavailable'),
                          data: (vs) {
                            if (vs == null) {
                              return _emptyMsg('No matches vs this opponent');
                            }
                            final opName =
                                vs['opponent_name'] as String? ?? '—';
                            final wr =
                                ((vs['win_rate'] as num? ?? 0) * 100)
                                    .toStringAsFixed(1);
                            final p1r =
                                ((vs['p1_win_rate'] as num? ?? 0) * 100)
                                    .toStringAsFixed(1);
                            final p2r =
                                ((vs['p2_win_rate'] as num? ?? 0) * 100)
                                    .toStringAsFixed(1);
                            final vol =
                                (vs['total_bet_volume'] as num? ?? 0)
                                    .toStringAsFixed(2);
                            return _StatList(items: [
                              _SI('Opponent', opName),
                              _SI('Win Rate vs', '$wr%'),
                              _SI('Matches Played vs',
                                  '${vs['matches_played'] ?? 0}'),
                              _SI('Matches Won vs',
                                  '${vs['matches_won'] ?? 0}'),
                              _SI('Bet Volume vs (SOL)', vol),
                              _SI('Bets Won vs',
                                  '${vs['total_bets_won'] ?? 0}'),
                              _SI('Flawless vs',
                                  '${vs['flawless_matches'] ?? 0}'),
                              _SI('Left Side Win Rate vs', '$p1r%'),
                              _SI('Right Side Win Rate vs', '$p2r%'),
                            ]);
                          },
                        )
                      else
                        _emptyMsg('Select an opponent above'),
                    ],
                  ),
                ),
                const SizedBox(height: 24),

                // ── Section 5: Training / Agent Info ──
                _Section(
                  title: 'Training Stats',
                  child: _StatList(items: [
                    _SI('AI Model', fighter.llmModel),
                    _SI('Architecture',
                        fighter.agentArchitecture ?? 'Unknown'),
                    _SI('Character', fighter.character),
                    _SI('Fight Style', fighter.fightStyle ?? '—'),
                    if (fighter.rank != null)
                      _SI('Global Rank', '#${fighter.rank}'),
                    _SI('Special Move', fighter.specialMove ?? '—'),
                    if (fighter.origin != null)
                      _SI('Origin', fighter.origin!),
                  ]),
                ),
                const SizedBox(height: 32),

                // ── Prev/Next navigation ──
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
                          onTap: () => widget.onNavigate(
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
                          onTap: () => widget.onNavigate(
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
          onTapArena: () => widget.onNavigate('/arena-list'),
          onTapFighters: () => widget.onNavigate('/fighter-overview'),
          onTapProfile: () => widget.onNavigate('/profile'),
        ),
        SizedBox(height: bottom > 0 ? bottom : 12),
      ],
    );
  }

  static String _fmtDate(String? iso) {
    if (iso == null) return '—';
    try {
      final dt = DateTime.parse(iso).toLocal();
      return '${dt.year}-${_p(dt.month)}-${_p(dt.day)}';
    } catch (_) {
      return '—';
    }
  }

  static String _fmtDateTime(String? iso) {
    if (iso == null) return '—';
    try {
      final dt = DateTime.parse(iso).toLocal();
      return '${dt.year}-${_p(dt.month)}-${_p(dt.day)} ${_p(dt.hour)}:${_p(dt.minute)}';
    } catch (_) {
      return '—';
    }
  }

  static String _p(int n) => n.toString().padLeft(2, '0');

  static Widget _emptyMsg(String msg) => Padding(
        padding: const EdgeInsets.symmetric(vertical: 8),
        child: Text(msg,
            style: bodyStyle(size: 13, color: Palette.secondary),
            textAlign: TextAlign.center),
      );
}

// ── Data helpers ──

class _SI {
  const _SI(this.label, this.value);
  final String label;
  final String value;
}

// FighterVsParams for the family provider
class FighterVsParams {
  const FighterVsParams(this.fighterId, this.opponentId);
  final String fighterId;
  final String opponentId;

  @override
  bool operator ==(Object other) =>
      other is FighterVsParams &&
      fighterId == other.fighterId &&
      opponentId == other.opponentId;

  @override
  int get hashCode => Object.hash(fighterId, opponentId);
}

// ── Widgets ──

class _Section extends StatelessWidget {
  const _Section({required this.title, required this.child});
  final String title;
  final Widget child;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 24),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Container(height: 1, color: Palette.darkGold),
          const SizedBox(height: 16),
          Text(title,
              style: displayStyle(size: 22, color: Palette.statLabel),
              textAlign: TextAlign.center),
          const SizedBox(height: 12),
          child,
        ],
      ),
    );
  }
}

class _StatList extends StatelessWidget {
  const _StatList({required this.items});
  final List<_SI> items;

  @override
  Widget build(BuildContext context) {
    return Column(
      children: items
          .map((item) => Padding(
                padding: const EdgeInsets.symmetric(vertical: 5),
                child: Row(
                  mainAxisAlignment: MainAxisAlignment.spaceBetween,
                  children: [
                    Flexible(
                      child: Text(item.label,
                          style: bodyStyle(
                              size: 13, color: Palette.statLabel)),
                    ),
                    const SizedBox(width: 8),
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

class _MatchRow extends StatelessWidget {
  const _MatchRow({required this.match});
  final Map<String, dynamic> match;

  @override
  Widget build(BuildContext context) {
    final isWin = match['result'] == 'WIN';
    final opponent = match['opponent_name'] as String? ?? 'Unknown';
    final side = match['side'] as String? ?? '';
    final rWon = match['rounds_won'] as int? ?? 0;
    final rLost = match['rounds_lost'] as int? ?? 0;
    final date = _fmt(match['completed_at'] as String?);

    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 4),
      child: Row(
        children: [
          Container(
            width: 44,
            padding:
                const EdgeInsets.symmetric(horizontal: 4, vertical: 2),
            decoration: BoxDecoration(
              borderRadius: BorderRadius.circular(4),
              color: isWin
                  ? Palette.green.withOpacity(0.12)
                  : Palette.red.withOpacity(0.12),
              border: Border.all(
                  color: isWin ? Palette.green : Palette.red,
                  width: 0.8),
            ),
            child: Text(isWin ? 'WIN' : 'LOSS',
                style: bodyStyle(
                    size: 10,
                    color: isWin ? Palette.green : Palette.red),
                textAlign: TextAlign.center),
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

  static String _fmt(String? iso) {
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

class _Loader extends StatelessWidget {
  const _Loader();

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
