import 'package:flutter/material.dart';
import 'package:flutter_boring_avatars/flutter_boring_avatars.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../core/palette.dart';
import '../core/typography.dart';
import '../router.dart';
import '../providers/auth_provider.dart';
import '../models/bet.dart';
import '../providers/bet_provider.dart';
import '../widgets/shared/app_shell.dart';
import '../widgets/wallet/wallet_action.dart';
import '../widgets/shared/profile_stats.dart';
import '../widgets/shared/history_card.dart';
import '../widgets/shared/pressable.dart';

class ProfileScreen extends ConsumerWidget {
  const ProfileScreen({super.key, required this.onNavigate});
  final void Function(String) onNavigate;

  Future<void> _handleLogout(BuildContext context, WidgetRef ref) async {
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
        Navigator.of(context)
            .pushNamedAndRemoveUntil('/sign-in-modal', (_) => false);
      }
    }
  }

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final bets = ref.watch(betProvider);
    final auth = ref.watch(authProvider);
    final displayName = auth.email?.split('@').first.toUpperCase() ?? 'PLAYER';
    final avatarSeed = auth.email ?? auth.walletAddress ?? displayName;
    final wonBets = bets.where((b) => b.status == BetStatus.won).length;
    final totalBets = bets.length;
    final winRate = totalBets > 0
        ? '${(wonBets / totalBets * 100).toStringAsFixed(0)}%'
        : '0%';

    return AppShell(
      activeTab: NavTab.profile,
      scrollable: true,
      contentBottomPadding: 180,
      headerTrailing: Pressable(
        onTap: () => _handleLogout(context, ref),
        scaleTo: 0.96,
        opacityTo: 0.7,
        child: Text('Log out', style: bodyStyle(size: 16, color: Palette.red)),
      ),
      onNavigate: (slug) => onNavigate(routeFor(slug)),
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
              TextSpan(children: [
                TextSpan(
                    text: 'LVL $level',
                    style: bodyStyle(size: 18, color: Palette.white)),
                TextSpan(
                    text: ' - $tier',
                    style:
                        bodyStyle(size: 18, color: Palette.secondary)),
              ]),
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
            ),
          ),
          const SizedBox(height: 24),
          Text('Bet History', style: displayStyle(size: 24)),
          if (bets.isEmpty)
            Padding(
              padding: const EdgeInsets.symmetric(vertical: 20),
              child: Text('No bets yet',
                  style: bodyStyle(size: 16, color: Palette.muted)),
            )
          else
            for (final bet in bets) ...[
              HistoryCardWidget(
                bet: bet,
                onTap: () => onNavigate('/battle-detail'),
              ),
              const SizedBox(height: 14),
            ],
          const SizedBox(height: 32),
        ],
      ),
    );
  }
}
