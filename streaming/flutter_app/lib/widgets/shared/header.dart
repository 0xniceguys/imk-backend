import 'package:flutter/material.dart';
import 'package:flutter_svg/flutter_svg.dart';
import '../../core/palette.dart';
import '../../core/typography.dart';
import '../../core/constants.dart';
import '../wallet/wallet_action.dart';

class HeaderWidget extends StatelessWidget {
  const HeaderWidget({
    super.key,
    this.trailing,
  });

  final Widget? trailing;

  @override
  Widget build(BuildContext context) {
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
        trailing ?? const WalletActionWidget(style: WalletActionStyle.compact),
      ],
    );
  }
}
