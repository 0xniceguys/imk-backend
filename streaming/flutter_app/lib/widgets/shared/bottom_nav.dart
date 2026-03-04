import 'package:flutter/material.dart';
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
          icon: active == NavTab.arena
              ? Assets.navActiveArena
              : Assets.navInactiveArena,
          active: active == NavTab.arena,
          onTap: onTapArena,
        ),
        _NavItem(
          label: 'Fighters',
          icon: active == NavTab.fighters
              ? Assets.navActiveFighters
              : Assets.navInactiveFighters,
          active: active == NavTab.fighters,
          onTap: onTapFighters,
        ),
        _NavItem(
          label: 'Profile',
          icon: Assets.navInactiveProfile,
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
          Image.asset(icon, width: 48, height: 48),
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
