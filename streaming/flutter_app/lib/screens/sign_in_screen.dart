import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../core/palette.dart';
import '../core/typography.dart';
import '../core/constants.dart';
import '../providers/auth_provider.dart';
import '../services/wallet_deep_link_service.dart';
import '../widgets/shared/ik_loader.dart';

class SignInScreen extends ConsumerStatefulWidget {
  const SignInScreen({super.key, required this.onNavigate});
  final void Function(String) onNavigate;

  @override
  ConsumerState<SignInScreen> createState() => _SignInScreenState();
}

class _SignInScreenState extends ConsumerState<SignInScreen>
    with SingleTickerProviderStateMixin {
  final _emailCtrl = TextEditingController();
  bool _showOtp = false;
  final _otpCtrl = TextEditingController();
  late final AnimationController _enterCtrl;
  late final Animation<double> _modalFade;
  late final Animation<double> _modalScale;

  @override
  void initState() {
    super.initState();
    debugPrint('[Screen] SIGN_IN initState');
    _enterCtrl = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 500),
    );
    _modalFade = CurvedAnimation(
        parent: _enterCtrl, curve: Curves.easeOut);
    _modalScale = Tween<double>(begin: 0.92, end: 1.0).animate(
      CurvedAnimation(parent: _enterCtrl, curve: Curves.easeOutCubic),
    );
    _enterCtrl.forward();
  }

  @override
  void dispose() {
    _emailCtrl.dispose();
    _otpCtrl.dispose();
    _enterCtrl.dispose();
    super.dispose();
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
    final ok =
        await ref.read(authProvider.notifier).loginWithWallet(wallet: wallet);
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

    return GestureDetector(
      onTap: () => FocusScope.of(context).unfocus(),
      child: Stack(
        fit: StackFit.expand,
        children: [
          Positioned.fill(
            child: Opacity(
              opacity: 0.11,
              child: Image.asset(Assets.startBg, fit: BoxFit.cover),
            ),
          ),
          Positioned(
            left: 0,
            bottom: 0,
            child: Image.asset(Assets.signInBg,
                width: 294, height: 523, fit: BoxFit.cover),
          ),
          Positioned.fill(
            child: Container(color: Palette.overlay40),
          ),
          Center(
            child: FadeTransition(
              opacity: _modalFade,
              child: ScaleTransition(
                scale: _modalScale,
                child: Container(
                  width: 299,
                  padding: const EdgeInsets.symmetric(
                      horizontal: 12, vertical: 16),
                  decoration: BoxDecoration(
                    border: Border.all(color: Palette.gold, width: 4),
                  ),
                  child: Container(
                    padding:
                        const EdgeInsets.fromLTRB(12, 28, 12, 16),
                    decoration: BoxDecoration(
                      color: Palette.overlay90,
                      borderRadius: BorderRadius.circular(22),
                      border:
                          Border.all(color: Palette.innerBorder),
                    ),
                    child: Column(
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        Image.asset(Assets.logoVector, width: 179),
                        const SizedBox(height: 12),
                        Text('Log in or sign up',
                            style: bodyStyle(size: 18)),
                        const SizedBox(height: 12),
                        // Animated email ↔ OTP swap
                        AnimatedSwitcher(
                          duration: const Duration(milliseconds: 300),
                          switchInCurve: Curves.easeOut,
                          switchOutCurve: Curves.easeIn,
                          child: _showOtp
                              ? _otpInput()
                              : _emailInput(),
                        ),
                        const SizedBox(height: 12),
                        _socialButton('Google',
                            icon: Icons.g_mobiledata, onTap: _onGoogle),
                        const SizedBox(height: 8),
                        _socialButton('Apple',
                            icon: Icons.apple, onTap: _onApple),
                        const SizedBox(height: 14),
                        // Divider
                        Row(
                          children: [
                            Expanded(
                              child: Container(
                                  height: 1,
                                  color: Palette.inputBorder),
                            ),
                            Padding(
                              padding: const EdgeInsets.symmetric(
                                  horizontal: 12),
                              child: Text('or',
                                  style: bodyStyle(
                                      size: 12,
                                      color: Palette.muted)),
                            ),
                            Expanded(
                              child: Container(
                                  height: 1,
                                  color: Palette.inputBorder),
                            ),
                          ],
                        ),
                        const SizedBox(height: 14),
                        _socialButton('Phantom',
                            icon: Icons.account_balance_wallet_outlined,
                            onTap: () => _onWallet(SolanaWallet.phantom)),
                        const SizedBox(height: 8),
                        _socialButton('Solflare',
                            icon: Icons.account_balance_wallet_outlined,
                            onTap: () => _onWallet(SolanaWallet.solflare)),
                        const SizedBox(height: 12),
                        Text('Protected by privy',
                            style: bodyStyle(
                                size: 14, color: Palette.muted)),
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
                contentPadding:
                    const EdgeInsets.symmetric(vertical: 8),
              ),
              keyboardType: TextInputType.emailAddress,
              onSubmitted: (_) => _onEmailSubmit(),
            ),
          ),
          TextButton(
            onPressed: _onEmailSubmit,
            style: plainBtn(),
            child: Text('Submit',
                style: bodyStyle(
                    size: 14, color: Palette.submitText)),
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
                contentPadding:
                    const EdgeInsets.symmetric(vertical: 8),
              ),
              keyboardType: TextInputType.number,
              onSubmitted: (_) => _onOtpSubmit(),
            ),
          ),
          TextButton(
            onPressed: _onOtpSubmit,
            style: plainBtn(),
            child: Text('Verify',
                style: bodyStyle(size: 14, color: Palette.gold)),
          ),
        ],
      ),
    );
  }

  Widget _socialButton(String label,
      {IconData? icon, VoidCallback? onTap, bool highlighted = false}) {
    return SizedBox(
      width: double.infinity,
      child: OutlinedButton.icon(
        onPressed: onTap,
        icon: icon != null
            ? Icon(icon, size: 20, color: Palette.white)
            : const SizedBox.shrink(),
        label: Text(label,
            textAlign: TextAlign.center, style: bodyStyle(size: 14)),
        style: OutlinedButton.styleFrom(
          side: const BorderSide(color: Palette.inputBorder),
          shape: RoundedRectangleBorder(
              borderRadius: BorderRadius.circular(9)),
          backgroundColor: highlighted
              ? Palette.highlightedBtnBg
              : Colors.transparent,
          padding: const EdgeInsets.symmetric(vertical: 12),
        ),
      ),
    );
  }
}
