import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../core/palette.dart';
import '../core/typography.dart';
import '../core/constants.dart';
import '../router.dart';
import '../providers/auth_provider.dart';
import '../models/bet.dart';
import '../providers/bet_provider.dart';
import '../widgets/shared/app_shell.dart';
import '../widgets/wallet/wallet_action.dart';
import '../widgets/shared/profile_stats.dart';
import '../widgets/shared/history_card.dart';
import '../widgets/wallet/wallet_manage_sheet.dart';
import '../widgets/shared/pressable.dart';

class ProfileScreen extends ConsumerWidget {
  const ProfileScreen({super.key, required this.onNavigate});
  final void Function(String) onNavigate;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final bets = ref.watch(betProvider);
    final auth = ref.watch(authProvider);
    final displayName = auth.email?.split('@').first.toUpperCase() ?? 'PLAYER';
    final wonBets = bets.where((b) => b.status == BetStatus.won).length;
    final totalBets = bets.length;
    final winRate = totalBets > 0
        ? '${(wonBets / totalBets * 100).toStringAsFixed(0)}%'
        : '0%';

    return AppShell(
      activeTab: NavTab.profile,
      scrollable: true,
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
            child:
                Image.asset(Assets.profileAvatar, fit: BoxFit.cover),
          ),
          const SizedBox(height: 12),
          Text(displayName,
              style: displayStyle(size: 40, color: Palette.gold)),
          if (auth.email != null)
            Text(auth.email!,
                style: bodyStyle(size: 14, color: Palette.secondary)),
          Text.rich(
            TextSpan(children: [
              TextSpan(
                  text: 'LVL 1',
                  style: bodyStyle(size: 18, color: Palette.white)),
              TextSpan(
                  text: ' - SERIAL GAMBLER',
                  style:
                      bodyStyle(size: 18, color: Palette.secondary)),
            ]),
          ),
          const SizedBox(height: 24),
          WalletActionWidget(
            onManageTap: () {
              showModalBottomSheet<void>(
                context: context,
                isScrollControlled: true,
                backgroundColor: Colors.transparent,
                builder: (_) => const WalletManageSheet(),
              );
            },
          ),
          const SizedBox(height: 24),
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 24),
            child: ProfileStatsWidget(
              winRate: winRate,
              totalBets: '$totalBets',
            ),
          ),
          const SizedBox(height: 24),
          Container(height: 1, color: Palette.darkGold),
          const SizedBox(height: 24),
          Text('Bet History', style: displayStyle(size: 36)),
          const SizedBox(height: 14),
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
          const SizedBox(height: 20),
          // Logout
          Pressable(
            onTap: () async {
              final confirmed = await showDialog<bool>(
                context: context,
                builder: (ctx) => AlertDialog(
                  backgroundColor: Palette.sheetBg,
                  title: Text('Log out?',
                      style: displayStyle(size: 22)),
                  content: Text('You will need to sign in again.',
                      style: bodyStyle(size: 14, color: Palette.muted)),
                  actions: [
                    TextButton(
                      onPressed: () => Navigator.pop(ctx, false),
                      child: Text('Cancel',
                          style: bodyStyle(color: Palette.muted)),
                    ),
                    TextButton(
                      onPressed: () => Navigator.pop(ctx, true),
                      child: Text('Log out',
                          style: bodyStyle(color: Palette.red)),
                    ),
                  ],
                ),
              );
              if (confirmed == true && context.mounted) {
                await ref.read(authProvider.notifier).logout();
                if (context.mounted) {
                  Navigator.of(context).pushNamedAndRemoveUntil(
                      '/sign-in-modal', (_) => false);
                }
              }
            },
            child: Text('Log out',
                style: displayStyle(size: 18, color: Palette.red)),
          ),
          const SizedBox(height: 16),
          Pressable(
            onTap: () async {
              final confirmed = await showDialog<bool>(
                context: context,
                builder: (ctx) => AlertDialog(
                  backgroundColor: Palette.sheetBg,
                  title: Text('Delete account?',
                      style: displayStyle(size: 22)),
                  content: Text(
                      'This will permanently delete your account, wallet, and all data. This cannot be undone.',
                      style: bodyStyle(size: 14, color: Palette.muted)),
                  actions: [
                    TextButton(
                      onPressed: () => Navigator.pop(ctx, false),
                      child: Text('Cancel',
                          style: bodyStyle(color: Palette.muted)),
                    ),
                    TextButton(
                      onPressed: () => Navigator.pop(ctx, true),
                      child: Text('Delete',
                          style: bodyStyle(color: Palette.red)),
                    ),
                  ],
                ),
              );
              if (confirmed == true && context.mounted) {
                await ref.read(authProvider.notifier).deleteAccount();
                if (context.mounted) {
                  Navigator.of(context).pushNamedAndRemoveUntil(
                      '/get-started', (_) => false);
                }
              }
            },
            child: Text('Delete account',
                style: bodyStyle(size: 14, color: Palette.muted)),
          ),
          const SizedBox(height: 32),
        ],
      ),
    );
  }
}
