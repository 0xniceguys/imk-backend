import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../core/palette.dart';
import '../core/typography.dart';
import '../core/constants.dart';
import '../router.dart';
import '../models/match.dart';
import '../providers/match_provider.dart';
import '../widgets/shared/app_shell.dart';
import '../widgets/shared/ornate_button.dart';
import '../widgets/shared/pressable.dart';
import '../widgets/shared/ik_loader.dart';
import '../widgets/betting/odds_display.dart';
import '../widgets/betting/bet_bottom_sheet.dart';

class BattleDetailScreen extends ConsumerWidget {
  const BattleDetailScreen({
    super.key,
    required this.onNavigate,
    this.matchId,
  });
  final void Function(String) onNavigate;
  final String? matchId;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final matchState = ref.watch(matchProvider);
    final matches = matchState.matches;
    if (!matchState.hasLoaded) {
      return const Scaffold(
        backgroundColor: Palette.black,
        body: Center(child: IKLoader(size: 40)),
      );
    }
    final match = matchId != null
        ? matches.firstWhere((m) => m.id == matchId,
            orElse: () => matches.first)
        : matches.first;

    return AppShell(
      activeTab: NavTab.arena,
      onNavigate: (slug) => onNavigate(routeFor(slug)),
      content: Column(
        children: [
          // Back + subtitle row
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 8),
            child: Row(
              mainAxisAlignment: MainAxisAlignment.spaceBetween,
              children: [
                Pressable(
                  onTap: () {
                    if (Navigator.of(context).canPop()) {
                      Navigator.of(context).pop();
                    } else {
                      onNavigate('/arena-list');
                    }
                  },
                  child: Row(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      const Icon(Icons.arrow_back_ios,
                          size: 14, color: Palette.muted),
                      const SizedBox(width: 4),
                      Text(
                        '${match.status.name[0].toUpperCase()}${match.status.name.substring(1)} Battle',
                        style: bodyStyle(size: 14, color: Palette.muted),
                      ),
                    ],
                  ),
                ),
                Text(match.label,
                    style: bodyStyle(size: 14, color: Palette.muted)),
              ],
            ),
          ),
          const SizedBox(height: 12),
          // Fighter matchup — separate tap targets per fighter
          Expanded(
            child: Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                // Fighter 1 (left)
                Expanded(
                  flex: 3,
                  child: Pressable(
                    onTap: () =>
                        onNavigate('/fighter-details/${match.fighter1.id}'),
                    scaleTo: 0.96,
                    child: Image.asset(Assets.battleLeft,
                        fit: BoxFit.contain),
                  ),
                ),
                // Center info
                Expanded(
                  flex: 4,
                  child: Padding(
                    padding: const EdgeInsets.only(top: 32),
                    child: Column(
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        Pressable(
                          onTap: () => onNavigate(
                              '/fighter-details/${match.fighter1.id}'),
                          child: Column(
                            children: [
                              Text(match.fighter1.name,
                                  style: displayStyle(
                                      size: 22, color: Palette.gold),
                                  textAlign: TextAlign.center),
                              Text(match.fighter1.llmModel,
                                  style: bodyStyle(
                                      size: 13,
                                      color: Palette.secondary),
                                  textAlign: TextAlign.center),
                            ],
                          ),
                        ),
                        const SizedBox(height: 8),
                        Text('V/S', style: displayStyle(size: 22)),
                        const SizedBox(height: 8),
                        Pressable(
                          onTap: () => onNavigate(
                              '/fighter-details/${match.fighter2.id}'),
                          child: Column(
                            children: [
                              Text(match.fighter2.name,
                                  style: displayStyle(
                                      size: 22, color: Palette.gold),
                                  textAlign: TextAlign.center),
                              Text(match.fighter2.llmModel,
                                  style: bodyStyle(
                                      size: 13,
                                      color: Palette.secondary),
                                  textAlign: TextAlign.center),
                            ],
                          ),
                        ),
                        const SizedBox(height: 14),
                        OddsDisplay(odds: match.odds, match: match),
                      ],
                    ),
                  ),
                ),
                // Fighter 2 (right)
                Expanded(
                  flex: 3,
                  child: Pressable(
                    onTap: () =>
                        onNavigate('/fighter-details/${match.fighter2.id}'),
                    scaleTo: 0.96,
                    child: Image.asset(Assets.battleRight,
                        fit: BoxFit.contain),
                  ),
                ),
              ],
            ),
          ),
          const SizedBox(height: 8),
          if (match.status == MatchStatus.live)
            OrnateButton(
              label: 'Watch Live',
              color: Palette.red,
              onTap: () => onNavigate('/live-match/${match.id}'),
            ),
          if (match.status == MatchStatus.live) const SizedBox(height: 8),
          OrnateButton(
            label: 'Place Bet',
            color: Palette.gold,
            onTap: () {
              showModalBottomSheet<void>(
                context: context,
                isScrollControlled: true,
                backgroundColor: Colors.transparent,
                builder: (_) => BetBottomSheet(match: match),
              );
            },
          ),
          const SizedBox(height: 8),
        ],
      ),
    );
  }
}
