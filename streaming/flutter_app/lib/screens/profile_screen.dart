import 'package:flutter/material.dart';
import 'package:flutter_boring_avatars/flutter_boring_avatars.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_svg/flutter_svg.dart';
import '../core/palette.dart';
import '../core/typography.dart';
import '../core/constants.dart';
import '../router.dart';
import '../providers/auth_provider.dart';
import '../models/bet.dart';
import '../providers/bet_provider.dart';
import '../providers/wallet_provider.dart';
import '../utils/skr_pricing.dart';
import '../widgets/shared/app_shell.dart';
import '../widgets/wallet/wallet_action.dart';
import '../widgets/shared/profile_stats.dart';
import '../widgets/shared/history_card.dart';
import '../widgets/shared/ik_loader.dart';
import '../widgets/shared/pressable.dart';

class ProfileScreen extends ConsumerStatefulWidget {
  const ProfileScreen({super.key, required this.onNavigate});
  final void Function(String) onNavigate;

  @override
  ConsumerState<ProfileScreen> createState() => _ProfileScreenState();
}

class _ProfileScreenState extends ConsumerState<ProfileScreen> {
  bool _isClaimingAll = false;
  String _claimAllAmountText = '';

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) async {
      await ref.read(betProvider.notifier).refresh();
      ref.invalidate(betSummaryProvider);
    });
  }

  Future<void> _claimAllBets(List<Bet> claimableBets) async {
    if (_isClaimingAll || claimableBets.isEmpty) return;
    final currency = claimableBets.first.currency;
    final total = claimableBets.fold<double>(
      0,
      (sum, bet) => sum + (bet.payout ?? bet.amount),
    );
    setState(() {
      _isClaimingAll = true;
      _claimAllAmountText = '${_trimAmount(total)} $currency';
    });

    var successCount = 0;
    var failureCount = 0;

    try {
      final notifier = ref.read(betProvider.notifier);
      for (var i = 0; i < claimableBets.length; i++) {
        final bet = claimableBets[i];
        try {
          await notifier.claimBet(betId: bet.id);
          successCount += 1;
        } catch (_) {
          failureCount += 1;
        }

        if (i < claimableBets.length - 1) {
          await Future.delayed(const Duration(milliseconds: 300));
        }
      }

      ref.invalidate(betSummaryProvider);
      if (!mounted) return;
      final message = failureCount == 0
          ? 'Claimed $successCount reward${successCount == 1 ? '' : 's'}'
          : 'Claimed $successCount, failed $failureCount';
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Text(message),
          backgroundColor: failureCount == 0 ? Palette.green : Palette.gold,
        ),
      );
    } finally {
      if (mounted) {
        setState(() {
          _isClaimingAll = false;
          _claimAllAmountText = '';
        });
      }
    }
  }

  Future<void> _handleLogout(BuildContext context) async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        backgroundColor: Palette.sheetBg,
        title: Text('Log out?', style: displayStyle(size: 22)),
        content: Text(
          'You will need to sign in again.',
          style: bodyStyle(size: 14, color: Palette.muted),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx, false),
            child: Text('Cancel', style: bodyStyle(color: Palette.muted)),
          ),
          TextButton(
            onPressed: () => Navigator.pop(ctx, true),
            child: Text('Log out', style: bodyStyle(color: Palette.red)),
          ),
        ],
      ),
    );
    if (confirmed == true && context.mounted) {
      await ref.read(authProvider.notifier).logout();
      if (context.mounted) {
        Navigator.of(
          context,
        ).pushNamedAndRemoveUntil('/sign-in-modal', (_) => false);
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final bets = ref.watch(betProvider);
    final summaryAsync = ref.watch(betSummaryProvider);
    final wallet = ref.watch(walletProvider);
    final auth = ref.watch(authProvider);
    final displayName = auth.email?.split('@').first.toUpperCase() ?? 'PLAYER';
    final avatarSeed = auth.email ?? auth.walletAddress ?? displayName;
    final wonBets = bets
        .where(
          (b) => b.status == BetStatus.won || b.status == BetStatus.claimed,
        )
        .length;
    final claimableBets = bets.where((b) => b.isClaimable).toList();
    final claimableTotal = claimableBets.fold<double>(
      0,
      (sum, bet) => sum + (bet.payout ?? bet.amount),
    );
    final claimableCurrency = claimableBets.isNotEmpty
        ? claimableBets.first.currency
        : wallet.seekerSymbol;
    final showClaimButton = claimableBets.isNotEmpty || _isClaimingAll;
    final claimButtonAmountText = _isClaimingAll && _claimAllAmountText.isNotEmpty
        ? _claimAllAmountText
        : '${_trimAmount(claimableTotal)} $claimableCurrency';
    final fallbackTotalBets = bets.length;
    final fallbackWinRate = fallbackTotalBets > 0
        ? '${(wonBets / fallbackTotalBets * 100).toStringAsFixed(0)}%'
        : '0%';
    final sortedBets = [...bets]
      ..sort((a, b) => a.placedAt.compareTo(b.placedAt));
    final firstBetAt = sortedBets.isNotEmpty ? sortedBets.first.placedAt : null;
    final daysActive = firstBetAt == null
        ? 0
        : DateTime.now().difference(firstBetAt).inDays;
    final fallbackBettingFor = '${daysActive.clamp(0, 9999)}d';

    final summary = summaryAsync.valueOrNull;
    final totalBets = summary?.totalBets ?? fallbackTotalBets;
    final winRate = summary != null
        ? '${(summary.winRate * 100).toStringAsFixed(0)}%'
        : fallbackWinRate;
    final skrUsdPrice = resolveSkrUsdPrice(wallet);
    final pnlValue = (summary?.netPnl ?? 0) * skrUsdPrice;
    final pnlText =
        '${pnlValue >= 0 ? '+' : ''}\$${pnlValue.toStringAsFixed(2)}';
    final pnlColor = pnlValue >= 0 ? Palette.green : Palette.red;

    return AppShell(
      activeTab: NavTab.profile,
      scrollable: true,
      contentBottomPadding: 180,
      headerTrailing: Pressable(
        onTap: () => _handleLogout(context),
        scaleTo: 0.96,
        opacityTo: 0.7,
        child: SvgPicture.asset(
          Assets.logoutIcon,
          width: 32,
          height: 32,
          // colorFilter: const ColorFilter.mode(Palette.red, BlendMode.srcIn),
        ),
      ),
      onNavigate: (slug) => widget.onNavigate(routeFor(slug)),
      content: Column(
        children: [
          const SizedBox(height: 8),
          // Avatar
          Container(
            width: 120,
            height: 120,
            decoration: BoxDecoration(
              border: Border.all(color: Palette.gold, width: 6),
              shape: BoxShape.circle,
            ),
            clipBehavior: Clip.antiAlias,
            child: BoringAvatar(
              name: avatarSeed,
              type: BoringAvatarType.ring,
              shape: const OvalBorder(),
              palette: const BoringAvatarPalette([
                Color(0xFFFFC500),
                Color(0xFF252525),
                Color(0xFF1A1A1A),
                Color(0xFF414141),
                Color(0xFFFFFFFF),
              ]),
            ),
          ),
          const SizedBox(height: 12),
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 24),
            child: Text(
              displayName,
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              textAlign: TextAlign.center,
              style: displayStyle(size: 30, color: Palette.gold),
            ),
          ),
          if (auth.email != null)
            Padding(
              padding: const EdgeInsets.symmetric(horizontal: 24, vertical: 12),
              child: Text(
                auth.email!,
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                textAlign: TextAlign.center,
                style: bodyStyle(size: 14, color: Palette.secondary),
              ),
            ),
          // Dynamic tier based on betting history
          () {
            final tier = totalBets < 5
                ? 'ROOKIE'
                : totalBets < 20
                ? 'GAMBLER'
                : totalBets < 50
                ? 'HIGH ROLLER'
                : 'WHALE';
            final level = (totalBets ~/ 5) + 1;
            return Text.rich(
              TextSpan(
                children: [
                  TextSpan(
                    text: 'LVL $level',
                    style: bodyStyle(size: 18, color: Palette.white),
                  ),
                  TextSpan(
                    text: ' - $tier',
                    style: bodyStyle(size: 18, color: Palette.secondary),
                  ),
                ],
              ),
            );
          }(),
          const SizedBox(height: 24),
          const WalletActionWidget(),
          const SizedBox(height: 24),
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 24),
            child: ProfileStatsWidget(
              winRate: winRate,
              totalBets: '$totalBets',
              plOverall: pnlText,
              plOverallColor: pnlColor,
              bettingFor: fallbackBettingFor,
              footer: showClaimButton
                  ? _ClaimAllButton(
                      amountText: claimButtonAmountText,
                      loading: _isClaimingAll,
                      onTap: () => _claimAllBets(claimableBets),
                    )
                  : null,
            ),
          ),
          const SizedBox(height: 24),
          Text('BET HISTORY', style: displayStyle(size: 22)),
          const SizedBox(height: 24),
          if (bets.isEmpty)
            Padding(
              padding: const EdgeInsets.symmetric(vertical: 20,),
              child: Text(
                'No bets yet',
                style: bodyStyle(size: 16, color: Palette.muted),
              ),
            )
          else
            for (final bet in [...bets]..sort((a, b) => b.placedAt.compareTo(a.placedAt))) ...[

              Padding(
              padding: const EdgeInsets.symmetric(vertical: 4,horizontal: 40),
              child:   HistoryCardWidget(
                bet: bet,
                onTap: () => widget.onNavigate('/battle-detail/${bet.matchId}'),
              ),
            ),
            
              const SizedBox(height: 14),
            ],
          const SizedBox(height: 32),
        ],
      ),
    );
  }

  String _trimAmount(double value) {
    final text = value.toStringAsFixed(2);
    return text.replaceFirst(RegExp(r'\.?0+$'), '');
  }
}

