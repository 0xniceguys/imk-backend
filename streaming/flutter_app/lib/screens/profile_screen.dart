import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:image_picker/image_picker.dart';
import 'package:path_provider/path_provider.dart';
import 'package:path/path.dart' as p;

import '../core/palette.dart';
import '../core/typography.dart';
import '../core/constants.dart';
import '../router.dart';
import '../providers/auth_provider.dart';
import '../models/bet.dart';
import '../providers/bet_provider.dart';
import '../services/api_service.dart';
import '../widgets/shared/app_shell.dart';
import '../widgets/wallet/wallet_action.dart';
import '../widgets/shared/profile_stats.dart';
import '../widgets/shared/history_card.dart';
import '../widgets/wallet/wallet_manage_sheet.dart';
import '../widgets/shared/pressable.dart';

// ── Level system (based on total bets placed) ────────────────────────────────
String _tierName(int totalBets) {
  if (totalBets < 5) return 'ROOKIE';
  if (totalBets < 15) return 'FIGHTER';
  if (totalBets < 30) return 'VETERAN';
  if (totalBets < 50) return 'CHAMPION';
  return 'LEGEND';
}

int _level(int totalBets) {
  if (totalBets < 5) return 1;
  if (totalBets < 15) return 2;
  if (totalBets < 30) return 3;
  if (totalBets < 50) return 4;
  return 5;
}

// ── P&L summary provider ─────────────────────────────────────────────────────
final betsSummaryProvider = FutureProvider<Map<String, dynamic>?>((ref) async {
  final api = ref.read(apiServiceProvider);
  return api.fetchBetsSummary();
});

class ProfileScreen extends ConsumerStatefulWidget {
  const ProfileScreen({super.key, required this.onNavigate});
  final void Function(String) onNavigate;

  @override
  ConsumerState<ProfileScreen> createState() => _ProfileScreenState();
}

class _ProfileScreenState extends ConsumerState<ProfileScreen> {
  bool _pickingAvatar = false;

  // ── Avatar picker ──
  Future<void> _pickAvatar() async {
    if (_pickingAvatar) return;
    setState(() => _pickingAvatar = true);
    try {
      final picker = ImagePicker();
      final picked = await picker.pickImage(
        source: ImageSource.gallery,
        maxWidth: 512,
        maxHeight: 512,
        imageQuality: 80,
      );
      if (picked == null) return;

      // Copy to app documents dir so it persists
      final dir = await getApplicationDocumentsDirectory();
      final dest = p.join(dir.path, 'avatar.jpg');
      await File(picked.path).copy(dest);
      if (mounted) {
        await ref.read(authProvider.notifier).updateAvatar(dest);
      }
    } finally {
      if (mounted) setState(() => _pickingAvatar = false);
    }
  }

