import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../core/palette.dart';
import '../core/typography.dart';
import '../models/fighter.dart';
import '../models/wallet_state.dart';
import '../providers/fighter_provider.dart';
import '../providers/fighter_stats_provider.dart';
import '../providers/wallet_provider.dart';
import '../utils/skr_pricing.dart';
import '../widgets/fighter/match_history.dart';
import '../widgets/shared/pressable.dart';
import '../widgets/shared/ik_loader.dart';
import '../widgets/fighter/fighter_image.dart';

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
  MatchHistoryResultFilter _matchResultFilter = MatchHistoryResultFilter.all;
  String? _matchHistoryOpponentId;

  @override
  Widget build(BuildContext context) {
    final fighters = ref.watch(fighterProvider);
    if (fighters.isEmpty) {
      return const Center(child: IKLoader(size: 40));
    }

    final initialIndex = widget.fighterId != null
        ? fighters.indexWhere((f) => f.id == widget.fighterId)
        : 0;
    final currentIndex = initialIndex >= 0 ? initialIndex : 0;
    final fighter = fighters[currentIndex];
    final bottom = MediaQuery.of(context).padding.bottom;
    final wallet = ref.watch(walletProvider);

    final statsAsync = ref.watch(fighterStatsProvider(fighter.id));
    final matchesAsync = ref.watch(fighterMatchesProvider(fighter.id));
    final vsAsync = _vsOpponentId != null
        ? ref.watch(
            fighterVsProvider(FighterVsParams(fighter.id, _vsOpponentId!)),
          )
        : null;

    if (_vsOpponentId == null && fighters.length > 1) {
      final other = fighters.firstWhere(
        (item) => item.id != fighter.id,
        orElse: () => fighters[0],
      );
      WidgetsBinding.instance.addPostFrameCallback((_) {
        if (mounted) {
          setState(() => _vsOpponentId = other.id);
        }
      });
    }

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
                    widget.onNavigate('/fighter-overview');
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
                      style: displayStyle(size: 18, color: Palette.muted),
                    ),
                  ],
                ),
              ),
            ),
            const SizedBox(height: 14),
            SizedBox(
              height: 320,
              child: Stack(
                alignment: Alignment.bottomCenter,
                children: [
                  Align(
                    alignment: Alignment.bottomCenter,
                    child: SizedBox(
                      height: 260,
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
                            size: 40,
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
                          style: bodyStyle(size: 16, color: Palette.secondary),
                        ),
                      ],
                    ),
                  ),
                ],
              ),
            ),
            const SizedBox(height: 24),
            const _GoldDivider(),
            const SizedBox(height: 24),
            _DetailsSection(
              title: 'Overall Stats',
              child: statsAsync.when(
                loading: () => const _Loader(),
                error: (error, stackTrace) => _emptyMsg('Stats unavailable'),
                data: (stats) => _StatsGrid(
                  items: [
                    _InfoItem(
                      'Win Rate',
                      stats != null
                          ? '${(((stats['win_rate'] as num?) ?? fighter.winRate) * 100).toStringAsFixed(1)}%'
                          : '${(fighter.winRate * 100).toStringAsFixed(0)}%',
                    ),
                    _InfoItem(
                      'Rank',
                      fighter.rank > 0 ? '#${fighter.rank}' : 'Unranked',
                    ),
                    _InfoItem(
                      'Matches Played',
                      '${stats?['matches_played'] ?? fighter.matchesPlayed}',
                    ),
                    _InfoItem(
                      'Matches Won',
                      '${stats?['matches_won'] ?? fighter.matchesWon}',
                    ),
                    _InfoItem(
                      'Losses',
                      '${stats != null ? ((stats['matches_played'] ?? fighter.matchesPlayed) - (stats['matches_won'] ?? fighter.matchesWon)) : fighter.matchesLost}',
                    ),
                    _InfoItem('Bet Volume', _formatBetVolume(stats, wallet)),
                  ],
                ),
              ),
            ),
            const SizedBox(height: 30),
            const _GoldDivider(),
            const SizedBox(height: 30),
            _DetailsSection(
              title: 'Head-to-Head Stats',
              child: matchesAsync.when(
                loading: () => const _Loader(),
                error: (error, stackTrace) =>
                    _emptyMsg('Head-to-head stats unavailable'),
                data: (matches) {
                  final rows = _buildHeadToHeadRows(
                    fighterId: fighter.id,
                    fighters: fighters,
                    matches: matches,
                  );
                  if (rows.isEmpty) {
                    return _emptyMsg('No opponent data available');
                  }

                  final selectedOpponentId =
                      rows.any((row) => row.opponentId == _vsOpponentId)
                      ? _vsOpponentId
                      : rows.first.opponentId;
                  if (selectedOpponentId != _vsOpponentId) {
                    WidgetsBinding.instance.addPostFrameCallback((_) {
                      if (!mounted) return;
                      setState(() => _vsOpponentId = selectedOpponentId);
                    });
                  }

                  return Column(
                    children: [
                      const _HeadToHeadLegend(),
                      const SizedBox(height: 12),
                      ...rows
                          .take(8)
                          .map(
                            (row) => Padding(
                              padding: const EdgeInsets.only(bottom: 10),
                              child: _HeadToHeadHeatRow(
                                row: row,
                                selected: row.opponentId == _vsOpponentId,
                                onTap: () =>
                                    setState(() => _vsOpponentId = row.opponentId),
                              ),
                            ),
                          ),
                      if (rows.length > 8)
                        Padding(
                          padding: const EdgeInsets.only(bottom: 8),
                          child: Text(
                            '+${rows.length - 8} more opponents',
                            style: bodyStyle(size: 12, color: Palette.secondary),
                          ),
                        ),
                      const SizedBox(height: 10),
                      if (vsAsync != null)
                        vsAsync.when(
                          loading: () => const _Loader(),
                          error: (error, stackTrace) =>
                              _emptyMsg('VS stats unavailable'),
                          data: (vs) {
                            if (vs == null) {
                              return _emptyMsg('No matches vs this opponent');
                            }
                            final opponentName = rows
                                .where(
                                  (row) => row.opponentId == _vsOpponentId,
                                )
                                .map((row) => row.opponentName)
                                .cast<String?>()
                                .firstWhere(
                                  (name) =>
                                      name != null && name.trim().isNotEmpty,
                                  orElse: () => null,
                                ) ??
                                (vs['opponent_name'] as String?) ??
                                'Unknown';

                            final played = _asInt(
                              vs['total_matches'] ?? vs['matches_played'],
                            );
                            final wins = _asInt(
                              vs['wins'] ?? vs['matches_won'],
                            );
                            final lossesRaw = vs['losses'];
                            final losses = lossesRaw == null
                                ? (played - wins).clamp(0, played)
                                : _asInt(lossesRaw);
                            final winRate = _asDouble(vs['win_rate']);

                            final recentMatches = vs['matches'] is List
                                ? (vs['matches'] as List)
                                : const [];
                            final latestResult = recentMatches.isNotEmpty
                                ? ((recentMatches.first
                                              as Map<String, dynamic>)['result']
                                          as String? ??
                                      'null')
                                : 'null';

                            return _StatsGrid(
                              items: [
                                _InfoItem('Opponent', opponentName),
                                _InfoItem(
                                  'Win Rate',
                                  '${(winRate * 100).toStringAsFixed(1)}%',
                                ),
                                _InfoItem('Played', '$played'),
                                _InfoItem('Wins', '$wins'),
                                _InfoItem('Losses', '$losses'),
                                _InfoItem('Latest Result', latestResult),
                              ],
                            );
                          },
                        )
                      else
                        _emptyMsg('Select an opponent above'),
                    ],
                  );
                },
              ),
            ),
            const SizedBox(height: 30),
            const _GoldDivider(),
            const SizedBox(height: 30),
            _DetailsSection(
              title: 'Match History',
              child: matchesAsync.when(
                loading: () => const _Loader(),
                error: (error, stackTrace) => _emptyMsg('History unavailable'),
                data: (matches) {
                  if (matches.isEmpty) {
                    return _emptyMsg('No completed matches yet');
                  }
                  final opponentOptions = buildMatchHistoryOpponentOptions(
                    matches,
                  );
                  final filteredMatches = filterMatchHistory(
                    matches,
                    resultFilter: _matchResultFilter,
                    opponentId: _matchHistoryOpponentId,
                  );
                  final visibleMatches = filteredMatches.take(3).toList();

                  return Column(
                    children: [
                      MatchHistoryFilters(
                        resultFilter: _matchResultFilter,
                        opponentId: _matchHistoryOpponentId,
                        opponentOptions: opponentOptions,
                        onResultChanged: (value) =>
                            setState(() => _matchResultFilter = value),
                        onOpponentChanged: (value) =>
                            setState(() => _matchHistoryOpponentId = value),
                      ),
                      const SizedBox(height: 18),
                      if (filteredMatches.isEmpty)
                        _emptyMsg('No matches found for the selected filters')
                      else ...[
                        Column(
                          children: visibleMatches
                              .map(
                                (match) => Padding(
                                  padding: const EdgeInsets.only(bottom: 10),
                                  child: MatchHistoryCard(
                                    match: match,
                                    fighterName: fighter.name,
                                    tokenSymbol: wallet.seekerSymbol,
                                  ),
                                ),
                              )
                              .toList(),
                        ),
                        if (filteredMatches.length > 3)
                          Padding(
                            padding: const EdgeInsets.only(top: 6),
                            child: Pressable(
                              onTap: () => widget.onNavigate(
                                '/fighter-match-history/${fighter.id}',
                              ),
                              child: Container(
                                padding: const EdgeInsets.symmetric(
                                  horizontal: 18,
                                  vertical: 12,
                                ),
                                decoration: BoxDecoration(
                                  borderRadius: BorderRadius.circular(2),
                                  color: Palette.cardBg.withValues(alpha: 0.32),
                                  border: Border.all(
                                    color: Palette.gold.withValues(alpha: 0.4),
                                  ),
                                ),
                                child: Text(
                                  'Show More',
                                  style: bodyStyle(
                                    size: 14,
                                    color: Palette.gold,
                                  ),
                                ),
                              ),
                            ),
                          ),
                      ],
                    ],
                  );
                },
              ),
            ),
            const SizedBox(height: 30),
            const _GoldDivider(),
            const SizedBox(height: 30),
            _DetailsSection(
              title: 'Fighter Profile',
              child: Column(
                children: [
                  if (fighter.fightStyle.isNotEmpty ||
                      fighter.origin.isNotEmpty)
                    Padding(
                      padding: const EdgeInsets.only(bottom: 14),
                      child: Wrap(
                        alignment: WrapAlignment.center,
                        spacing: 8,
                        runSpacing: 8,
                        children: [
                          if (fighter.fightStyle.isNotEmpty)
                            _Tag(label: fighter.fightStyle),
                          if (fighter.origin.isNotEmpty)
                            _Tag(label: fighter.origin),
                        ],
                      ),
                    ),
                  _StatsGrid(
                    items: [
                      _InfoItem('Character', fighter.character),
                      _InfoItem(
                        'Architecture',
                        fighter.agentArchitecture?.toUpperCase() ?? 'Unknown',
                      ),
                      _InfoItem(
                        'Origin',
                        fighter.origin.isEmpty ? 'Unknown' : fighter.origin,
                      ),
                      _InfoItem(
                        'Slug',
                        fighter.slug.isEmpty ? 'Unknown' : fighter.slug,
                      ),
                    ],
                  ),
                ],
              ),
            ),
            const SizedBox(height: 30),
            const _GoldDivider(),
            const SizedBox(height: 30),
            _DetailsSection(
              title: 'Special Move',
              child: Text(
                fighter.specialMove.isEmpty
                    ? 'No special move listed.'
                    : fighter.specialMove,
                textAlign: TextAlign.center,
                style: bodyStyle(size: 15, color: Palette.white, height: 1.35),
              ),
            ),
            const SizedBox(height: 30),
            _DetailsSection(
              title: 'Lore',
              child: Text(
                fighter.description.isEmpty
                    ? 'No description available.'
                    : fighter.description,
                textAlign: TextAlign.center,
                style: bodyStyle(
                  size: 15,
                  color: Palette.secondary,
                  height: 1.4,
                ),
              ),
            ),
            const SizedBox(height: 34),
            const _GoldDivider(),
            const SizedBox(height: 18),
            Text(
              'Switch Fighter',
              style: displayStyle(size: 18, color: Palette.statLabel),
            ),
            const SizedBox(height: 10),
            Container(
              padding: const EdgeInsets.symmetric(horizontal: 14),
              decoration: BoxDecoration(
                borderRadius: BorderRadius.circular(2),
                border: Border.all(color: Palette.gold.withValues(alpha: 0.55)),
                gradient: const LinearGradient(
                  begin: Alignment.topCenter,
                  end: Alignment.bottomCenter,
                  colors: [Color(0x22000000), Color(0x44000000)],
                ),
              ),
              child: DropdownButtonHideUnderline(
                child: DropdownButton<String>(
                  value: fighter.id,
                  isExpanded: true,
                  dropdownColor: Palette.cardBg,
                  iconEnabledColor: Palette.gold,
                  style: bodyStyle(size: 15, color: Palette.white),
                  onChanged: (value) {
                    if (value == null || value == fighter.id) return;
                    widget.onNavigate('/fighter-details/$value');
                  },
                  items: fighters
                      .map(
                        (item) => DropdownMenuItem<String>(
                          value: item.id,
                          child: Text(
                            item.name,
                            overflow: TextOverflow.ellipsis,
                          ),
                        ),
                      )
                      .toList(),
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }

  static Widget _emptyMsg(String msg) => Padding(
    padding: const EdgeInsets.symmetric(vertical: 8),
    child: Text(
      msg,
      style: bodyStyle(size: 13, color: Palette.secondary),
      textAlign: TextAlign.center,
    ),
  );
}

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

class _FighterDetailArtwork extends StatelessWidget {
  const _FighterDetailArtwork({required this.fighter});

  final Fighter fighter;

  @override
  Widget build(BuildContext context) {
    return FighterImage(fighter: fighter, fit: BoxFit.contain);
  }
}

class _DetailsSection extends StatelessWidget {
  const _DetailsSection({required this.title, required this.child});

  final String title;
  final Widget child;

  @override
  Widget build(BuildContext context) {
    return Column(
      children: [
        Text(
          title,
          textAlign: TextAlign.center,
          style: displayStyle(size: 22, color: Palette.statLabel),
        ),
        const SizedBox(height: 14),
        child,
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
      spacing: 12,
      runSpacing: 12,
      alignment: WrapAlignment.center,
      children: items
          .map(
            (item) => SizedBox(
              width: 145,
              child: Container(
                padding: const EdgeInsets.symmetric(
                  horizontal: 10,
                  vertical: 11,
                ),
                decoration: BoxDecoration(
                  borderRadius: BorderRadius.circular(2),
                  border: Border.all(
                    color: Palette.border.withValues(alpha: 0.9),
                  ),
                  gradient: const LinearGradient(
                    begin: Alignment.topCenter,
                    end: Alignment.bottomCenter,
                    colors: [Color(0x22000000), Color(0x44000000)],
                  ),
                ),
                child: Column(
                  children: [
                    Text(
                      item.label,
                      textAlign: TextAlign.center,
                      style: bodyStyle(size: 13, color: Palette.statLabel),
                    ),
                    const SizedBox(height: 6),
                    Text(
                      item.value,
                      textAlign: TextAlign.center,
                      maxLines: 2,
                      overflow: TextOverflow.ellipsis,
                      style: bodyStyle(size: 16, color: Palette.white),
                    ),
                  ],
                ),
              ),
            ),
          )
          .toList(),
    );
  }
}

class _HeadToHeadLegend extends StatelessWidget {
  const _HeadToHeadLegend();

  @override
  Widget build(BuildContext context) {
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 8),
      decoration: BoxDecoration(
        borderRadius: BorderRadius.circular(2),
        color: Palette.cardBg.withValues(alpha: 0.2),
      ),
      child: const Row(
        mainAxisAlignment: MainAxisAlignment.spaceBetween,
        children: [
          _LegendDot(label: 'Strong', color: Palette.green),
          _LegendDot(label: 'Even', color: Palette.gold),
          _LegendDot(label: 'Weak', color: Palette.red),
          _LegendDot(label: 'No Data', color: Palette.statLabel),
        ],
      ),
    );
  }
}

