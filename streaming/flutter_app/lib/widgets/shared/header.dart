import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_svg/flutter_svg.dart';
import 'package:intl/intl.dart';
import '../../core/palette.dart';
import '../../core/typography.dart';
import '../../core/constants.dart';
import '../../providers/wallet_provider.dart';

class HeaderWidget extends ConsumerWidget {
  const HeaderWidget({super.key});

  static final _usd = NumberFormat.currency(symbol: '\$', decimalDigits: 2);

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final wallet = ref.watch(walletProvider);
    return Row(
      mainAxisAlignment: MainAxisAlignment.spaceBetween,
      children: [
        Row(
          children: [
            SvgPicture.asset(Assets.skullIcon, width: 32, height: 32),
            Text(
              'IMK',
              style: displayStyle(
                size: 29,
                color: Palette.gold,
                letterSpacing: -0.87,
                height: 0.98,
              ),
            ),
          ],
        ),
        Row(
          children: [
            Image.asset(Assets.balanceIcon, width: 24, height: 24),
            const SizedBox(width: 7),
            Text(
              _usd.format(wallet.totalUsdValue),
              style: displayStyle(
                size: 22,
                color: Palette.muted,
                letterSpacing: -0.66,
                height: 0.98,
              ),
            ),
          ],
        ),
      ],
    );
  }
}
