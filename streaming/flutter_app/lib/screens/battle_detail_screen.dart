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
import '../widgets/shared/ornate_button.dart';
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

    final selectedName = _selectedFighter == 0
        ? (match.fighter1?.name ?? 'Fighter 1')
        : (match.fighter2?.name ?? 'Fighter 2');
    final selectedOdds = _selectedFighter == 0
        ? match.odds.fighter1Odds
        : match.odds.fighter2Odds;

    return AppShell(
      activeTab: NavTab.arena,
      scrollable: true,
      contentBottomPadding: 180,
      onNavigate: (slug) => widget.onNavigate(routeFor(slug)),
      content: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          _TopRow(
            match: match,
            onBack: () {
              if (Navigator.of(context).canPop()) {
                Navigator.of(context).pop();
              } else {
                widget.onNavigate('/arena-list');
              }
            },
          ),
          const SizedBox(height: 6),
          Center(
            child: Text(
              match.label,
              style: bodyStyle(size: 14, color: Palette.secondary),
            ),
          ),
          const SizedBox(height: 4),
          Center(
            child: Text(
              _subhead(match),
              style: bodyStyle(size: 12, color: Palette.muted),
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
            child: Text(
              'WINNER MARKET',
              style: displayStyle(size: 20, color: Palette.gold),
            ),
          ),
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 2, 16, 10),
            child: Text(
              'Tap odds to pick your side. Bet slip opens with this selection.',
              style: bodyStyle(size: 12, color: Palette.muted),
            ),
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
                    onTap: () => setState(() => _selectedFighter = 0),
                  ),
                ),
                const SizedBox(width: 10),
                Expanded(
                  child: _OddsPick(
                    selected: _selectedFighter == 1,
                    sideLabel: 'B',
                    name: match.fighter2?.name ?? 'Fighter 2',
                    odds: match.odds.fighter2Odds,
                    onTap: () => setState(() => _selectedFighter = 1),
                  ),
                ),
              ],
            ),
          ),
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 8, 16, 0),
            child: Row(
              children: [
                Text(
                  'Potential return: ',
                  style: bodyStyle(size: 12, color: Palette.muted),
                ),
                Text(
                  '1 SKR → ${selectedOdds.toStringAsFixed(2)} SKR',
                  style: bodyStyle(size: 12, color: Palette.green),
                ),
              ],
            ),
          ),
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 2, 16, 0),
            child: Text(
              'Odds can update until bet placement.',
              style: bodyStyle(size: 11, color: Palette.statLabel),
            ),
          ),
          const SizedBox(height: 14),
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 16),
            child: Wrap(
              spacing: 22,
              runSpacing: 10,
              children: [
                _MetaText(
                  label: 'Total Volume',
                  value: '\$${match.totalPool.toStringAsFixed(1)}',
                ),
                _MetaText(label: 'Active Bets', value: '${match.activeBets}'),
                _MetaText(
                  label: 'ROI Range',
                  value:
                      '${match.odds.fighter1Odds.toStringAsFixed(1)}-${match.odds.fighter2Odds.toStringAsFixed(1)}x',
                ),
                _MetaText(label: 'Best Of', value: '${match.bestOf}'),
              ],
            ),
          ),
          const SizedBox(height: 20),
          if (match.status == MatchStatus.live)
            OrnateButton(
              label: 'Watch Live',
              color: Palette.red,
              onTap: () => widget.onNavigate('/live-match/${match.id}'),
            ),
          if (match.status == MatchStatus.live) const SizedBox(height: 10),
          if (match.bettingOpen)
            OrnateButton(
              label: 'Bet $selectedName  ${selectedOdds.toStringAsFixed(1)}x',
              color: Palette.gold,
              onTap: () => _openBetSheet(match),
            )
          else
            Center(
              child: Text(
                'Betting closed',
                style: bodyStyle(size: 15, color: Palette.muted),
              ),
            ),
          const SizedBox(height: 14),
        ],
      ),
    );
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

  static String _subhead(Match match) {
    final when = match.scheduledAt.toLocal();
    final hh = when.hour.toString().padLeft(2, '0');
    final mm = when.minute.toString().padLeft(2, '0');
    final status = switch (match.status) {
      MatchStatus.live => 'LIVE NOW',
      MatchStatus.upcoming => 'COMING',
      MatchStatus.completed => 'COMPLETED',
      MatchStatus.cancelled => 'CANCELLED',
    };
    return '$status • ${when.day}/${when.month} $hh:$mm';
  }
}

class _TopRow extends StatelessWidget {
  const _TopRow({required this.match, required this.onBack});

  final Match match;
  final VoidCallback onBack;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 6),
      child: Row(
        mainAxisAlignment: MainAxisAlignment.spaceBetween,
        children: [
          Pressable(
            onTap: onBack,
            child: Row(
              mainAxisSize: MainAxisSize.min,
              children: [
                const Icon(
                  Icons.arrow_back_ios,
                  size: 14,
                  color: Palette.muted,
                ),
                const SizedBox(width: 4),
                Text('Back', style: bodyStyle(size: 14, color: Palette.muted)),
              ],
            ),
          ),
          _MatchStatusTag(status: match.status),
        ],
      ),
    );
  }
}

class _MatchStatusTag extends StatelessWidget {
  const _MatchStatusTag({required this.status});

  final MatchStatus status;

  @override
  Widget build(BuildContext context) {
    final isLive = status == MatchStatus.live;
    final text = switch (status) {
      MatchStatus.live => 'LIVE',
      MatchStatus.upcoming => 'COMING',
      MatchStatus.completed => 'FINAL',
      MatchStatus.cancelled => 'CANCELLED',
    };
    final color = isLive ? Palette.green : Palette.gold;
    return Text(
      text,
      style: displayStyle(size: 14, color: color, letterSpacing: 0.8),
    );
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

class _MetaText extends StatelessWidget {
  const _MetaText({required this.label, required this.value});

  final String label;
  final String value;

  @override
  Widget build(BuildContext context) {
    return SizedBox(
      width: 148,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(label, style: bodyStyle(size: 12, color: Palette.statLabel)),
          const SizedBox(height: 2),
          Text(value, style: bodyStyle(size: 18, color: Palette.white)),
        ],
      ),
    );
  }
}