class _LegendDot extends StatelessWidget {
  const _LegendDot({required this.label, required this.color});

  final String label;
  final Color color;

  @override
  Widget build(BuildContext context) {
    return Row(
      children: [
        Container(
          width: 8,
          height: 8,
          decoration: BoxDecoration(color: color, shape: BoxShape.circle),
        ),
        const SizedBox(width: 5),
        Text(label, style: bodyStyle(size: 10, color: Palette.statLabel)),
      ],
    );
  }
}

class _HeadToHeadHeatRow extends StatelessWidget {
  const _HeadToHeadHeatRow({
    required this.row,
    required this.selected,
    required this.onTap,
  });

  final _HeadToHeadRowData row;
  final bool selected;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final heatColor = _heatColor(row.winRate, row.played);
    final fill = row.played == 0 ? 0.0 : row.winRate.clamp(0.0, 1.0);

    return Pressable(
      onTap: onTap,
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
        decoration: BoxDecoration(
          borderRadius: BorderRadius.circular(2),
          color: selected
              ? Palette.gold.withValues(alpha: 0.08)
              : Palette.cardBg.withValues(alpha: 0.18),
          border: Border.all(
            color: selected
                ? Palette.gold.withValues(alpha: 0.55)
                : Palette.border.withValues(alpha: 0.55),
          ),
        ),
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.center,
          children: [
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    row.opponentName,
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: bodyStyle(size: 14, color: Palette.white),
                  ),
                  const SizedBox(height: 3),
                  Text(
                    '${row.wins}-${row.losses}  ·  ${row.confidence}',
                    style: bodyStyle(size: 11, color: Palette.statLabel),
                  ),
                ],
              ),
            ),
            const SizedBox(width: 12),
            SizedBox(
              width: 130,
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.end,
                children: [
                  Text(
                    row.played == 0
                        ? '--'
                        : '${(row.winRate * 100).toStringAsFixed(0)}%',
                    style: bodyStyle(size: 14, color: heatColor),
                  ),
                  const SizedBox(height: 6),
                  ClipRRect(
                    borderRadius: BorderRadius.circular(999),
                    child: Stack(
                      children: [
                        Container(
                          height: 6,
                          color: Palette.border.withValues(alpha: 0.4),
                        ),
                        FractionallySizedBox(
                          widthFactor: fill,
                          child: Container(height: 6, color: heatColor),
                        ),
                      ],
                    ),
                  ),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _Tag extends StatelessWidget {
  const _Tag({required this.label});

  final String label;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
      decoration: BoxDecoration(
        border: Border.all(
          color: Palette.gold.withValues(alpha: 0.72),
          width: 1,
        ),
        borderRadius: BorderRadius.circular(2),
        gradient: const LinearGradient(
          begin: Alignment.topCenter,
          end: Alignment.bottomCenter,
          colors: [Color(0x55FFC500), Color(0x22FFC500)],
        ),
        boxShadow: [
          BoxShadow(
            color: Palette.gold.withValues(alpha: 0.16),
            blurRadius: 8,
            offset: const Offset(0, 2),
          ),
        ],
      ),
      child: Text(
        label,
        style: bodyStyle(
          size: 11,
          color: Palette.gold,
          weight: FontWeight.w700,
          letterSpacing: 0.4,
        ),
      ),
    );
  }
}

