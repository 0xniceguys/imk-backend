import 'package:flutter/material.dart';
import '../../router.dart';
import 'header.dart';
import 'bottom_nav.dart';

class AppShell extends StatelessWidget {
  const AppShell({
    super.key,
    required this.activeTab,
    required this.content,
    this.scrollable = false,
    required this.onNavigate,
  });

  final NavTab activeTab;
  final Widget content;
  final bool scrollable;
  final void Function(ScreenSlug) onNavigate;

  @override
  Widget build(BuildContext context) {
    final top = MediaQuery.of(context).padding.top;
    final bottom = MediaQuery.of(context).padding.bottom;
    return Column(
      children: [
        SizedBox(height: top + 22),
        const Padding(
          padding: EdgeInsets.symmetric(horizontal: 26),
          child: HeaderWidget(),
        ),
        const SizedBox(height: 20),
        Expanded(
          child: scrollable
              ? SingleChildScrollView(child: content)
              : content,
        ),
        const SizedBox(height: 12),
        BottomNavWidget(
          active: activeTab,
          onTapArena: () => onNavigate(ScreenSlug.arenaList),
          onTapFighters: () => onNavigate(ScreenSlug.fighterOverview),
          onTapProfile: () => onNavigate(ScreenSlug.profile),
        ),
        SizedBox(height: bottom > 0 ? bottom : 12),
      ],
    );
  }
}
