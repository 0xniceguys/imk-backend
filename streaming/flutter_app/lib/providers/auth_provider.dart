import 'dart:convert';

import 'package:flutter/foundation.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:shared_preferences/shared_preferences.dart';
import '../services/privy_service.dart';
import '../services/api_service.dart';
import '../services/wallet_deep_link_service.dart';
import '../utils/base58.dart';
import 'match_provider.dart';

void _log(String msg) {
  // ignore: avoid_print
  if (kDebugMode) print('[Auth] $msg');
}

enum AuthStatus { unknown, unauthenticated, authenticating, authenticated }

class AuthState {
  final AuthStatus status;
  final String? email;
  final String? walletAddress;
  final String? error;
  final String? pendingEmail;
  final bool hasSeenIntro;

  const AuthState({
    this.status = AuthStatus.unknown,
    this.email,
    this.walletAddress,
    this.error,
    this.pendingEmail,
    this.hasSeenIntro = false,
  });

  AuthState copyWith({
    AuthStatus? status,
    String? email,
    String? walletAddress,
    String? error,
    String? pendingEmail,
    bool? hasSeenIntro,
  }) =>
      AuthState(
        status: status ?? this.status,
        email: email ?? this.email,
        walletAddress: walletAddress ?? this.walletAddress,
        error: error,
        pendingEmail: pendingEmail ?? this.pendingEmail,
        hasSeenIntro: hasSeenIntro ?? this.hasSeenIntro,
      );
}

class AuthNotifier extends StateNotifier<AuthState> {
  final PrivyService _privy;
  final WalletDeepLinkService _walletDeepLink;
  final ApiService _api;

  AuthNotifier(this._privy, this._walletDeepLink, this._api)
      : super(const AuthState()) {
    _init();
  }

  Future<void> _init() async {
    final prefs = await SharedPreferences.getInstance();
    final hasSeenIntro = prefs.getBool('hasSeenIntro') ?? false;

    // Persist hasSeenIntro=true IMMEDIATELY on first app open.
    // This guarantees that if Android recreates the activity while the user
    // is in Phantom (deep link callback), the new start uses
    // initialRoute='/sign-in-modal', never '/get-started'.
    if (!hasSeenIntro) {
      await prefs.setBool('hasSeenIntro', true);
    }

    // ── Dev bypass ──────────────────────────────────────────────────────────
    // flutter run --dart-define=BYPASS_AUTH=true
    // Skips Privy entirely and goes straight to arena. Debug builds only.
    const bypassAuth =
        bool.fromEnvironment('BYPASS_AUTH', defaultValue: false);
    if (bypassAuth && kDebugMode) {
      _log('BYPASS_AUTH=true — skipping Privy, going straight to arena');
      state = const AuthState(
        status: AuthStatus.authenticated,
        email: 'dev@bypass.local',
        walletAddress: 'DevBypassWallet',
        hasSeenIntro: true,
      );
      return;
    }
    // ───────────────────────────────────────────────────────────────────────

    await _privy.initialize();
    if (_privy.isLoggedIn) {
      await _privy.createSolanaWallet(); // no-op if wallet already exists
      await _syncBackendAuth();
      state = AuthState(
        status: AuthStatus.authenticated,
        email: _privy.email,
        walletAddress: _privy.walletAddress,
        hasSeenIntro: true,
      );
    } else {
      state = AuthState(
        status: AuthStatus.unauthenticated,
        hasSeenIntro: true, // always true after first _init()
      );
    }
  }

  /// Get Privy access token, send to backend, set on ApiService for future calls.
  Future<void> _syncBackendAuth() async {
    // Persist on every successful auth so Get Started never shows again.
    final prefs = await SharedPreferences.getInstance();
    await prefs.setBool('hasSeenIntro', true);

    final token = await _privy.getAccessToken();
    if (token == null) {
      _log('No Privy access token available');
      return;
    }
    _log('Got Privy token, sending to backend...');
    _api.setAuthToken(token);
    final user = await _api.login(token);
    if (user != null) {
      _log('Backend auth OK: ${user['id']}');
    } else {
      _log('Backend auth failed — proceeding without backend session');
    }
  }

