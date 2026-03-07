import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../core/constants.dart';
import '../core/palette.dart';
import '../core/typography.dart';
import '../models/match.dart';
import '../providers/match_provider.dart';
import '../router.dart';
import '../widgets/betting/bet_bottom_sheet.dart';
import '../widgets/fighter/fighter_image.dart';
import '../widgets/shared/app_shell.dart';
import '../widgets/shared/gold_gradient_divider.dart';
import '../widgets/shared/ik_loader.dart';
import '../widgets/shared/pressable.dart';

class BattleDetailScreen extends ConsumerStatefulWidget {
  const BattleDetailScreen({super.key, required this.onNavigate, this.matchId});

  final void Function(String) onNavigate;
  final String? matchId;

  @override
  ConsumerState<BattleDetailScreen> createState() => _BattleDetailScreenState();
}

class _BattleDetailScreenState extends ConsumerState<BattleDetailScreen> {
  int _selectedFighter = 0;

  @override
  Widget build(BuildContext context) {
    final matchState = ref.watch(matchProvider);
    final matches = matchState.matches;

    if (!matchState.hasLoaded) {
      return const Scaffold(
        backgroundColor: Palette.black,
        body: Center(child: IKLoader(size: 40)),
      );
    }

    if (matches.isEmpty) {
      return const Scaffold(
        backgroundColor: Palette.black,
        body: Center(
          child: Text(
            'No matches available',
            style: TextStyle(color: Palette.muted, fontSize: 16),
          ),
        ),
      );
    }

    final match = widget.matchId != null
        ? matches.firstWhere(
            (m) => m.id == widget.matchId,
            orElse: () => matches.first,
          )
        : matches.first;

    return AppShell(
      activeTab: NavTab.arena,
      scrollable: true,
      contentBottomPadding: 140,
      onNavigate: (slug) => widget.onNavigate(routeFor(slug)),
      content: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          const SizedBox(height: 6),
          Center(
            child: Text(
              match.label,
              style: bodyStyle(size: 14, color: Palette.secondary),
            ),
          ),
          const SizedBox(height: 6),
          Center(
            child: Text(
              _queueLabel(match),
              style: displayStyle(
                size: 15,
                color: match.status == MatchStatus.live
                    ? Palette.green
                    : Palette.gold,
                letterSpacing: 0.8,
              ),
            ),
          ),
          const SizedBox(height: 10),
          SizedBox(
            height: 220,
            child: Row(
              crossAxisAlignment: CrossAxisAlignment.end,
              children: [
                Expanded(
                  child: _PortraitTap(
                    onTap: () => widget.onNavigate(
                      '/fighter-details/${match.fighter1?.id}',
                    ),
                    child: match.fighter1 != null
                        ? FighterImage(
                            fighter: match.fighter1!,
                            fit: BoxFit.contain,
                          )
                        : Image.asset(Assets.battleLeft, fit: BoxFit.contain),
                  ),
                ),
                Padding(
                  padding: const EdgeInsets.only(bottom: 24),
                  child: Text(
                    'VS',
                    style: displayStyle(size: 30, color: Palette.gold),
                  ),
                ),
                Expanded(
                  child: _PortraitTap(
                    onTap: () => widget.onNavigate(
                      '/fighter-details/${match.fighter2?.id}',
                    ),
                    child: match.fighter2 != null
                        ? FighterImage(
                            fighter: match.fighter2!,
                            fit: BoxFit.contain,
                          )
                        : Image.asset(Assets.battleRight, fit: BoxFit.contain),
                  ),
                ),
              ],
            ),
          ),
          Row(
            children: [
              Expanded(
                child: _FighterLabel(
                  name: match.fighter1?.name ?? '?',
                  model: match.fighter1?.llmModel ?? '',
                  onTap: () => widget.onNavigate(
                    '/fighter-details/${match.fighter1?.id}',
                  ),
                ),
              ),
              Expanded(
                child: _FighterLabel(
                  name: match.fighter2?.name ?? '?',
                  model: match.fighter2?.llmModel ?? '',
                  onTap: () => widget.onNavigate(
                    '/fighter-details/${match.fighter2?.id}',
                  ),
                ),
              ),
            ],
          ),
          const GoldGradientDivider(
            margin: EdgeInsets.fromLTRB(18, 12, 18, 12),
          ),
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 16),
            child: Row(
              children: [
                Expanded(
                  child: _OddsPick(
                    selected: _selectedFighter == 0,
                    sideLabel: 'A',
                    name: match.fighter1?.name ?? 'Fighter 1',
                    odds: match.odds.fighter1Odds,
                    onTap: () => _pickSideAndOpenBet(0, match),
                  ),
                ),
                const SizedBox(width: 10),
                Expanded(
                  child: _OddsPick(
                    selected: _selectedFighter == 1,
                    sideLabel: 'B',
                    name: match.fighter2?.name ?? 'Fighter 2',
                    odds: match.odds.fighter2Odds,
                    onTap: () => _pickSideAndOpenBet(1, match),
                  ),
                ),
              ],
            ),
          ),
          if (!match.bettingOpen) ...[
            const SizedBox(height: 10),
            Center(
              child: Text(
                'Betting closed',
                style: bodyStyle(size: 13, color: Palette.muted),
              ),
            ),
          ],
          const GoldGradientDivider(margin: EdgeInsets.fromLTRB(18, 14, 18, 0)),
          const SizedBox(height: 6),
        ],
      ),
    );
  }

  void _pickSideAndOpenBet(int side, Match match) {
    setState(() => _selectedFighter = side);
    if (!match.bettingOpen) return;
    _openBetSheet(match);
  }

  void _openBetSheet(Match match) {
    showModalBottomSheet<void>(
      context: context,
      isScrollControlled: true,
      backgroundColor: Colors.transparent,
      builder: (_) => BetBottomSheet(
        match: match,
        initialSelectedFighter: _selectedFighter,
      ),
    );
  }

  static String _queueLabel(Match match) {
    if (match.status == MatchStatus.live) {
      return 'LIVE';
    }
    if (match.status == MatchStatus.upcoming) {
      final q = match.queuePosition;
      if (q == 1) return 'NEXT MATCH';
      if (q != null && q >= 2) return '#$q IN-QUEUE';
      return 'IN-QUEUE';
    }
    if (match.status == MatchStatus.completed) return 'COMPLETED';
    return 'CANCELLED';
  }
}

