import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:intl/intl.dart';
import '../../core/palette.dart';
import '../../core/typography.dart';
import '../../core/constants.dart';
import '../../providers/wallet_provider.dart';
import '../shared/pressable.dart';

class WalletActionWidget extends ConsumerWidget {
  const WalletActionWidget({super.key, this.onManageTap});

  final VoidCallback? onManageTap;

  static final _usd = NumberFormat.currency(symbol: '\$', decimalDigits: 2);

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final wallet = ref.watch(walletProvider);
    return SizedBox(
      width: 260,
      child: Column(
        children: [
          Image.asset(Assets.ctaTop,
              width: 260, height: 8, fit: BoxFit.cover),
          const SizedBox(height: 12),
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 14),
            child: Row(
              mainAxisAlignment: MainAxisAlignment.spaceBetween,
              children: [
                Text(_usd.format(wallet.totalUsdValue),
                    style: displayStyle(size: 22, color: Palette.gold)),
                Pressable(
                  onTap: onManageTap,
                  child: Container(
                    padding: const EdgeInsets.symmetric(
                        horizontal: 12, vertical: 6),
                    decoration: BoxDecoration(
                      border: Border.all(color: Palette.border),
                      borderRadius: BorderRadius.circular(4),
                    ),
                    child: Text('Manage Wallet',
                        style: bodyStyle(size: 14)),
                  ),
                ),
              ],
            ),
          ),
          const SizedBox(height: 12),
          Image.asset(Assets.ctaBottom,
              width: 260, height: 8, fit: BoxFit.cover),
        ],
      ),
    );
  }
}
