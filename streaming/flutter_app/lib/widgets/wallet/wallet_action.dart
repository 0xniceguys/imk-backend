import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:intl/intl.dart';
import '../../core/palette.dart';
import '../../providers/wallet_provider.dart';
import '../shared/ornate_button.dart';

class WalletActionWidget extends ConsumerWidget {
  const WalletActionWidget({super.key, this.onManageTap});

  final VoidCallback? onManageTap;

  static final _usd = NumberFormat.currency(symbol: '\$', decimalDigits: 2);

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final wallet = ref.watch(walletProvider);
    return OrnateButton(
      label: _usd.format(wallet.totalUsdValue),
      color: Palette.gold,
      onTap: onManageTap ?? () {},
    );
  }
}