  /// Mark intro as seen (persisted across app restarts).
  Future<void> markIntroSeen() async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setBool('hasSeenIntro', true);
    state = state.copyWith(hasSeenIntro: true);
  }

  Future<void> sendEmailCode(String email) async {
    state = state.copyWith(
        status: AuthStatus.authenticating, pendingEmail: email);
    final ok = await _privy.sendEmailCode(email);
    if (!ok) {
      state = state.copyWith(
          status: AuthStatus.unauthenticated, error: 'Failed to send code');
    }
  }

  Future<bool> verifyEmailCode(String code) async {
    final email = state.pendingEmail;
    if (email == null) return false;
    final ok = await _privy.loginWithEmailCode(code, email);
    if (ok) {
      await _privy.createSolanaWallet();
      await _syncBackendAuth();
      state = AuthState(
        status: AuthStatus.authenticated,
        email: _privy.email,
        walletAddress: _privy.walletAddress,
      );
      return true;
    }
    state = state.copyWith(
        status: AuthStatus.unauthenticated, error: 'Invalid code');
    return false;
  }

  Future<bool> loginWithGoogle() async {
    state = state.copyWith(status: AuthStatus.authenticating);
    final ok = await _privy.loginWithGoogle();
    if (ok) {
      await _privy.createSolanaWallet();
      await _syncBackendAuth();
      state = AuthState(
        status: AuthStatus.authenticated,
        email: _privy.email,
        walletAddress: _privy.walletAddress,
      );
      return true;
    }
    state = state.copyWith(
        status: AuthStatus.unauthenticated,
        error: _privy.lastError ?? 'Google login failed');
    return false;
  }

  Future<bool> loginWithApple() async {
    state = state.copyWith(status: AuthStatus.authenticating);
    final ok = await _privy.loginWithApple();
    if (ok) {
      await _privy.createSolanaWallet();
      await _syncBackendAuth();
      state = AuthState(
        status: AuthStatus.authenticated,
        email: _privy.email,
        walletAddress: _privy.walletAddress,
      );
      return true;
    }
    state = state.copyWith(
        status: AuthStatus.unauthenticated,
        error: _privy.lastError ?? 'Apple login failed');
    return false;
  }

  Future<bool> loginWithPasskey() async {
    state = state.copyWith(status: AuthStatus.authenticating);
    final ok = await _privy.loginWithPasskey();
    if (ok) {
      await _privy.createSolanaWallet();
      await _syncBackendAuth();
      state = AuthState(
        status: AuthStatus.authenticated,
        walletAddress: _privy.walletAddress,
      );
      return true;
    }
    state = state.copyWith(
        status: AuthStatus.unauthenticated,
        error: _privy.lastError ?? 'Passkey login failed');
    return false;
  }

  /// Login with external Solana wallet (Phantom / Solflare) via SIWS.
  ///
  /// Flow:
  ///  1. Deep-link to wallet app → get wallet address
  ///  2. Generate SIWS message from Privy
  ///  3. Deep-link to wallet app → sign the message
  ///  4. Send signature to Privy → authenticated
  Future<bool> loginWithWallet(
      {SolanaWallet wallet = SolanaWallet.phantom}) async {
    _log('=== loginWithWallet START (${wallet.name}) ===');
    final totalSw = Stopwatch()..start();
    state = state.copyWith(status: AuthStatus.authenticating);
    try {
      // Step 1: Connect to wallet → get address
      _log('Step 1: connecting to ${wallet.name}...');
      final stepSw = Stopwatch()..start();
      final address = await _walletDeepLink.connect(wallet: wallet);
      _log('Step 1 DONE in ${stepSw.elapsedMilliseconds}ms: address=$address');

      // Step 2: Generate SIWS message from Privy
      _log('Step 2: generating SIWS message for $address...');
      stepSw.reset();
      final message = await _privy.generateSiwsMessage(address);
      _log('Step 2 API call took ${stepSw.elapsedMilliseconds}ms');
      if (message == null) {
        _log('Step 2 FAILED: message is null, lastError=${_privy.lastError}');
        state = state.copyWith(
            status: AuthStatus.unauthenticated,
            error: _privy.lastError ?? 'Failed to generate SIWS message');
        return false;
      }
      _log('Step 2 DONE: SIWS message (${message.length} chars)');
      _log('Step 2 message preview: ${message.substring(0, message.length.clamp(0, 200))}...');

      // Step 3: Sign message with wallet
      _log('Step 3: requesting signMessage from ${wallet.name}...');
      stepSw.reset();
      final signatureB58 = await _walletDeepLink.signMessage(message);
      _log('Step 3 DONE in ${stepSw.elapsedMilliseconds}ms');
      _log('Step 3 signature (base58): $signatureB58');

      // Convert signature from base58 → base64.
      // Phantom returns base58, but Privy API expects base64.
      final sigBytes = base58Decode(signatureB58);
      final signatureB64 = base64Encode(sigBytes);
      _log('Step 3 signature (base64): $signatureB64');
      _log('Step 3 sig bytes: ${sigBytes.length} (expect 64 for ed25519)');

      // NO delay before Step 4 — it's just an HTTP call, not a deep link.
      _log('Step 4: sending SIWS to Privy IMMEDIATELY (no delay)...');
      _log('Total elapsed since SIWS generated: ${totalSw.elapsedMilliseconds}ms');
      stepSw.reset();
      final ok = await _privy.loginWithSiws(message, signatureB64, address);
      _log('Step 4 API call took ${stepSw.elapsedMilliseconds}ms, result=$ok');
      if (ok) {
        _log('=== loginWithWallet SUCCESS in ${totalSw.elapsedMilliseconds}ms ===');
        // Create Privy embedded wallet — same as email/social login.
        // The external wallet (Phantom/Solflare) was for auth only;
        // the embedded wallet is what the app uses for transactions.
        await _privy.createSolanaWallet();
        await _syncBackendAuth();
        state = AuthState(
          status: AuthStatus.authenticated,
          walletAddress: _privy.walletAddress,
        );
        return true;
      }

      _log('=== loginWithWallet FAILED: ${_privy.lastError} ===');
      state = state.copyWith(
          status: AuthStatus.unauthenticated,
          error: _privy.lastError ?? 'Wallet login failed');
      return false;
    } catch (e, st) {
      _log('=== loginWithWallet EXCEPTION in ${totalSw.elapsedMilliseconds}ms ===');
      _log('Error: $e');
      _log('Stack: $st');
      _walletDeepLink.disconnect();
      state = state.copyWith(
          status: AuthStatus.unauthenticated, error: e.toString());
      return false;
    }
  }

  Future<void> logout() async {
    await _privy.logout();
    _api.setAuthToken(null);
    state = AuthState(
      status: AuthStatus.unauthenticated,
      hasSeenIntro: state.hasSeenIntro,
    );
  }

  Future<void> deleteAccount() async {
    await _privy.deleteAccount();
    state = AuthState(
      status: AuthStatus.unauthenticated,
      hasSeenIntro: state.hasSeenIntro,
    );
  }
}

final privyServiceProvider = Provider<PrivyService>((ref) => PrivyService());

final walletDeepLinkProvider =
    Provider<WalletDeepLinkService>((ref) => WalletDeepLinkService());

final authProvider = StateNotifierProvider<AuthNotifier, AuthState>(
  (ref) => AuthNotifier(
    ref.read(privyServiceProvider),
    ref.read(walletDeepLinkProvider),
    ref.read(apiServiceProvider),
  ),
);
