import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_svg/flutter_svg.dart';
import 'package:intl/intl.dart';
import '../../core/palette.dart';
import '../../core/typography.dart';
import '../../core/constants.dart';
import '../../providers/wallet_provider.dart';
import '../shared/ornate_button.dart';
import '../shared/pressable.dart';
import 'wallet_manage_sheet.dart';

enum WalletActionStyle { ornate, compact }

class WalletActionWidget extends ConsumerWidget {
  const WalletActionWidget({
    super.key,
    this.style = WalletActionStyle.ornate,
  });

  final WalletActionStyle style;

  static final _usd = NumberFormat.currency(symbol: '\$', decimalDigits: 2);

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final wallet = ref.watch(walletProvider);
    final total = _usd.format(wallet.totalUsdValue);

    if (style == WalletActionStyle.compact) {
      return Pressable(
        onTap: () => showWalletManageSheet(context),
        scaleTo: 0.96,
        opacityTo: 0.8,
        child: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            SvgPicture.asset(
              Assets.moneyIcon,
              width: 24,
              height: 24,
              colorFilter: const ColorFilter.mode(
                Palette.gold,
                BlendMode.srcIn,
              ),
            ),
            const SizedBox(width: 7),
            Text(
              total,
              style: displayStyle(
                size: 22,
                color: Palette.muted,
                letterSpacing: -0.66,
                height: 0.98,
              ),
            ),
          ],
        ),
      );
    }

    return OrnateButton(
      label: total,
      color: Palette.gold,
      onTap: () => showWalletManageSheet(context),
    );
  }
}
