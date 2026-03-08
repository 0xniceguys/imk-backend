import 'package:flutter/material.dart';
import 'package:flutter_svg/flutter_svg.dart';
import '../../core/palette.dart';
import '../../core/typography.dart';
import '../../core/constants.dart';
import '../../router.dart';
import 'pressable.dart';

class BottomNavWidget extends StatelessWidget {
  const BottomNavWidget({
    super.key,
    required this.active,
    required this.onTapArena,
    required this.onTapFighters,
    required this.onTapProfile,
  });

  final NavTab active;
  final VoidCallback onTapArena;
  final VoidCallback onTapFighters;
  final VoidCallback onTapProfile;

  @override
  Widget build(BuildContext context) {
    return Row(
      mainAxisAlignment: MainAxisAlignment.spaceEvenly,
      children: [
        _NavItem(
          label: 'Arena',
          icon: Assets.navArenaIcon,
          active: active == NavTab.arena,
          onTap: onTapArena,
        ),
        _NavItem(
          label: 'Fighters',
          icon: Assets.navFightersIcon,
          active: active == NavTab.fighters,
          onTap: onTapFighters,
        ),
        _NavItem(
          label: 'Profile',
          icon: Assets.navProfileIcon,
          active: active == NavTab.profile,
          onTap: onTapProfile,
        ),
      ],
    );
  }
}

class _NavItem extends StatelessWidget {
  const _NavItem({
    required this.label,
    required this.icon,
    required this.active,
    required this.onTap,
  });

  final String label;
  final String icon;
  final bool active;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return Pressable(
      onTap: onTap,
      scaleTo: 0.95,
      opacityTo: 0.6,
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          SvgPicture.asset(
            icon,
            width: 48,
            height: 48,
            colorMapper: _NavIconColorMapper(
              active ? Palette.gold : Palette.muted,
            ),
          ),
          const SizedBox(height: 4),
          AnimatedDefaultTextStyle(
            duration: const Duration(milliseconds: 200),
            style: displayStyle(
              size: 20,
              color: active ? Palette.gold : Palette.muted,
            ),
            child: Text(label),
          ),
        ],
      ),
    );
  }
}

class _NavIconColorMapper extends ColorMapper {
  const _NavIconColorMapper(this.replacement);

  final Color replacement;

  @override
  Color substitute(
    String? id,
    String elementName,
    String attributeName,
    Color color,
  ) {
    final argb = color.toARGB32();
    final rgb = argb & 0x00FFFFFF;
    // Recolor only the original gray icon strokes/fills (#888888).
    if (rgb == 0x00888888) {
      final alpha = ((argb >> 24) & 0xFF) / 255.0;
      return replacement.withValues(alpha: alpha);
    }
    return color;
  }
}
