import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../core/palette.dart';
import '../../core/typography.dart';
import '../../models/fighter.dart';
import '../../models/wallet_state.dart';
import '../../providers/fighter_stats_provider.dart';
import '../../providers/wallet_provider.dart';
import '../../utils/skr_pricing.dart';
import '../shared/pressable.dart';
import 'fighter_image.dart';

class FighterCarousel extends StatefulWidget {
  const FighterCarousel({
    super.key,
    required this.fighters,
    required this.onMoreDetails,
  });

  final List<Fighter> fighters;
  final void Function(String fighterId) onMoreDetails;

  @override
  State<FighterCarousel> createState() => _FighterCarouselState();
}

class _FighterCarouselState extends State<FighterCarousel> {
  late final PageController _controller;
  int _currentPage = 0;
  static const int _loopBase = 10000;
  static const List<double> _greyscaleMatrix = <double>[
    0.2126,
    0.7152,
    0.0722,
    0,
    0,
    0.2126,
    0.7152,
    0.0722,
    0,
    0,
    0.2126,
    0.7152,
    0.0722,
    0,
    0,
    0,
    0,
    0,
    1,
    0,
  ];

  int _realIndexForPage(int page) {
    final count = widget.fighters.length;
    if (count == 0) return 0;
    final mod = page % count;
    return mod < 0 ? mod + count : mod;
  }

  Future<void> _goToRelativePage(int delta) async {
    final target = _currentPage + delta;
    await _controller.animateToPage(
      target,
      duration: const Duration(milliseconds: 280),
      curve: Curves.easeOutCubic,
    );
  }

  @override
  void initState() {
    super.initState();
    final initialPage = widget.fighters.isEmpty
        ? 0
        : widget.fighters.length * _loopBase;
    _currentPage = initialPage;
    _controller = PageController(
      viewportFraction: 0.5,
      initialPage: initialPage,
    );
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      behavior: HitTestBehavior.translucent,
      onHorizontalDragEnd: (details) {
        final vx = details.primaryVelocity ?? 0;
        if (vx <= -120) {
          _goToRelativePage(1);
        } else if (vx >= 120) {
          _goToRelativePage(-1);
        }
      },
      child: LayoutBuilder(
        builder: (context, constraints) {
          final currentRealIndex = _realIndexForPage(_currentPage);
          final fighter = widget.fighters[currentRealIndex];
          final heroHeight = (constraints.maxHeight - 190)
              .clamp(220.0, 420.0)
              .toDouble();
          final activeCardHeight = (heroHeight * 0.95)
              .clamp(220.0, 380.0)
              .toDouble();
          final inactiveCardHeight = (heroHeight * 0.70)
              .clamp(170.0, 300.0)
              .toDouble();

          return Column(
            children: [
              SizedBox(
                height: heroHeight,
                child: Stack(
                  alignment: Alignment.bottomCenter,
                  children: [
                    PageView.builder(
                      controller: _controller,
                      physics: const NeverScrollableScrollPhysics(),
                      onPageChanged: (index) =>
                          setState(() => _currentPage = index),
                      itemBuilder: (context, index) {
                        final active = index == _currentPage;
                        final item = widget.fighters[_realIndexForPage(index)];
                        Widget artwork = _FighterArtwork(fighter: item);
                        if (!active) {
                          artwork = ColorFiltered(
                            colorFilter: const ColorFilter.matrix(
                              _greyscaleMatrix,
                            ),
                            child: artwork,
                          );
                        }

                        return Align(
                          alignment: Alignment.bottomCenter,
                          child: AnimatedContainer(
                            duration: const Duration(milliseconds: 260),
                            curve: Curves.easeOutCubic,
                            height: active
                                ? activeCardHeight
                                : inactiveCardHeight,
                            margin: EdgeInsets.only(
                              top: active ? 16 : 56,
                              bottom: active ? 0 : 10,
                            ),
                            child: Pressable(
                              onTap: () => widget.onMoreDetails(item.id),
                              scaleTo: 0.97,
                              opacityTo: 0.92,
                              child: AnimatedOpacity(
                                duration: const Duration(milliseconds: 260),
                                opacity: active ? 1 : 0.55,
                                child: artwork,
                              ),
                            ),
                          ),
                        );
                      },
                    ),
                    Positioned(
                      left: 20,
                      right: 20,
                      bottom: 16,
                      child: IgnorePointer(
                        child: Column(
                          children: [
                            _CarouselIndicators(
                              count: widget.fighters.length,
                              activeIndex: currentRealIndex,
                            ),
                            const SizedBox(height: 8),
                            AnimatedSwitcher(
                              duration: const Duration(milliseconds: 220),
                              child: Text(
                                fighter.name,
                                key: ValueKey('fighter_name_${fighter.id}'),
                                textAlign: TextAlign.center,
                                maxLines: 1,
                                overflow: TextOverflow.ellipsis,
                                style: displayStyle(
                                  size: 30,
                                  color: Palette.gold,
                                  height: 0.92,
                                ),
                              ),
                            ),
                            AnimatedSwitcher(
                              duration: const Duration(milliseconds: 220),
                              child: Text(
                                fighter.llmModel,
                                key: ValueKey('fighter_model_${fighter.id}'),
                                textAlign: TextAlign.center,
                                maxLines: 1,
                                overflow: TextOverflow.ellipsis,
                                style: bodyStyle(
                                  size: 15,
                                  color: Palette.secondary,
                                ),
                              ),
                            ),
                          ],
                        ),
                      ),
                    ),
                  ],
                ),
              ),
              Container(
                height: 1,
                margin: const EdgeInsets.symmetric(horizontal: 20),
                decoration: const BoxDecoration(
                  gradient: LinearGradient(
                    colors: [
                      Colors.transparent,
                      Color(0xFFFFC500),
                      Colors.transparent,
                    ],
                  ),
                ),
              ),
              const SizedBox(height: 14),
              Padding(
                padding: const EdgeInsets.symmetric(horizontal: 22),
                child: _FighterOverviewStats(fighter: fighter),
              ),
              const SizedBox(height: 14),
              Pressable(
                onTap: () => widget.onMoreDetails(fighter.id),
                scaleTo: 0.96,
                opacityTo: 0.8,
                child: Text(
                  'MORE DETAILS',
                  style: displayStyle(
                    size: 16,
                    color: Palette.secondary,
                    decoration: TextDecoration.underline,
                  ),
                ),
              ),
              const SizedBox(height: 8),
            ],
          );
        },
      ),
    );
  }
}

