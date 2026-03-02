import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../core/palette.dart';
import '../core/typography.dart';
import '../core/constants.dart';
import '../providers/auth_provider.dart';
import '../widgets/shared/ornate_button.dart';

class GetStartedScreen extends ConsumerStatefulWidget {
  const GetStartedScreen({super.key, required this.onNavigate});
  final void Function(String) onNavigate;

  @override
  ConsumerState<GetStartedScreen> createState() => _GetStartedScreenState();
}

class _GetStartedScreenState extends ConsumerState<GetStartedScreen>
    with TickerProviderStateMixin {
  late final AnimationController _ctrl;
  late final Animation<double> _bgFade;
  late final Animation<double> _heroSlide;
  late final Animation<double> _logoFade;
  late final Animation<double> _taglineFade;
  late final Animation<double> _ctaFade;

  @override
  void initState() {
    super.initState();
    debugPrint('[Screen] GET_STARTED initState');
    _ctrl = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 1400),
    );

    // Staggered entrance: bg → hero → logo → tagline → CTA
    _bgFade = Tween<double>(begin: 0, end: 0.15).animate(
      CurvedAnimation(parent: _ctrl, curve: const Interval(0, 0.3)),
    );
    _heroSlide = Tween<double>(begin: 60, end: 0).animate(
      CurvedAnimation(
          parent: _ctrl,
          curve: const Interval(0.1, 0.5, curve: Curves.easeOutCubic)),
    );
    _logoFade = CurvedAnimation(
        parent: _ctrl,
        curve: const Interval(0.2, 0.5, curve: Curves.easeOut));
    _taglineFade = CurvedAnimation(
        parent: _ctrl,
        curve: const Interval(0.35, 0.65, curve: Curves.easeOut));
    _ctaFade = CurvedAnimation(
        parent: _ctrl,
        curve: const Interval(0.55, 0.85, curve: Curves.easeOut));

    _ctrl.forward();
  }

  @override
  void dispose() {
    _ctrl.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final screenH = MediaQuery.of(context).size.height;
    // Scale hero to fill ~90% of screen height, keep original aspect ratio
    final heroHeight = screenH * 0.88;
    final heroWidth = heroHeight * (386 / 686); // maintain aspect ratio

    return AnimatedBuilder(
      animation: _ctrl,
      builder: (context, _) => Container(
        color: Palette.black,
        child: Stack(
          fit: StackFit.expand,
          children: [
            // Subtle background texture
            Positioned.fill(
              child: Opacity(
                opacity: _bgFade.value,
                child: Image.asset(Assets.startBg, fit: BoxFit.cover),
              ),
            ),
            // Hero character — left-aligned, large, anchored to bottom
            Positioned(
              left: -heroWidth * 0.08,
              bottom: -20 + _heroSlide.value,
              child: Opacity(
                opacity: (_ctrl.value * 2.5).clamp(0.0, 1.0),
                child: Image.asset(
                  Assets.startHero,
                  width: heroWidth,
                  height: heroHeight,
                  fit: BoxFit.contain,
                ),
              ),
            ),
            // Top gradient — ensures logo/tagline readability
            Positioned(
              top: 0,
              left: 0,
              right: 0,
              height: screenH * 0.35,
              child: IgnorePointer(
                child: DecoratedBox(
                  decoration: BoxDecoration(
                    gradient: LinearGradient(
                      begin: Alignment.topCenter,
                      end: Alignment.bottomCenter,
                      colors: [
                        Palette.black.withValues(alpha: 0.9),
                        Palette.black.withValues(alpha: 0.6),
                        Colors.transparent,
                      ],
                      stops: const [0.0, 0.5, 1.0],
                    ),
                  ),
                ),
              ),
            ),
            // Bottom gradient — ensures CTA readability
            Positioned(
              bottom: 0,
              left: 0,
              right: 0,
              height: screenH * 0.35,
              child: IgnorePointer(
                child: DecoratedBox(
                  decoration: BoxDecoration(
                    gradient: LinearGradient(
                      begin: Alignment.bottomCenter,
                      end: Alignment.topCenter,
                      colors: [
                        Palette.black.withValues(alpha: 0.95),
                        Palette.black.withValues(alpha: 0.7),
                        Colors.transparent,
                      ],
                      stops: const [0.0, 0.55, 1.0],
                    ),
                  ),
                ),
              ),
            ),
            // Content overlay
            SafeArea(
              child: Padding(
                padding: const EdgeInsets.symmetric(horizontal: 32),
                child: Column(
                children: [
                  const SizedBox(height: 24),
                  // Logo fades + slides in
                  FadeTransition(
                    opacity: _logoFade,
                    child: SlideTransition(
                      position: Tween<Offset>(
                        begin: const Offset(0, -0.15),
                        end: Offset.zero,
                      ).animate(_logoFade),
                      child: Image.asset(Assets.logoVector, width: 170),
                    ),
                  ),
                  const SizedBox(height: 8),
                  // Tagline fades in
                  FadeTransition(
                    opacity: _taglineFade,
                    child: Text(
                      'LLMs train Agents. Agents fight.\nHumans bet on Solana.',
                      textAlign: TextAlign.center,
                      style: displayStyle(
                          size: 18, color: Palette.muted, letterSpacing: -0.5),
                    ),
                  ),
                  const Spacer(),
                  // CTA slides up + fades in
                  FadeTransition(
                    opacity: _ctaFade,
                    child: SlideTransition(
                      position: Tween<Offset>(
                        begin: const Offset(0, 0.4),
                        end: Offset.zero,
                      ).animate(_ctaFade),
                      child: Column(
                        children: [
                          OrnateButton(
                            label: 'Get started',
                            onTap: () {
                              ref.read(authProvider.notifier).markIntroSeen();
                              widget.onNavigate('/sign-in-modal');
                            },
                          ),
                          const SizedBox(height: 6),
                          Text(
                            'By continuing you accept the terms and\nconditions and privacy policy.',
                            textAlign: TextAlign.center,
                            style: bodyStyle(
                                size: 12,
                                color: Palette.muted),
                          ),
                        ],
                      ),
                    ),
                  ),
                  const SizedBox(height: 28),
                ],
              ),
            ),
            ),
          ],
        ),
      ),
    );
  }
}
