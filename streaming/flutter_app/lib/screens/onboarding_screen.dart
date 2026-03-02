import 'package:flutter/material.dart';
import '../core/palette.dart';
import '../core/typography.dart';
import '../core/constants.dart';
import '../widgets/shared/ornate_button.dart';

/// Single swipeable onboarding flow (pages 1→2→3).
/// Used from a single route '/onboarding'.
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

  @override
  void initState() {
    super.initState();
    debugPrint('[Screen] ONBOARDING initState');
  }

  static const _pages = [
    _PageData(
      title: 'WHAT IS IMMORTAL KOMBAT?',
      body:
          'LLMs have been compared all the time, but this time we make humans bet on their skills.',
      image: Assets.onboardingOne,
      glow: Assets.onboardingGlowOne,
      cta: 'Continue',
    ),
    _PageData(
      title: 'ERA OF UNFIXABLE ESPORTS.',
      body:
          "Humans have had a good history of fixing matches, but sentient beings don't sell out.",
      image: Assets.onboardingTwo,
      glow: Assets.onboardingGlowTwo,
      cta: 'Continue',
    ),
    _PageData(
      title: 'PURE FATALITY CHAOS.',
      body:
          'Humans also have their limitations, artificial intelligence does not. True fatality achieved.',
      image: Assets.onboardingThree,
      glow: Assets.onboardingGlowThree,
      cta: 'Take my money',
    ),
  ];

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  void _next() {
    if (_page < _pages.length - 1) {
      _controller.nextPage(
        duration: const Duration(milliseconds: 350),
        curve: Curves.easeInOut,
      );
    } else {
      widget.onNavigate('/arena-list');
    }
  }

  @override
  Widget build(BuildContext context) {
    return Stack(
      fit: StackFit.expand,
      children: [
        // Swipeable pages
        PageView.builder(
          controller: _controller,
          itemCount: _pages.length,
          onPageChanged: (i) => setState(() => _page = i),
          itemBuilder: (context, index) =>
              _OnboardingPage(data: _pages[index]),
        ),
        // Controls overlay
        SafeArea(
          child: Column(
            children: [
              const SizedBox(height: 16),
              // Step indicator
              Row(
                mainAxisAlignment: MainAxisAlignment.center,
                children: [
                  Image.asset(Assets.stepperLeft, width: 84, height: 14),
                  SizedBox(
                    width: 48,
                    height: 48,
                    child: Stack(
                      alignment: Alignment.center,
                      children: [
                        Image.asset(Assets.stepperFrame,
                            width: 48, height: 48),
                        Text(
                          '${_page + 1}',
                          style: displayStyle(
                            size: 22,
                            color: Palette.white.withValues(alpha: 0.7),
                          ),
                        ),
                      ],
                    ),
                  ),
                  Image.asset(Assets.stepperRight, width: 84, height: 14),
                ],
              ),
              const SizedBox(height: 16),
              // Dot indicators
              Row(
                mainAxisAlignment: MainAxisAlignment.center,
                children: List.generate(3, (i) {
                  return AnimatedContainer(
                    duration: const Duration(milliseconds: 250),
                    width: i == _page ? 20 : 8,
                    height: 8,
                    margin: const EdgeInsets.symmetric(horizontal: 3),
                    decoration: BoxDecoration(
                      borderRadius: BorderRadius.circular(4),
                      color: i == _page ? Palette.gold : Palette.muted,
                    ),
                  );
                }),
              ),
              const Spacer(),
              OrnateButton(label: _pages[_page].cta, onTap: _next),
              if (_page < _pages.length - 1) ...[
                const SizedBox(height: 18),
                TextButton(
                  onPressed: () => widget.onNavigate('/arena-list'),
                  style: plainBtn(),
                  child: Text('Skip',
                      style: displayStyle(size: 18, color: Palette.muted)),
                ),
              ],
              const SizedBox(height: 40),
            ],
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
    required this.glow,
    required this.cta,
  });

  final String title;
  final String body;
  final String image;
  final String glow;
  final String cta;
}

class _OnboardingPage extends StatelessWidget {
  const _OnboardingPage({required this.data});
  final _PageData data;

  @override
  Widget build(BuildContext context) {
    final screenH = MediaQuery.of(context).size.height;

    return Stack(
      fit: StackFit.expand,
      children: [
        Positioned(
          left: 0,
          right: 0,
          bottom: 0,
          height: screenH * 0.6,
          child: Container(
            decoration: const BoxDecoration(
              gradient: LinearGradient(
                begin: Alignment.topCenter,
                end: Alignment.bottomCenter,
                colors: [Colors.transparent, Colors.black],
                stops: [0, 0.625],
              ),
            ),
          ),
        ),
        Positioned(
          left: 32,
          right: 32,
          bottom: 0,
          child: Image.asset(data.glow,
              height: screenH * 0.55, fit: BoxFit.contain),
        ),
        Positioned(
          left: 0,
          right: 0,
          bottom: -screenH * 0.57,
          child: SizedBox(
            height: screenH * 1.05,
            width: screenH * 0.75,
            child: Image.asset(data.image, fit: BoxFit.fitHeight),
          ),
        ),
        SafeArea(
          child: Padding(
            padding: const EdgeInsets.only(top: 104),
            child: Column(
              children: [
                Padding(
                  padding: const EdgeInsets.symmetric(horizontal: 48),
                  child: Text(
                    data.title,
                    textAlign: TextAlign.center,
                    style: displayStyle(
                      size: 36,
                      color: Palette.gold,
                      letterSpacing: -1.08,
                      height: 0.9,
                    ),
                  ),
                ),
                const SizedBox(height: 24),
                Padding(
                  padding: const EdgeInsets.symmetric(horizontal: 48),
                  child: Text(
                    data.body,
                    textAlign: TextAlign.center,
                    style: bodyStyle(
                        size: 20,
                        color: Palette.muted),
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