class _GoldDivider extends StatelessWidget {
  const _GoldDivider();

  @override
  Widget build(BuildContext context) {
    return Container(
      height: 1,
      margin: const EdgeInsets.only(bottom: 2),
      decoration: const BoxDecoration(
        gradient: LinearGradient(
          colors: [Colors.transparent, Color(0xFFFFC500), Colors.transparent],
        ),
      ),
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

class _InfoItem {
  const _InfoItem(this.label, this.value);

  final String label;
  final String value;
}

class _HeadToHeadRowData {
  const _HeadToHeadRowData({
    required this.opponentId,
    required this.opponentName,
    required this.played,
    required this.wins,
    required this.losses,
    required this.winRate,
    required this.adjustedWinRate,
  });

  final String opponentId;
  final String opponentName;
  final int played;
  final int wins;
  final int losses;
  final double winRate;
  final double adjustedWinRate;

  String get confidence {
    if (played >= 8) return 'High confidence';
    if (played >= 3) return 'Medium confidence';
    if (played > 0) return 'Low confidence';
    return 'No data';
  }
}

List<_HeadToHeadRowData> _buildHeadToHeadRows({
  required String fighterId,
  required List<Fighter> fighters,
  required List<Map<String, dynamic>> matches,
}) {
  final namesById = <String, String>{};
  for (final fighter in fighters) {
    namesById[fighter.id] = fighter.name;
  }

  final aggregates = <String, _VsAggregate>{};
  for (final match in matches) {
    final opponentId = match['opponent_id'] as String?;
    if (opponentId == null || opponentId.isEmpty) continue;
    final entry = aggregates.putIfAbsent(opponentId, _VsAggregate.new);
    final result = (match['result'] as String? ?? '').toUpperCase();
    entry.played += 1;
    if (result == 'WIN') {
      entry.wins += 1;
    } else if (result == 'LOSS') {
      entry.losses += 1;
    }
  }

  final rows = fighters
      .where((fighter) => fighter.id != fighterId)
      .map((fighter) {
        final agg = aggregates[fighter.id];
        final played = agg?.played ?? 0;
        final wins = agg?.wins ?? 0;
        final losses = agg?.losses ?? 0;
        final winRate = played > 0 ? wins / played : 0.0;
        final adjusted = (wins + 2) / (played + 4);
        return _HeadToHeadRowData(
          opponentId: fighter.id,
          opponentName: namesById[fighter.id] ?? 'Unknown',
          played: played,
          wins: wins,
          losses: losses,
          winRate: winRate,
          adjustedWinRate: adjusted,
        );
      })
      .toList();

  rows.sort((a, b) {
    if (a.played == 0 && b.played == 0) {
      return a.opponentName.compareTo(b.opponentName);
    }
    if (a.played == 0) {
      return 1;
    }
    if (b.played == 0) {
      return -1;
    }
    final byAdjusted = b.adjustedWinRate.compareTo(a.adjustedWinRate);
    if (byAdjusted != 0) {
      return byAdjusted;
    }
    return b.played.compareTo(a.played);
  });
  return rows;
}

class _VsAggregate {
  int played = 0;
  int wins = 0;
  int losses = 0;
}

int _asInt(dynamic value) {
  if (value is int) return value;
  if (value is num) return value.toInt();
  if (value is String) return int.tryParse(value) ?? 0;
  return 0;
}

double _asDouble(dynamic value) {
  if (value is double) return value;
  if (value is num) return value.toDouble();
  if (value is String) return double.tryParse(value) ?? 0.0;
  return 0.0;
}

String _formatBetVolume(Map<String, dynamic>? stats, WalletState wallet) {
  if (stats == null) return '--';
  final totalBetVolumeSkr = (stats['total_bet_volume'] as num?)?.toDouble();
  if (totalBetVolumeSkr == null || !totalBetVolumeSkr.isFinite) return '--';
  final totalBetVolumeUsd = skrToUsd(totalBetVolumeSkr, wallet);
  if (totalBetVolumeUsd > 0 || totalBetVolumeSkr == 0) {
    return '\$${totalBetVolumeUsd.toStringAsFixed(2)}';
  }
  return '${totalBetVolumeSkr.toStringAsFixed(2)} ${wallet.seekerSymbol}';
}

Color _heatColor(double winRate, int played) {
  if (played == 0) {
    return Palette.statLabel;
  }
  if (winRate >= 0.65) {
    return Palette.green;
  }
  if (winRate >= 0.45) {
    return Palette.gold;
  }
  return Palette.red;
}