class _ClaimAllButton extends StatelessWidget {
  const _ClaimAllButton({
    required this.amountText,
    required this.loading,
    required this.onTap,
  });

  final String amountText;
  final bool loading;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return Pressable(
      onTap: loading ? null : onTap,
      scaleTo: 0.97,
      haptic: true,
      child: Column(
        children: [
          Transform.flip(
            flipY: true,
            child: SvgPicture.asset(
              Assets.ornateTrial,
              width: 250,
              height: 8,
              fit: BoxFit.fill,
            ),
          ),
          const SizedBox(height: 10),
          Opacity(
            opacity: loading ? 0.8 : 1,
            child: Column(
              children: [
                Text(
                  'CLAIM BETS & REWARDS',
                  textAlign: TextAlign.center,
                  style: bodyStyle(size: 13, color: Palette.secondary),
                ),
                const SizedBox(height: 8),
                if (loading)
                  const IKLoader(size: 24)
                else
                  Text(
                    amountText,
                    textAlign: TextAlign.center,
                    style: displayStyle(size: 22, color: Palette.gold),
                  ),
              ],
            ),
          ),
          const SizedBox(height: 10),
          SvgPicture.asset(
            Assets.ornateTrial,
            width: 250,
            height: 8,
            fit: BoxFit.fill,
          ),
        ],
      ),
    );
  }
}
