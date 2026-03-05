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
    this.contentBottomPadding = 0,
    required this.onNavigate,
  });

  final NavTab activeTab;
  final Widget content;
  final bool scrollable;
  final double contentBottomPadding;
  final void Function(ScreenSlug) onNavigate;
  static const _navBottomInset = 30.0;
  static const _navGradientHeight = 200.0;

  @override
  Widget build(BuildContext context) {
    final top = MediaQuery.of(context).padding.top;
    final bottom = MediaQuery.of(context).padding.bottom;
    return Stack(
      children: [
        Column(
          children: [
            SizedBox(height: top + 22),
            const Padding(
              padding: EdgeInsets.symmetric(horizontal: 26),
              child: HeaderWidget(),
            ),
            const SizedBox(height: 20),
            Expanded(
              child: scrollable
                  ? SingleChildScrollView(
                      padding: EdgeInsets.only(bottom: contentBottomPadding),
                      child: content,
                    )
                  : Padding(
                      padding: EdgeInsets.only(bottom: contentBottomPadding),
                      child: content,
                    ),
            ),
          ],
        ),
        Positioned(
          left: 0,
          right: 0,
          bottom: 0,
          child: SizedBox(
            height: _navGradientHeight + bottom,
            child: Stack(
              children: [
                const Positioned.fill(
                  child: IgnorePointer(
                    child: DecoratedBox(
                      decoration: BoxDecoration(
                        gradient: LinearGradient(
                          begin: Alignment.topCenter,
                          end: Alignment.bottomCenter,
                          colors: [
                            Color(0x00000000),
                            Color(0xFF000000),
                          ],
                          stops: [0.0, 0.5],
                        ),
                      ),
                    ),
                  ),
                ),
                Positioned(
                  left: 0,
                  right: 0,
                  bottom: _navBottomInset,
                  child: BottomNavWidget(
                    active: activeTab,
                    onTapArena: () => onNavigate(ScreenSlug.arenaList),
                    onTapFighters: () => onNavigate(ScreenSlug.fighterOverview),
                    onTapProfile: () => onNavigate(ScreenSlug.profile),
                  ),
                ),
              ],
            ),
          ),
        ),
      ],
    );
  }
}
