import 'package:flutter/material.dart';
import '../core/constants.dart';
import '../core/palette.dart';
import '../core/typography.dart';
import '../widgets/shared/ik_loader.dart';
import '../widgets/shared/ornate_button.dart';
import '../widgets/shared/pressable.dart';

class OnboardingScreen extends StatefulWidget {
  const OnboardingScreen({
    super.key,
    required this.onNavigate,
  });

  final void Function(String) onNavigate;

  @override
  State<OnboardingScreen> createState() => _OnboardingScreenState();
}

class _OnboardingScreenState extends State<OnboardingScreen> {
  final _controller = PageController();
  int _page = 0;
  bool _isNavigating = false;

  static const _pages = [
    _PageData(
      title: 'WATCH AI FIGHTS LIVE',
      body: 'Every match is real-time. Read momentum fast and pick your side.',
      image: Assets.characterScorpio,
      cta: 'Next',
    ),
    _PageData(
      title: 'BET IN SKR',
      body: 'Choose a fighter and lock your bet before the match closes.',
      image: Assets.characterCage,
      cta: 'Next',
    ),
    _PageData(
      title: 'TRACK AND CLAIM',
      body: 'Review outcomes, claim rewards, and jump into the next arena.',
      image: Assets.characterSonya,
      cta: 'Enter Arena',
    ),
  ];

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  void _next() {
    if (_isNavigating) return;
    if (_page < _pages.length - 1) {
      _controller.nextPage(
        duration: const Duration(milliseconds: 320),
        curve: Curves.easeOutCubic,
      );
    } else {
      _goToArena();
    }
  }

  void _goToArena() {
    if (_isNavigating) return;
    setState(() => _isNavigating = true);
    widget.onNavigate('/arena-list');
  }

  @override
  Widget build(BuildContext context) {
    final current = _pages[_page];

    return Stack(
      fit: StackFit.expand,
      children: [
        PageView.builder(
          controller: _controller,
          itemCount: _pages.length,
          onPageChanged: (index) => setState(() => _page = index),
          itemBuilder: (context, index) => _OnboardingPage(data: _pages[index]),
        ),
        SafeArea(
          child: Padding(
            padding: const EdgeInsets.symmetric(horizontal: 20),
            child: Column(
              children: [
                const SizedBox(height: 18),
                Row(
                  children: [
                    ...List.generate(
                      _pages.length,
                      (index) => Container(
                        width: 4,
                        height: 4,
                        margin: const EdgeInsets.symmetric(horizontal: 2),
                        decoration: BoxDecoration(
                          shape: BoxShape.circle,
                          color: index == _page
                              ? Palette.gold
                              : const Color(0xFF555555),
                        ),
                      ),
                    ),
                    const Spacer(),
                    Pressable(
                      onTap: _goToArena,
                      opacityTo: 0.75,
                      child: Text(
                        'Skip',
                        style: displayStyle(size: 18, color: Palette.muted),
                      ),
                    ),
                  ],
                ),
                const SizedBox(height: 28),
                Padding(
                  padding: const EdgeInsets.symmetric(horizontal: 14),
                  child: Column(
                    children: [
                      Text(
                        current.title,
                        textAlign: TextAlign.center,
                        style: displayStyle(
                          size: 32,
                          color: Palette.gold,
                          height: 0.92,
                          letterSpacing: -0.9,
                        ),
                      ),
                      const SizedBox(height: 12),
                      Text(
                        current.body,
                        textAlign: TextAlign.center,
                        style: bodyStyle(
                          size: 16,
                          color: Palette.secondary,
                          height: 1.25,
                        ),
                      ),
                    ],
                  ),
                ),
                const Spacer(),
                const SizedBox(height: 20),
                if (_isNavigating)
                  const IKLoader(size: 26)
                else
                  OrnateButton(label: current.cta, onTap: _next),
                const SizedBox(height: 28),
              ],
            ),
          ),
        ),
      ],
    );
  }
}

class _PageData {
  const _PageData({
    required this.title,
    required this.body,
    required this.image,
    required this.cta,
  });

  final String title;
  final String body;
  final String image;
  final String cta;
}

class _OnboardingPage extends StatelessWidget {
  const _OnboardingPage({required this.data});

  final _PageData data;

  @override
  Widget build(BuildContext context) {
    final screen = MediaQuery.of(context).size;

    return Stack(
      fit: StackFit.expand,
      children: [
        const ColoredBox(color: Palette.black),
        Positioned(
          left: 0,
          right: 0,
          bottom: -screen.height * 0.08,
          child: IgnorePointer(
            child: Image.asset(
              data.image,
              height: screen.height * 0.66,
              fit: BoxFit.contain,
            ),
          ),
        ),
        Positioned(
          left: 0,
          right: 0,
          bottom: 0,
          child: IgnorePointer(
            child: Container(
              height: screen.height * 0.35,
              decoration: BoxDecoration(
                gradient: LinearGradient(
                  begin: Alignment.bottomCenter,
                  end: Alignment.topCenter,
                  colors: [
                    Palette.black.withValues(alpha: 0.95),
                    Palette.black.withValues(alpha: 0.72),
                    Colors.transparent,
                  ],
                ),
              ),
            ),
          ),
        ),
      ],
    );
  }
}
