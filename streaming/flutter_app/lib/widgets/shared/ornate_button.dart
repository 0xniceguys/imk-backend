import 'package:flutter/material.dart';
import 'package:flutter_svg/flutter_svg.dart';
import '../../core/palette.dart';
import '../../core/typography.dart';
import '../../core/constants.dart';
import 'pressable.dart';

class OrnateButton extends StatelessWidget {
  const OrnateButton({
    super.key,
    required this.label,
    required this.onTap,
    this.color = Palette.white,
  });

  final String label;
  final VoidCallback onTap;
  final Color color;

  @override
  Widget build(BuildContext context) {
    return Pressable(
      onTap: onTap,
      scaleTo: 0.94,
      haptic: true,
      child: SizedBox(
        width: 250,
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
            const SizedBox(height: 11),
            Text(label, style: displayStyle(size: 20, color: color)),
            const SizedBox(height: 11),
            SvgPicture.asset(
              Assets.ornateTrial,
              width: 250,
              height: 8,
              fit: BoxFit.fill,
            ),
          ],
        ),
      ),
    );
  }
}
