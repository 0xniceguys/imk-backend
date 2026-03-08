import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../core/palette.dart';
import '../core/typography.dart';
import '../providers/fighter_provider.dart';
import '../providers/fighter_stats_provider.dart';
import '../providers/wallet_provider.dart';
import '../widgets/fighter/match_history.dart';
import '../widgets/shared/ik_loader.dart';
import '../widgets/shared/pressable.dart';

class FighterMatchHistoryScreen extends ConsumerStatefulWidget {
  const FighterMatchHistoryScreen({
    super.key,
    required this.onNavigate,
    this.fighterId,
  });

  final void Function(String) onNavigate;
  final String? fighterId;

  @override
  ConsumerState<FighterMatchHistoryScreen> createState() =>
      _FighterMatchHistoryScreenState();
}

class _FighterMatchHistoryScreenState
    extends ConsumerState<FighterMatchHistoryScreen> {
  MatchHistoryResultFilter _resultFilter = MatchHistoryResultFilter.all;
  String? _opponentId;
  bool _nonZeroPoolOnly = false;

  @override
  Widget build(BuildContext context) {
    final fighters = ref.watch(fighterProvider);
    if (fighters.isEmpty) {
      return const Center(child: IKLoader(size: 40));
    }

    final initialIndex = widget.fighterId != null
        ? fighters.indexWhere((fighter) => fighter.id == widget.fighterId)
        : 0;
    final fighter = fighters[initialIndex >= 0 ? initialIndex : 0];
    final wallet = ref.watch(walletProvider);
    final bottom = MediaQuery.of(context).padding.bottom;
    final matchesAsync = ref.watch(fighterMatchesProvider(fighter.id));

    return SafeArea(
      bottom: false,
      child: Padding(
        padding: EdgeInsets.fromLTRB(24, 8, 24, bottom + 20),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Pressable(
              onTap: () => Navigator.of(context).maybePop(),
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
            const SizedBox(height: 18),
            Text(
              'All Matches',
              style: displayStyle(size: 30, color: Palette.gold),
            ),
            const SizedBox(height: 6),
            Text(
              fighter.name,
              style: bodyStyle(size: 14, color: Palette.secondary),
            ),
            const SizedBox(height: 22),
            Expanded(
              child: matchesAsync.when(
                loading: () => const Center(child: IKLoader(size: 28)),
                error: (error, stackTrace) => Center(
                  child: Text(
                    'History unavailable',
                    style: bodyStyle(size: 14, color: Palette.secondary),
                  ),
                ),
                data: (matches) {
                  if (matches.isEmpty) {
                    return Center(
                      child: Text(
                        'No completed matches yet',
                        style: bodyStyle(size: 14, color: Palette.secondary),
                      ),
                    );
                  }

                  final opponentOptions = buildMatchHistoryOpponentOptions(
                    matches,
                  );
                  final filteredMatches = filterMatchHistory(
                    matches,
                    resultFilter: _resultFilter,
                    opponentId: _opponentId,
                    nonZeroPoolOnly: _nonZeroPoolOnly,
                  );

                  return Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      MatchHistoryFilters(
                        resultFilter: _resultFilter,
                        opponentId: _opponentId,
                        opponentOptions: opponentOptions,
                        onResultChanged: (value) =>
                            setState(() => _resultFilter = value),
                        onOpponentChanged: (value) =>
                            setState(() => _opponentId = value),
                        nonZeroPoolOnly: _nonZeroPoolOnly,
                        onNonZeroPoolOnlyChanged: (value) =>
                            setState(() => _nonZeroPoolOnly = value),
                      ),
                      const SizedBox(height: 18),
                      Expanded(
                        child: filteredMatches.isEmpty
                            ? Center(
                                child: Text(
                                  'No matches found for the selected filters',
                                  style: bodyStyle(
                                    size: 14,
                                    color: Palette.secondary,
                                  ),
                                ),
                              )
                            : ListView.separated(
                                itemCount: filteredMatches.length,
                                separatorBuilder: (_, index) =>
                                    const SizedBox(height: 10),
                                itemBuilder: (context, index) {
                                  return MatchHistoryCard(
                                    match: filteredMatches[index],
                                    fighterName: fighter.name,
                                    tokenSymbol: wallet.seekerSymbol,
                                  );
                                },
                              ),
                      ),
                    ],
                  );
                },
              ),
            ),
          ],
        ),
      ),
    );
  }
}
