import 'package:flutter/material.dart';
import 'package:flutter_svg/flutter_svg.dart';
import '../../core/palette.dart';
import '../../core/typography.dart';
import '../../core/constants.dart';
import 'pressable.dart';
import '../wallet/wallet_action.dart';

class HeaderWidget extends StatelessWidget {
  const HeaderWidget({
    super.key,
    this.trailing,
  });

  final Widget? trailing;

  @override
  Widget build(BuildContext context) {
    final mainTrailing =
        trailing ?? const WalletActionWidget(style: WalletActionStyle.compact);
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
          mainAxisSize: MainAxisSize.min,
          children: [
            if (kOnboardingDebugTapAvailable) ...[
              Pressable(
                onTap: () => Navigator.of(context).pushNamed('/onboarding'),
                opacityTo: 0.8,
                child: SvgPicture.asset(
                  Assets.skullIcon,
                  width: 20,
                  height: 20,
                  colorFilter: const ColorFilter.mode(
                    Palette.gold,
                    BlendMode.srcIn,
                  ),
                ),
              ),
              const SizedBox(width: 10),
            ],
            mainTrailing,
          ],
        ),
      ],
    );
  }
}