  // ── Display name edit dialog ──
  Future<void> _editDisplayName(String current) async {
    final ctrl = TextEditingController(text: current);
    final result = await showDialog<String>(
      context: context,
      builder: (ctx) => AlertDialog(
        backgroundColor: Palette.sheetBg,
        title: Text('Set Display Name', style: displayStyle(size: 20)),
        content: TextField(
          controller: ctrl,
          autofocus: true,
          maxLength: 30,
          style: bodyStyle(size: 16),
          decoration: InputDecoration(
            hintText: 'Your name…',
            hintStyle: bodyStyle(size: 16, color: Palette.muted),
            enabledBorder: const UnderlineInputBorder(
              borderSide: BorderSide(color: Palette.border),
            ),
            focusedBorder: const UnderlineInputBorder(
              borderSide: BorderSide(color: Palette.gold),
            ),
          ),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx),
            child: Text('Cancel', style: bodyStyle(color: Palette.muted)),
          ),
          TextButton(
            onPressed: () => Navigator.pop(ctx, ctrl.text.trim()),
            child: Text('Save', style: bodyStyle(color: Palette.gold)),
          ),
        ],
      ),
    );
    if (result != null && result.isNotEmpty && mounted) {
      final ok = await ref.read(authProvider.notifier).updateDisplayName(result);
      if (mounted && !ok) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('Failed to save — check your connection')),
        );
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final auth = ref.watch(authProvider);
    final bets = ref.watch(betProvider);
    final summaryAsync = ref.watch(betsSummaryProvider);

    final totalBets = bets.length;
    final level = _level(totalBets);
    final tier = _tierName(totalBets);
    final displayName = auth.displayName ??
        auth.email?.split('@').first.toUpperCase() ??
        'PLAYER';

    // Avatar image provider
    final ImageProvider avatarImage = auth.avatarPath != null &&
            File(auth.avatarPath!).existsSync()
        ? FileImage(File(auth.avatarPath!))
        : const AssetImage(Assets.profileAvatar) as ImageProvider;

    return AppShell(
      activeTab: NavTab.profile,
      scrollable: true,
      onNavigate: (slug) => widget.onNavigate(routeFor(slug)),
      content: Column(
        children: [
          const SizedBox(height: 8),

          // ── Avatar (tappable) ─────────────────────────────────────────────
          Pressable(
            onTap: _pickAvatar,
            scaleTo: 0.95,
            child: Stack(
              alignment: Alignment.bottomRight,
              children: [
                Container(
                  width: 120,
                  height: 120,
                  decoration: BoxDecoration(
                    border: Border.all(color: Palette.gold, width: 4),
                    shape: BoxShape.circle,
                  ),
                  clipBehavior: Clip.antiAlias,
                  child: _pickingAvatar
                      ? const Center(child: CircularProgressIndicator(color: Palette.gold, strokeWidth: 2))
                      : Image(image: avatarImage, fit: BoxFit.cover),
                ),
                Container(
                  width: 32,
                  height: 32,
                  decoration: BoxDecoration(
                    color: Palette.gold,
                    shape: BoxShape.circle,
                    border: Border.all(color: Palette.black, width: 2),
                  ),
                  child: const Icon(Icons.camera_alt, size: 16, color: Palette.black),
                ),
              ],
            ),
          ),
          const SizedBox(height: 12),

          // ── Display name (tappable) ───────────────────────────────────────
          Pressable(
            onTap: () => _editDisplayName(displayName),
            child: Row(
              mainAxisSize: MainAxisSize.min,
              children: [
                Text(displayName,
                    style: displayStyle(size: 36, color: Palette.gold)),
                const SizedBox(width: 8),
                const Icon(Icons.edit, size: 16, color: Palette.muted),
              ],
            ),
          ),

          if (auth.email != null)
            Text(auth.email!,
                style: bodyStyle(size: 13, color: Palette.secondary)),

          // ── Level badge ───────────────────────────────────────────────────
          const SizedBox(height: 6),
          Container(
            padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 4),
            decoration: BoxDecoration(
              border: Border.all(color: Palette.gold.withValues(alpha: 0.4)),
              borderRadius: BorderRadius.circular(20),
            ),
            child: Text.rich(
              TextSpan(children: [
                TextSpan(
                    text: 'LVL $level',
                    style: bodyStyle(
                        size: 15,
                        color: Palette.gold,
                        weight: FontWeight.w700)),
                TextSpan(
                    text: '  ·  $tier',
                    style: bodyStyle(size: 15, color: Palette.secondary)),
              ]),
            ),
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
              winRate: totalBets > 0
                  ? '${(bets.where((b) => b.status == BetStatus.won).length / totalBets * 100).toStringAsFixed(0)}%'
                  : '0%',
              totalBets: '$totalBets',
            ),
          ),

          // ── P&L Summary bar ───────────────────────────────────────────────
          const SizedBox(height: 20),
          summaryAsync.when(
            data: (summary) {
              if (summary == null) return const SizedBox.shrink();
              final wagered = (summary['total_wagered'] as num?)?.toDouble() ?? 0;
              final netPnl = (summary['net_pnl'] as num?)?.toDouble() ?? 0;
              final netColor = netPnl >= 0 ? Palette.green : Palette.red;
              final netSign = netPnl >= 0 ? '+' : '';
              return Container(
                margin: const EdgeInsets.symmetric(horizontal: 24),
                padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
                decoration: BoxDecoration(
                  border: Border.all(color: Palette.border),
                  borderRadius: BorderRadius.circular(8),
                ),
                child: Row(
                  mainAxisAlignment: MainAxisAlignment.spaceAround,
                  children: [
                    _PnLStat(label: 'Bets', value: '${summary['total_bets'] ?? 0}', color: Palette.white),
                    Container(width: 1, height: 32, color: Palette.border),
                    _PnLStat(label: 'Wagered', value: '${wagered.toStringAsFixed(2)} SOL', color: Palette.white),
                    Container(width: 1, height: 32, color: Palette.border),
                    _PnLStat(label: 'Net P&L', value: '$netSign${netPnl.toStringAsFixed(2)} SOL', color: netColor),
                  ],
                ),
              );
            },
            loading: () => const SizedBox.shrink(),
            error: (_, __) => const SizedBox.shrink(),
          ),

          const SizedBox(height: 24),
          Container(height: 1, color: Palette.darkGold),
          const SizedBox(height: 24),
          Text('Bet History', style: displayStyle(size: 32)),
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
                onTap: () => widget.onNavigate('/battle-detail'),
              ),
              const SizedBox(height: 14),
            ],

          const SizedBox(height: 20),

          // ── Logout ────────────────────────────────────────────────────────
          Pressable(
            onTap: () async {
              final confirmed = await showDialog<bool>(
                context: context,
                builder: (ctx) => AlertDialog(
                  backgroundColor: Palette.sheetBg,
                  title: Text('Log out?', style: displayStyle(size: 22)),
                  content: Text('You will need to sign in again.',
                      style: bodyStyle(size: 14, color: Palette.muted)),
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
            },
            child: Text('Log out', style: displayStyle(size: 18, color: Palette.red)),
          ),
          const SizedBox(height: 16),
          Pressable(
            onTap: () async {
              final confirmed = await showDialog<bool>(
                context: context,
                builder: (ctx) => AlertDialog(
                  backgroundColor: Palette.sheetBg,
                  title: Text('Delete account?', style: displayStyle(size: 22)),
                  content: Text(
                      'This will permanently delete your account, wallet, and all data. This cannot be undone.',
                      style: bodyStyle(size: 14, color: Palette.muted)),
                  actions: [
                    TextButton(
                      onPressed: () => Navigator.pop(ctx, false),
                      child: Text('Cancel', style: bodyStyle(color: Palette.muted)),
                    ),
                    TextButton(
                      onPressed: () => Navigator.pop(ctx, true),
                      child: Text('Delete', style: bodyStyle(color: Palette.red)),
                    ),
                  ],
                ),
              );
              if (confirmed == true && context.mounted) {
                await ref.read(authProvider.notifier).deleteAccount();
                if (context.mounted) {
                  Navigator.of(context)
                      .pushNamedAndRemoveUntil('/get-started', (_) => false);
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

// ── Small P&L stat widget ─────────────────────────────────────────────────────
class _PnLStat extends StatelessWidget {
  const _PnLStat({required this.label, required this.value, required this.color});
  final String label;
  final String value;
  final Color color;

  @override
  Widget build(BuildContext context) {
    return Column(
      children: [
        Text(value, style: bodyStyle(size: 14, color: color, weight: FontWeight.w600)),
        const SizedBox(height: 2),
        Text(label, style: bodyStyle(size: 11, color: Palette.muted)),
      ],
    );
  }
}