class _PortraitTap extends StatelessWidget {
  const _PortraitTap({required this.onTap, required this.child});

  final VoidCallback onTap;
  final Widget child;

  @override
  Widget build(BuildContext context) {
    return Pressable(
      onTap: onTap,
      scaleTo: 0.97,
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 2),
        child: child,
      ),
    );
  }
}

class _FighterLabel extends StatelessWidget {
  const _FighterLabel({
    required this.name,
    required this.model,
    required this.onTap,
  });

  final String name;
  final String model;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return Pressable(
      onTap: onTap,
      scaleTo: 0.98,
      child: Column(
        children: [
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 10),
            child: Text(
              name,
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              textAlign: TextAlign.center,
              style: displayStyle(size: 21, color: Palette.gold),
            ),
          ),
          const SizedBox(height: 1),
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 10),
            child: Text(
              model,
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              textAlign: TextAlign.center,
              style: bodyStyle(size: 12, color: Palette.secondary),
            ),
          ),
        ],
      ),
    );
  }
}

class _OddsPick extends StatelessWidget {
  const _OddsPick({
    required this.selected,
    required this.sideLabel,
    required this.name,
    required this.odds,
    required this.onTap,
  });

  final bool selected;
  final String sideLabel;
  final String name;
  final double odds;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return Pressable(
      onTap: onTap,
      scaleTo: 0.97,
      haptic: true,
      child: AnimatedContainer(
        duration: const Duration(milliseconds: 180),
        curve: Curves.easeOut,
        padding: const EdgeInsets.fromLTRB(10, 10, 10, 8),
        decoration: BoxDecoration(
          border: Border.all(
            color: selected ? Palette.gold : Palette.border,
            width: selected ? 1.4 : 1,
          ),
          color: selected
              ? Palette.darkGold.withValues(alpha: 0.55)
              : Colors.transparent,
          borderRadius: BorderRadius.circular(4),
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              'SIDE $sideLabel',
              style: bodyStyle(
                size: 10,
                color: selected ? Palette.gold : Palette.muted,
                letterSpacing: 0.8,
              ),
            ),
            const SizedBox(height: 4),
            Text(
              name.toUpperCase(),
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: displayStyle(
                size: 15,
                color: selected ? Palette.gold : Palette.white,
              ),
            ),
            const SizedBox(height: 3),
            Text(
              '${odds.toStringAsFixed(2)}x',
              style: bodyStyle(
                size: 16,
                color: selected ? Palette.green : Palette.secondary,
                weight: FontWeight.w600,
              ),
            ),
          ],
        ),
      ),
    );
  }
}
