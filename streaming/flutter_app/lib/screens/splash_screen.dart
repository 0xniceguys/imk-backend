import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../core/palette.dart';
import '../core/constants.dart';
import '../providers/auth_provider.dart';
import '../providers/wallet_provider.dart';

// ──────────────────────────────────────────────────────────────────────────────
// Splash page (animated intro → auth check → navigate)
// ──────────────────────────────────────────────────────────────────────────────

class SplashPage extends ConsumerStatefulWidget {
  const SplashPage({super.key, required this.postRoute});
  final String postRoute;

  @override
  ConsumerState<SplashPage> createState() => _SplashPageState();
}

class _SplashPageState extends ConsumerState<SplashPage>
    with TickerProviderStateMixin {
  late final AnimationController _logoCtrl;
  late final Animation<double> _logoFade;
  late final Animation<double> _logoScale;
  late final AnimationController _shimmerCtrl;
  late final Animation<double> _shimmerAnim;
  late final AnimationController _subtitleCtrl;
  late final Animation<double> _subtitleFade;
  late final Animation<double> _subtitleSlide;
  late final AnimationController _glowCtrl;
  late final Animation<double> _glowFade;

  @override
  void initState() {
    super.initState();
    _logoCtrl = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 700),
    );
    _logoFade = CurvedAnimation(parent: _logoCtrl, curve: Curves.easeOut);
    _logoScale = Tween<double>(
      begin: 0.72,
      end: 1.0,
    ).animate(CurvedAnimation(parent: _logoCtrl, curve: Curves.easeOutBack));

    _shimmerCtrl = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 900),
    );
    _shimmerAnim = CurvedAnimation(
      parent: _shimmerCtrl,
      curve: Curves.easeInOut,
    );

    _subtitleCtrl = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 600),
    );
    _subtitleFade = CurvedAnimation(
      parent: _subtitleCtrl,
      curve: Curves.easeOut,
    );
    _subtitleSlide = Tween<double>(begin: 20, end: 0).animate(
      CurvedAnimation(parent: _subtitleCtrl, curve: Curves.easeOutCubic),
    );

    _glowCtrl = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 500),
    );
    _glowFade = CurvedAnimation(parent: _glowCtrl, curve: Curves.easeInOut);

    _run();
  }

  /// Wait for auth state to leave [AuthStatus.unknown].
  /// Returns the resolved [AuthStatus].
  Future<AuthStatus> _waitForAuth() async {
    final completer = Completer<AuthStatus>();
    // Check current state first — may already be resolved
    final current = ref.read(authProvider).status;
    if (current != AuthStatus.unknown) return current;

    // Listen for the first non-unknown state
    late final ProviderSubscription<AuthState> sub;
    sub = ref.listenManual<AuthState>(authProvider, (prev, next) {
      if (next.status != AuthStatus.unknown && !completer.isCompleted) {
        completer.complete(next.status);
        sub.close();
      }
    });

    // Safety timeout — if Privy hangs, fall back to sign-in after 8s
    return completer.future.timeout(
      const Duration(seconds: 8),
      onTimeout: () {
        sub.close();
        return AuthStatus.unauthenticated;
      },
    );
  }

  Future<void> _run() async {
    if (!mounted) return;
    // Start animation and auth resolution in parallel
    final authFuture = _waitForAuth();

    await _logoCtrl.forward();
    if (!mounted) return;
    _shimmerCtrl.forward();
    await Future.delayed(const Duration(milliseconds: 200));
    if (!mounted) return;
    _subtitleCtrl.forward();
    await Future.delayed(const Duration(milliseconds: 500));
    if (!mounted) return;
    await _glowCtrl.forward();
    await Future.delayed(const Duration(milliseconds: 250));
    if (!mounted) return;

    // Wait for auth — animation is done, auth may already be resolved
    final authStatus = await authFuture;
    if (!mounted) return;

    if (authStatus == AuthStatus.authenticated) {
      // Returning user — go straight to arena, no sign-in flash
      ref.read(walletProvider.notifier).loadWallet();
      Navigator.of(
        context,
      ).pushNamedAndRemoveUntil('/arena-list', (_) => false);
    } else {
      // New/logged-out user — go to get-started or sign-in
      Navigator.of(
        context,
      ).pushNamedAndRemoveUntil(widget.postRoute, (_) => false);
    }
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
    return Scaffold(
      backgroundColor: Palette.black,
      body: AnimatedBuilder(
        animation: Listenable.merge([
          _logoCtrl,
          _shimmerCtrl,
          _subtitleCtrl,
          _glowCtrl,
        ]),
        builder: (ctx, child) => Container(
          color: Palette.black,
          child: Stack(
            fit: StackFit.expand,
            children: [
              // Radial ambient glow
              Positioned.fill(
                child: Opacity(
                  opacity: _logoFade.value * 0.35,
                  child: const DecoratedBox(
                    decoration: BoxDecoration(
                      gradient: RadialGradient(
                        center: Alignment.center,
                        radius: 0.6,
                        colors: [Color(0x2EFFC500), Colors.transparent],
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
                              alpha: _glowFade.value * 0.5,
                            ),
                            Colors.transparent,
                          ],
                        ),
                      ),
                    ),
                  ),
                ),
              Center(
                child: Column(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    // Logo: scale bounce + fade
                    Opacity(
                      opacity: _logoFade.value,
                      child: Transform.scale(
                        scale: _logoScale.value,
                        child: _buildShimmerLogo(),
                      ),
                    ),
                    const SizedBox(height: 24),
                    // Tagline: slide up + fade
                    Transform.translate(
                      offset: Offset(0, _subtitleSlide.value),
                      child: Opacity(
                        opacity: _subtitleFade.value,
                        child: Column(
                          children: [
                            Container(
                              height: 1,
                              width: 200,
                              margin: const EdgeInsets.only(bottom: 12),
                              decoration: const BoxDecoration(
                                gradient: LinearGradient(
                                  colors: [
                                    Colors.transparent,
                                    Color(0xFFFFC500),
                                    Colors.transparent,
                                  ],
                                ),
                              ),
                            ),
                            const Text(
                              'LLMs fight. Humans bet.',
                              style: TextStyle(
                                color: Palette.muted,
                                fontSize: 14,
                                letterSpacing: -0.1,
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
          ),
        ),
      ),
    );
  }

  Widget _buildShimmerLogo() {
    final p = _shimmerAnim.value;
    return ShaderMask(
      blendMode: BlendMode.srcATop,
      shaderCallback: (bounds) => LinearGradient(
        colors: const [
          Colors.transparent,
          Color(0xFFFFE066),
          Colors.transparent,
        ],
        stops: [
          (p - 0.25).clamp(0.0, 1.0),
          p.clamp(0.0, 1.0),
          (p + 0.25).clamp(0.0, 1.0),
        ],
        begin: Alignment.centerLeft,
        end: Alignment.centerRight,
      ).createShader(bounds),
      child: Image.asset(Assets.logoVector, width: 200),
    );
  }
}