class _FighterArtwork extends StatelessWidget {
  const _FighterArtwork({required this.fighter});

  final Fighter fighter;

  @override
  Widget build(BuildContext context) {
    return FighterImage(fighter: fighter, fit: BoxFit.contain);
  }
}

class _CarouselIndicators extends StatelessWidget {
  const _CarouselIndicators({required this.count, required this.activeIndex});

  final int count;
  final int activeIndex;

  @override
  Widget build(BuildContext context) {
    return Column(
      children: [
        Row(
          mainAxisSize: MainAxisSize.min,
          children: List.generate(
            count,
            (index) => Container(
              width: 4,
              height: 4,
              margin: const EdgeInsets.symmetric(horizontal: 2),
              decoration: BoxDecoration(
                shape: BoxShape.circle,
                color: index == activeIndex
                    ? Palette.gold
                    : const Color(0xFF555555),
              ),
            ),
          ),
        ),
        const SizedBox(height: 4),
        Text(
          '${activeIndex + 1}/$count',
          style: bodyStyle(size: 9, color: Palette.statLabel),
        ),
      ],
    );
  }
}

class _FighterOverviewStats extends ConsumerWidget {
  const _FighterOverviewStats({required this.fighter});

  final Fighter fighter;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final statsAsync = ref.watch(fighterStatsProvider(fighter.id));
    final wallet = ref.watch(walletProvider);

    final winRateText = statsAsync.maybeWhen(
      data: (stats) =>
          _winRate((stats?['win_rate'] as num?)?.toDouble() ?? fighter.winRate),
      orElse: () => _winRate(fighter.winRate),
    );
    final matchesPlayedText = statsAsync.maybeWhen(
      data: (stats) =>
          '${(stats?['matches_played'] as num?)?.toInt() ?? fighter.matchesPlayed}',
      orElse: () => '${fighter.matchesPlayed.clamp(0, 999999)}',
    );
    final betVolumeText = statsAsync.when(
      loading: () => '--',
      error: (_, _) => '--',
      data: (stats) {
        if (stats == null) return '--';
        final totalBetVolumeSkr =
            (stats['total_bet_volume'] as num?)?.toDouble() ?? 0;
        return _formatBetVolume(totalBetVolumeSkr, wallet);
      },
    );

    final stats = [
      _StatItem('Win Rate', winRateText),
      _StatItem('Matches Played', matchesPlayedText),
      _StatItem('Bet Volume', betVolumeText),
      _StatItem(
        'Fight Style',
        fighter.fightStyle.trim().isEmpty ? 'N/A' : fighter.fightStyle,
      ),
    ];

    return Wrap(
      alignment: WrapAlignment.center,
      spacing: 18,
      runSpacing: 12,
      children: stats
          .map(
            (stat) => SizedBox(
              width: 128,
              child: Column(
                children: [
                  Text(
                    stat.label,
                    textAlign: TextAlign.center,
                    style: bodyStyle(size: 14, color: Palette.statLabel),
                  ),
                  const SizedBox(height: 5),
                  Text(
                    stat.value,
                    textAlign: TextAlign.center,
                    maxLines: 2,
                    overflow: TextOverflow.ellipsis,
                    style: bodyStyle(size: 16, color: Palette.white),
                  ),
                ],
              ),
            ),
          )
          .toList(),
    );
  }

  static String _winRate(double value) {
    if (value.isNaN || value.isInfinite) return 'N/A';
    return '${(value * 100).toStringAsFixed(0)}%';
  }

  static String _formatBetVolume(double totalBetVolumeSkr, WalletState wallet) {
    if (!totalBetVolumeSkr.isFinite) return '--';
    final totalBetVolumeUsd = skrToUsd(totalBetVolumeSkr, wallet);
    if (totalBetVolumeUsd > 0 || totalBetVolumeSkr == 0) {
      return '\$${totalBetVolumeUsd.toStringAsFixed(2)}';
    }
    return '${totalBetVolumeSkr.toStringAsFixed(2)} ${wallet.seekerSymbol}';
  }
}

class _StatItem {
  const _StatItem(this.label, this.value);

  final String label;
  final String value;
}
