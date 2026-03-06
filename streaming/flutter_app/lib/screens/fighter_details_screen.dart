import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../core/palette.dart';
import '../core/typography.dart';
import '../models/fighter.dart';
import '../providers/fighter_provider.dart';
import '../providers/fighter_stats_provider.dart';
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
                    _InfoItem(
                      'Bet Volume',
                      stats != null
                          ? '${(stats['total_bet_volume'] as num? ?? 0).toStringAsFixed(2)} SOL'
                          : '0.00 SOL',
                    ),
                  ],
                ),
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
              title: 'Match History',
              child: matchesAsync.when(
                loading: () => const _Loader(),
                error: (error, stackTrace) => _emptyMsg('History unavailable'),
                data: (matches) {
                  if (matches.isEmpty) {
                    return _emptyMsg('No completed matches yet');
                  }
                  return Column(
                    children: matches
                        .take(10)
                        .map((match) => _MatchRow(match: match))
                        .toList(),
                  );
                },
              ),
            ),
            const SizedBox(height: 30),
            const _GoldDivider(),
            const SizedBox(height: 30),
            _DetailsSection(
              title: 'Head-to-Head Stats',
              child: Column(
                children: [
                  if (fighters.length > 1)
                    Container(
                      padding: const EdgeInsets.symmetric(horizontal: 14),
                      decoration: BoxDecoration(
                        border: Border.all(color: Palette.border),
                        borderRadius: BorderRadius.circular(2),
                      ),
                      child: DropdownButton<String>(
                        value: _vsOpponentId,
                        dropdownColor: Palette.cardBg,
                        style: bodyStyle(size: 14, color: Palette.white),
                        isExpanded: true,
                        underline: const SizedBox.shrink(),
                        onChanged: (value) =>
                            setState(() => _vsOpponentId = value),
                        items: fighters
                            .where((item) => item.id != fighter.id)
                            .map(
                              (item) => DropdownMenuItem<String>(
                                value: item.id,
                                child: Text(item.name),
                              ),
                            )
                            .toList(),
                      ),
                    ),
                  const SizedBox(height: 16),
                  if (vsAsync != null)
                    vsAsync.when(
                      loading: () => const _Loader(),
                      error: (error, stackTrace) =>
                          _emptyMsg('VS stats unavailable'),
                      data: (vs) {
                        if (vs == null) {
                          return _emptyMsg('No matches vs this opponent');
                        }
                        final opponentName =
                            fighters
                                .where((item) => item.id == _vsOpponentId)
                                .map((item) => item.name)
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
                        final wins = _asInt(vs['wins'] ?? vs['matches_won']);
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

class _MatchRow extends StatelessWidget {
  const _MatchRow({required this.match});

  final Map<String, dynamic> match;

  @override
  Widget build(BuildContext context) {
    final isWin = match['result'] == 'WIN';
    final opponent = match['opponent_name'] as String? ?? 'Unknown';
    final side = match['side'] as String? ?? '';
    final roundsWon = match['rounds_won'] as int? ?? 0;
    final roundsLost = match['rounds_lost'] as int? ?? 0;
    final date = _fmt(match['completed_at'] as String?);

    return Container(
      margin: const EdgeInsets.symmetric(vertical: 4),
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 8),
      decoration: BoxDecoration(
        borderRadius: BorderRadius.circular(2),
        border: Border.all(color: Palette.border.withValues(alpha: 0.9)),
        color: Palette.cardBg.withValues(alpha: 0.35),
      ),
      child: Row(
        children: [
          Container(
            width: 50,
            padding: const EdgeInsets.symmetric(horizontal: 4, vertical: 2),
            decoration: BoxDecoration(
              borderRadius: BorderRadius.circular(2),
              color: isWin
                  ? Palette.green.withValues(alpha: 0.14)
                  : Palette.red.withValues(alpha: 0.14),
              border: Border.all(
                color: isWin ? Palette.green : Palette.red,
                width: 0.9,
              ),
            ),
            child: Text(
              isWin ? 'WIN' : 'LOSS',
              style: bodyStyle(
                size: 10,
                color: isWin ? Palette.green : Palette.red,
              ),
              textAlign: TextAlign.center,
            ),
          ),
          const SizedBox(width: 10),
          Expanded(
            child: Text(
              'vs $opponent',
              style: bodyStyle(size: 14, color: Palette.white),
              overflow: TextOverflow.ellipsis,
            ),
          ),
          Text(
            '$roundsWon-$roundsLost  $side  $date',
            style: bodyStyle(size: 11, color: Palette.statLabel),
          ),
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
