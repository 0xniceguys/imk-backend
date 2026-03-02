import 'package:flutter/material.dart';
import '../core/palette.dart';
import '../core/constants.dart';

/// Animated splash screen shown on every cold start.
/// Calls [onDone] once the intro animation completes (~2 s).
class SplashScreen extends StatefulWidget {
  const SplashScreen({super.key, required this.onDone});
  final VoidCallback onDone;

  @override
  State<SplashScreen> createState() => _SplashScreenState();
}

class _SplashScreenState extends State<SplashScreen>
    with TickerProviderStateMixin {
  // Phase-1: logo scale + fade  (0 – 700 ms)
  late final AnimationController _logoCtrl;
  late final Animation<double> _logoFade;
  late final Animation<double> _logoScale;

  // Phase-2: shimmer sweep  (400 – 1200 ms)
  late final AnimationController _shimmerCtrl;
  late final Animation<double> _shimmerAnim;

  // Phase-3: subtitle + tagline  (700 – 1400 ms)
  late final AnimationController _subtitleCtrl;
  late final Animation<double> _subtitleFade;
  late final Animation<double> _subtitleSlide;

  // Phase-4: glow pulse before exit (1200 – 1800 ms)
  late final AnimationController _glowCtrl;
  late final Animation<double> _glowFade;

  @override
  void initState() {
    super.initState();

    // ── Logo ──────────────────────────────────────────────────────────────
    _logoCtrl = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 700),
    );
    _logoFade = CurvedAnimation(parent: _logoCtrl, curve: Curves.easeOut);
    _logoScale = Tween<double>(begin: 0.72, end: 1.0).animate(
      CurvedAnimation(parent: _logoCtrl, curve: Curves.easeOutBack),
    );

    // ── Shimmer ───────────────────────────────────────────────────────────
    _shimmerCtrl = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 900),
    );
    _shimmerAnim = CurvedAnimation(
      parent: _shimmerCtrl,
      curve: Curves.easeInOut,
    );

    // ── Subtitle ──────────────────────────────────────────────────────────
    _subtitleCtrl = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 600),
    );
    _subtitleFade =
        CurvedAnimation(parent: _subtitleCtrl, curve: Curves.easeOut);
    _subtitleSlide = Tween<double>(begin: 20, end: 0).animate(
      CurvedAnimation(parent: _subtitleCtrl, curve: Curves.easeOutCubic),
    );

    // ── Glow exit ─────────────────────────────────────────────────────────
    _glowCtrl = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 500),
    );
    _glowFade = CurvedAnimation(parent: _glowCtrl, curve: Curves.easeInOut);

    _runSequence();
  }

  Future<void> _runSequence() async {
    // Logo comes in
    await _logoCtrl.forward();
    // Shimmer starts slightly before subtitle
    _shimmerCtrl.forward();
    await Future.delayed(const Duration(milliseconds: 200));
    _subtitleCtrl.forward();
    await Future.delayed(const Duration(milliseconds: 500));
    // Glow pulse
    await _glowCtrl.forward();
    await Future.delayed(const Duration(milliseconds: 250));
    // Done — trigger navigation
    if (mounted) widget.onDone();
  }

  @override
  void dispose() {
    _logoCtrl.dispose();
    _shimmerCtrl.dispose();
    _subtitleCtrl.dispose();
    _glowCtrl.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Container(
      color: Palette.black,
      child: AnimatedBuilder(
        animation: Listenable.merge(
            [_logoCtrl, _shimmerCtrl, _subtitleCtrl, _glowCtrl]),
        builder: (context, _) {
          return Stack(
            fit: StackFit.expand,
            children: [
              // Background radial glow (subtle gold haze)
              Positioned.fill(
                child: Opacity(
                  opacity: _logoFade.value * 0.35,
                  child: DecoratedBox(
                    decoration: BoxDecoration(
                      gradient: RadialGradient(
                        center: Alignment.center,
                        radius: 0.6,
                        colors: [
                          Palette.gold.withValues(alpha: 0.18),
                          Colors.transparent,
                        ],
                      ),
                    ),
                  ),
                ),
              ),

              // Exit glow burst
              if (_glowFade.value > 0)
                Positioned.fill(
                  child: Opacity(
                    opacity: _glowFade.value * 0.12,
                    child: DecoratedBox(
                      decoration: BoxDecoration(
                        gradient: RadialGradient(
                          center: Alignment.center,
                          radius: 0.9,
                          colors: [
                            Palette.gold.withValues(
                                alpha: _glowFade.value * 0.5),
                            Colors.transparent,
                          ],
                        ),
                      ),
                    ),
                  ),
                ),

              // Center content
              Center(
                child: Column(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    // Logo with scale + fade + shimmer
                    Opacity(
                      opacity: _logoFade.value,
                      child: Transform.scale(
                        scale: _logoScale.value,
                        child: _ShimmerLogo(
                          shimmerProgress: _shimmerAnim.value,
                        ),
                      ),
                    ),
                    const SizedBox(height: 24),

                    // Tagline fades + slides up
                    Transform.translate(
                      offset: Offset(0, _subtitleSlide.value),
                      child: Opacity(
                        opacity: _subtitleFade.value,
                        child: Column(
                          children: [
                            Container(
                              height: 1,
                              width: 120,
                              margin:
                                  const EdgeInsets.only(bottom: 12),
                              decoration: BoxDecoration(
                                gradient: LinearGradient(colors: [
                                  Colors.transparent,
                                  Palette.gold.withValues(alpha: 0.6),
                                  Colors.transparent,
                                ]),
                              ),
                            ),
                            const Text(
                              'LLMs fight. Humans bet.',
                              style: TextStyle(
                                color: Palette.muted,
                                fontSize: 14,
                                letterSpacing: 1.5,
                                fontFamily: 'SF Pro Display',
                              ),
                            ),
                          ],
                        ),
                      ),
                    ),
                  ],
                ),
              ),
            ],
          );
        },
      ),
    );
  }
}

/// Logo widget with an optional shimmer sweep overlay using ShaderMask.
class _ShimmerLogo extends StatelessWidget {
  const _ShimmerLogo({required this.shimmerProgress});
  final double shimmerProgress;

  @override
  Widget build(BuildContext context) {
    // Shimmer highlight: a bright band that sweeps left → right
    final shimmerGradient = LinearGradient(
      colors: const [
        Colors.transparent,
        Color(0xFFFFE066),
        Colors.transparent,
      ],
      stops: [
        (shimmerProgress - 0.25).clamp(0.0, 1.0),
        shimmerProgress.clamp(0.0, 1.0),
        (shimmerProgress + 0.25).clamp(0.0, 1.0),
      ],
      begin: Alignment.centerLeft,
      end: Alignment.centerRight,
    );

    return ShaderMask(
      blendMode: BlendMode.srcATop,
      shaderCallback: (bounds) => shimmerProgress > 0 && shimmerProgress < 1
          ? shimmerGradient.createShader(bounds)
          : const LinearGradient(colors: [Colors.transparent, Colors.transparent])
              .createShader(bounds),
      child: Image.asset(
        Assets.logoVector,
        width: 200,
      ),
    );
  }
}
