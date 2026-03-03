import 'dart:ui';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../core/palette.dart';
import '../core/typography.dart';
import '../core/constants.dart';
import '../providers/auth_provider.dart';
import '../services/wallet_deep_link_service.dart';
import '../widgets/shared/ornate_button.dart';
import '../widgets/shared/ik_loader.dart';

class GetStartedScreen extends ConsumerStatefulWidget {
  const GetStartedScreen({super.key, this.onNavigate});
  final void Function(String)? onNavigate;

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

  // Sign-in modal auth state
  bool _showSignInModal = false;
  bool _showOtp = false;
  final _emailCtrl = TextEditingController();
  final _otpCtrl = TextEditingController();

  @override
  void initState() {
    super.initState();
    debugPrint('[Screen] GET_STARTED initState');
    _ctrl = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 1400),
    );

    // Staggered entrance: bg -> hero -> logo -> tagline -> CTA
    _bgFade = Tween<double>(
      begin: 0,
      end: 0.15,
    ).animate(CurvedAnimation(parent: _ctrl, curve: const Interval(0, 0.3)));
    _heroSlide = Tween<double>(begin: 60, end: 0).animate(
      CurvedAnimation(
        parent: _ctrl,
        curve: const Interval(0.1, 0.5, curve: Curves.easeOutCubic),
      ),
    );
    _logoFade = CurvedAnimation(
      parent: _ctrl,
      curve: const Interval(0.2, 0.5, curve: Curves.easeOut),
    );
    _taglineFade = CurvedAnimation(
      parent: _ctrl,
      curve: const Interval(0.35, 0.65, curve: Curves.easeOut),
    );
    _ctaFade = CurvedAnimation(
      parent: _ctrl,
      curve: const Interval(0.55, 0.85, curve: Curves.easeOut),
    );

    _ctrl.forward();
  }

  @override
  void dispose() {
    _ctrl.dispose();
    _emailCtrl.dispose();
    _otpCtrl.dispose();
    super.dispose();
  }

  void _closeSignInModal() {
    FocusScope.of(context).unfocus();
    setState(() {
      _showSignInModal = false;
    });
  }

  void _onGetStartedTapped() {
    setState(() {
      _showSignInModal = true;
    });
  }

  Future<void> _onEmailSubmit() async {
    final email = _emailCtrl.text.trim();
    if (email.isEmpty) return;
    await ref.read(authProvider.notifier).sendEmailCode(email);
    if (!mounted) return;
    final err = ref.read(authProvider).error;
    if (err != null) {
      _showError(err);
    } else {
      setState(() => _showOtp = true);
    }
  }

  Future<void> _onOtpSubmit() async {
    final code = _otpCtrl.text.trim();
    if (code.isEmpty) return;
    final ok = await ref.read(authProvider.notifier).verifyEmailCode(code);
    if (!mounted) return;
    // Navigation on success is handled reactively by app.dart ref.listen
    if (!ok) _showError(ref.read(authProvider).error);
  }

  Future<void> _onGoogle() async {
    final ok = await ref.read(authProvider.notifier).loginWithGoogle();
    if (!mounted) return;
    if (!ok) _showError(ref.read(authProvider).error);
  }

  Future<void> _onApple() async {
    final ok = await ref.read(authProvider.notifier).loginWithApple();
    if (!mounted) return;
    if (!ok) _showError(ref.read(authProvider).error);
  }

  Future<void> _onWallet(SolanaWallet wallet) async {
    final ok = await ref
        .read(authProvider.notifier)
        .loginWithWallet(wallet: wallet);
    if (!mounted) return;
    if (!ok) _showError(ref.read(authProvider).error);
  }

  void _showError(String? message) {
    if (message == null) return;
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(
        content: Text(message, style: const TextStyle(color: Colors.white)),
        backgroundColor: Palette.red,
        duration: const Duration(seconds: 5),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final auth = ref.watch(authProvider);
    final isLoading = auth.status == AuthStatus.authenticating;

    final screenH = MediaQuery.of(context).size.height;
    // Scale hero to fill ~90% of screen height, keep original aspect ratio
    final heroHeight = screenH * 0.75;
    final heroWidth = heroHeight * (386 / 686);

    return PopScope(
      canPop: !_showSignInModal,
      onPopInvokedWithResult: (didPop, result) {
        if (!didPop && _showSignInModal) {
          _closeSignInModal();
        }
      },
      child: AnimatedBuilder(
        animation: _ctrl,
        builder: (context, _) => Container(
          color: Palette.black,
          child: Stack(
            fit: StackFit.expand,
            children: [
              Positioned.fill(
                child: Opacity(
                  opacity: 0.5,
                  child: Image.asset(Assets.startBg, fit: BoxFit.cover),
                ),
              ),
              Positioned(
                left: -heroWidth * 0.0,
                bottom: -50 + _heroSlide.value,
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
              SafeArea(
                child: Padding(
                  padding: const EdgeInsets.symmetric(horizontal: 32),
                  child: Column(
                    children: [
                      const SizedBox(height: 48),
                      FadeTransition(
                        opacity: _logoFade,
                        child: SlideTransition(
                          position: Tween<Offset>(
                            begin: const Offset(0, -0.15),
                            end: Offset.zero,
                          ).animate(_logoFade),
                          child: Image.asset(Assets.logoVector, width: 250),
                        ),
                      ),
                      const SizedBox(height: 8),
                      FadeTransition(
                        opacity: _taglineFade,
                        child: Text(
                          'LLMs train Agents. Agents fight.\nHumans bet on Solana.',
                          textAlign: TextAlign.center,
                          style: displayStyle(
                            size: 20,
                            color: Palette.muted,
                            letterSpacing: -0.7,
                          ),
                        ),
                      ),
                      const Spacer(),
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
                                onTap: _onGetStartedTapped,
                              ),
                              const SizedBox(height: 16),
                              Text(
                                'By continuing you accept the terms and\nconditions and privacy policy.',
                                textAlign: TextAlign.center,
                                style: bodyStyle(size: 12, color: Palette.muted),
                              ),
                            ],
                          ),
                        ),
                      ),
                      const SizedBox(height: 24),
                    ],
                  ),
                ),
              ),
              if (_showSignInModal)
                Positioned.fill(
                  child: GestureDetector(
                    behavior: HitTestBehavior.opaque,
                    onTap: _closeSignInModal,
                    child: Stack(
                      fit: StackFit.expand,
                      children: [
                        ClipRect(
                          child: BackdropFilter(
                            filter: ImageFilter.blur(sigmaX: 18, sigmaY: 18),
                            child: Container(
                              color: Colors.black.withValues(alpha: 0.36),
                            ),
                          ),
                        ),
                        Center(
                          child: GestureDetector(
                            behavior: HitTestBehavior.translucent,
                            onTap: () {},
                            child: TweenAnimationBuilder<double>(
                              tween: Tween(begin: 0.96, end: 1.0),
                              duration: const Duration(milliseconds: 220),
                              curve: Curves.easeOutCubic,
                              builder: (context, scale, child) {
                                return Transform.scale(
                                  scale: scale,
                                  child: child,
                                );
                              },
                              child: Container(
                                width: 299,
                                padding: const EdgeInsets.symmetric(
                                  horizontal: 0,
                                  vertical: 0,
                                ),
                                decoration: BoxDecoration(
                                  border: Border.all(
                                    color: Palette.gold,
                                    width: 4,
                                  ),
                                ),
                                child: Container(
                                  padding: const EdgeInsets.fromLTRB(
                                    12,
                                    28,
                                    12,
                                    16,
                                  ),
                                  decoration: BoxDecoration(
                                    color: Palette.overlay90,
                                    borderRadius: BorderRadius.circular(22),
                                    border: Border.all(
                                      color: Palette.innerBorder,
                                    ),
                                  ),
                                  child: Column(
                                    mainAxisSize: MainAxisSize.min,
                                    children: [
                                      Image.asset(Assets.logoVector, width: 179),
                                      const SizedBox(height: 12),
                                      Text(
                                        'Log in or sign up',
                                        style: bodyStyle(size: 18),
                                      ),
                                      const SizedBox(height: 12),
                                      AnimatedSwitcher(
                                        duration:
                                            const Duration(milliseconds: 300),
                                        switchInCurve: Curves.easeOut,
                                        switchOutCurve: Curves.easeIn,
                                        child: _showOtp
                                            ? _otpInput()
                                            : _emailInput(),
                                      ),
                                      const SizedBox(height: 12),
                                      _socialButton(
                                        'Google',
                                        icon: Icons.g_mobiledata,
                                        onTap: _onGoogle,
                                      ),
                                      const SizedBox(height: 8),
                                      _socialButton(
                                        'Apple',
                                        icon: Icons.apple,
                                        onTap: _onApple,
                                      ),
                                      const SizedBox(height: 14),
                                      Row(
                                        children: [
                                          Expanded(
                                            child: Container(
                                              height: 1,
                                              color: Palette.inputBorder,
                                            ),
                                          ),
                                          Padding(
                                            padding: const EdgeInsets.symmetric(
                                              horizontal: 12,
                                            ),
                                            child: Text(
                                              'or',
                                              style: bodyStyle(
                                                size: 12,
                                                color: Palette.muted,
                                              ),
                                            ),
                                          ),
                                          Expanded(
                                            child: Container(
                                              height: 1,
                                              color: Palette.inputBorder,
                                            ),
                                          ),
                                        ],
                                      ),
                                      const SizedBox(height: 14),
                                      _socialButton(
                                        'Phantom',
                                        icon: Icons.account_balance_wallet_outlined,
                                        onTap: () =>
                                            _onWallet(SolanaWallet.phantom),
                                      ),
                                      const SizedBox(height: 8),
                                      _socialButton(
                                        'Solflare',
                                        icon: Icons.account_balance_wallet_outlined,
                                        onTap: () =>
                                            _onWallet(SolanaWallet.solflare),
                                      ),
                                      const SizedBox(height: 12),
                                      Text(
                                        'Protected by privy',
                                        style: bodyStyle(
                                          size: 14,
                                          color: Palette.muted,
                                        ),
                                      ),
                                      if (isLoading) ...[
                                        const SizedBox(height: 12),
                                        const IKLoader(size: 24),
                                      ],
                                    ],
                                  ),
                                ),
                              ),
                            ),
                          ),
                        ),
                      ],
                    ),
                  ),
                ),
            ],
          ),
        ),
      ),
    );
  }

  Widget _emailInput() {
    return Container(
      key: const ValueKey('email'),
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 4),
      decoration: BoxDecoration(
        borderRadius: BorderRadius.circular(9),
        border: Border.all(color: Palette.inputBorder),
      ),
      child: Row(
        children: [
          Icon(Icons.email_outlined, size: 16, color: Palette.hint),
          const SizedBox(width: 8),
          Expanded(
            child: TextField(
              controller: _emailCtrl,
              style: bodyStyle(size: 14),
              decoration: InputDecoration(
                hintText: 'your@email.com',
                hintStyle: bodyStyle(size: 14, color: Palette.hint),
                border: InputBorder.none,
                isDense: true,
                contentPadding: const EdgeInsets.symmetric(vertical: 8),
              ),
              keyboardType: TextInputType.emailAddress,
              onSubmitted: (_) => _onEmailSubmit(),
            ),
          ),
          TextButton(
            onPressed: _onEmailSubmit,
            style: plainBtn(),
            child: Text(
              'Submit',
              style: bodyStyle(size: 14, color: Palette.submitText),
            ),
          ),
        ],
      ),
    );
  }

  Widget _otpInput() {
    return Container(
      key: const ValueKey('otp'),
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 4),
      decoration: BoxDecoration(
        borderRadius: BorderRadius.circular(9),
        border: Border.all(color: Palette.gold),
      ),
      child: Row(
        children: [
          Expanded(
            child: TextField(
              controller: _otpCtrl,
              style: bodyStyle(size: 14),
              autofocus: true,
              decoration: InputDecoration(
                hintText: 'Enter 6-digit code',
                hintStyle: bodyStyle(size: 14, color: Palette.hint),
                border: InputBorder.none,
                isDense: true,
                contentPadding: const EdgeInsets.symmetric(vertical: 8),
              ),
              keyboardType: TextInputType.number,
              onSubmitted: (_) => _onOtpSubmit(),
            ),
          ),
          TextButton(
            onPressed: _onOtpSubmit,
            style: plainBtn(),
            child: Text(
              'Verify',
              style: bodyStyle(size: 14, color: Palette.gold),
            ),
          ),
        ],
      ),
    );
  }

  Widget _socialButton(
    String label, {
    IconData? icon,
    VoidCallback? onTap,
    bool highlighted = false,
  }) {
    return SizedBox(
      width: double.infinity,
      child: OutlinedButton.icon(
        onPressed: onTap,
        icon: icon != null
            ? Icon(icon, size: 20, color: Palette.white)
            : const SizedBox.shrink(),
        label: Text(
          label,
          textAlign: TextAlign.center,
          style: bodyStyle(size: 14),
        ),
        style: OutlinedButton.styleFrom(
          side: const BorderSide(color: Palette.inputBorder),
          shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(9)),
          backgroundColor:
              highlighted ? Palette.highlightedBtnBg : Colors.transparent,
          padding: const EdgeInsets.symmetric(vertical: 12),
        ),
      ),
    );
  }
}
