import 'package:flutter/foundation.dart';
import 'package:privy_flutter/privy_flutter.dart';
import '../core/runtime_client_config.dart';

void _log(String msg) {
  // ignore: avoid_print
  if (kDebugMode) print('[Privy] $msg');
}

SolanaCluster _clusterFromRuntime() {
  switch (RuntimeClientConfig.instance.cluster) {
    case 'devnet':
      return SolanaCluster.devnet;
    case 'testnet':
      return SolanaCluster.testnet;
    default:
      return SolanaCluster.mainnet;
  }
}

class PrivyService {
  late final Privy _privy;
  PrivyUser? _user;
  String? lastError;

  bool get isLoggedIn => _user != null;

  String? get email {
    final accounts = _user?.linkedAccounts;
    if (accounts == null) return null;
    for (final a in accounts) {
      if (a is EmailAccount) return a.emailAddress;
      if (a is GoogleOAuthAccount) return a.email;
      if (a is AppleOAuthAccount) return a.email;
    }
    return null;
  }

  String? get walletAddress {
    final solWallets = _user?.embeddedSolanaWallets;
    if (solWallets != null && solWallets.isNotEmpty) {
      return solWallets.first.address;
    }
    return null;
  }

  Future<void> initialize() async {
    final runtime = RuntimeClientConfig.instance;
    final appId = runtime.privyAppId;
    final clientId = runtime.privyClientId;

    _log('=== initialize START ===');
    _log('appId=$appId, clientId=$clientId, cluster=${runtime.cluster}');
    final config = PrivyConfig(
      appId: appId,
      appClientId: clientId,
      logLevel: PrivyLogLevel.verbose,
    );
    _privy = Privy.init(config: config);
    _log('Privy.init done, checking auth state...');

    // Check if user is already authenticated from a previous session
    try {
      final authState = await _privy.getAuthState();
      _log('Auth state: ${authState.runtimeType}');
      if (authState is Authenticated) {
        _user = authState.user;
        _log(
          'Already authenticated: email=${email ?? "none"}, wallet=${walletAddress ?? "none"}',
        );
        _log('User id: ${_user?.id}');
        _log('Linked accounts: ${_user?.linkedAccounts.length}');
      } else {
        _log('Not authenticated');
      }
    } catch (e, st) {
      _log('initialize getAuthState EXCEPTION: $e');
      _log('Stack: $st');
    }
    _log('=== initialize END ===');
  }

  // ── Email auth ──

  Future<bool> sendEmailCode(String emailAddr) async {
    _log('Sending email code to $emailAddr');
    lastError = null;
    final result = await _privy.email.sendCode(emailAddr);
    result.fold(
      onSuccess: (_) => _log('Email code sent successfully'),
      onFailure: (e) {
        _log('sendEmailCode FAILED: ${e.message}');
        lastError = e.message;
      },
    );
    return result is Success<void>;
  }

  Future<bool> loginWithEmailCode(String code, String emailAddr) async {
    _log('Verifying email code for $emailAddr');
    lastError = null;
    final result = await _privy.email.loginWithCode(
      code: code,
      email: emailAddr,
    );
    result.fold(
      onSuccess: (user) {
        _user = user;
        _log('Email login success: ${user.linkedAccounts.length}');
      },
      onFailure: (e) {
        _log('loginWithEmailCode FAILED: ${e.message}');
        lastError = e.message;
      },
    );
    return result is Success<PrivyUser>;
  }

  // ── OAuth ──

  Future<bool> loginWithGoogle() async {
    final appUrlScheme = 'privy-${RuntimeClientConfig.instance.privyAppId}';
    _log('loginWithGoogle: appUrlScheme=$appUrlScheme');
    lastError = null;
    try {
      final result = await _privy.oAuth.login(
        provider: OAuthProvider.google,
        appUrlScheme: appUrlScheme,
      );
      result.fold(
        onSuccess: (user) {
          _user = user;
          _log('Google login success: ${user.linkedAccounts.length}');
        },
        onFailure: (e) {
          _log('Google login FAILED: ${e.message}');
          lastError = e.message;
        },
      );
      return result is Success<PrivyUser>;
    } catch (e, st) {
      _log('Google login EXCEPTION: $e\n$st');
      lastError = e.toString();
      return false;
    }
  }

  Future<bool> loginWithApple() async {
    final appUrlScheme = 'privy-${RuntimeClientConfig.instance.privyAppId}';
    _log('loginWithApple: appUrlScheme=$appUrlScheme');
    lastError = null;
    try {
      final result = await _privy.oAuth.login(
        provider: OAuthProvider.apple,
        appUrlScheme: appUrlScheme,
      );
      result.fold(
        onSuccess: (user) {
          _user = user;
          _log('Apple login success: ${user.linkedAccounts.length}');
        },
        onFailure: (e) {
          _log('Apple login FAILED: ${e.message}');
          lastError = e.message;
        },
      );
      return result is Success<PrivyUser>;
    } catch (e, st) {
      _log('Apple login EXCEPTION: $e\n$st');
      lastError = e.toString();
      return false;
    }
  }

  // ── Passkey ──

  Future<bool> loginWithPasskey() async {
    _log('loginWithPasskey');
    lastError = null;
    try {
      final result = await _privy.passkey.login(
        relyingParty: 'immortalkombat.com',
      );
      result.fold(
        onSuccess: (user) {
          _user = user;
          _log('Passkey login success: ${user.linkedAccounts.length}');
        },
        onFailure: (e) {
          _log('Passkey login FAILED: ${e.message}');
          lastError = e.message;
        },
      );
      return result is Success<PrivyUser>;
    } catch (e, st) {
      _log('Passkey login EXCEPTION: $e\n$st');
      lastError = e.toString();
      return false;
    }
  }

  // ── SIWS (Sign-In With Solana — external wallet) ──

  Future<String?> generateSiwsMessage(String walletAddr) async {
    _log('=== generateSiwsMessage START ===');
    _log('walletAddress: $walletAddr');
    _log('appDomain: immortalkombat.mercle.ai');
    _log('appUri: https://immortalkombat.mercle.ai');
    lastError = null;
    try {
      final params = SiwsMessageParams(
        appDomain: 'immortalkombat.mercle.ai',
        appUri: 'https://immortalkombat.mercle.ai',
        walletAddress: walletAddr,
      );
      _log('Calling _privy.siws.generateMessage...');
      final sw = Stopwatch()..start();
      final result = await _privy.siws.generateMessage(params);
      _log(
        'generateMessage returned in ${sw.elapsedMilliseconds}ms, type=${result.runtimeType}',
      );
      String? message;
      result.fold(
        onSuccess: (msg) {
          message = msg;
          _log('SIWS message generated SUCCESS (${msg.length} chars)');
          _log('SIWS message FULL:\n$msg');
        },
        onFailure: (e) {
          _log('generateSiwsMessage FAILED: ${e.runtimeType} - ${e.message}');
          lastError = e.message;
        },
      );
      _log(
        '=== generateSiwsMessage END (message=${message != null ? "OK" : "NULL"}) ===',
      );
      return message;
    } catch (e, st) {
      _log('generateSiwsMessage EXCEPTION: $e');
      _log('Stack: $st');
      lastError = e.toString();
      return null;
    }
  }

  Future<bool> loginWithSiws(
    String message,
    String signature,
    String walletAddr,
  ) async {
    _log('=== loginWithSiws START ===');
    _log('walletAddr: $walletAddr');
    _log('message length: ${message.length}');
    _log('message FULL:\n$message');
    _log('signature: $signature');
    _log('signature length: ${signature.length}');
    _log('appDomain: immortalkombat.mercle.ai');
    _log('appUri: https://immortalkombat.mercle.ai');
    _log('walletClientType: other');
    _log('connectorType: solana_wallet');
    lastError = null;
    try {
      final params = SiwsMessageParams(
        appDomain: 'immortalkombat.mercle.ai',
        appUri: 'https://immortalkombat.mercle.ai',
        walletAddress: walletAddr,
      );
      _log('Calling _privy.siws.login...');
      final sw = Stopwatch()..start();
      final result = await _privy.siws.login(
        message: message,
        signature: signature,
        params: params,
        metadata: const WalletLoginMetadata(
          walletClientType: WalletClientType.other,
          connectorType: 'solana_wallet',
        ),
      );
      _log(
        'siws.login returned in ${sw.elapsedMilliseconds}ms, type=${result.runtimeType}',
      );
      result.fold(
        onSuccess: (user) {
          _user = user;
          _log('SIWS login SUCCESS! userId=${user.id}');
          _log('linkedAccounts: ${user.linkedAccounts.length}');
          for (final a in user.linkedAccounts) {
            _log('  account: ${a.runtimeType}');
          }
        },
        onFailure: (e) {
          _log('loginWithSiws FAILED: ${e.runtimeType} - ${e.message}');
          lastError = e.message;
        },
      );
      final success = result is Success<PrivyUser>;
      _log('=== loginWithSiws END (success=$success) ===');
      return success;
    } catch (e, st) {
      _log('loginWithSiws EXCEPTION: $e');
      _log('Stack: $st');
      lastError = e.toString();
      return false;
    }
  }

  // ── Embedded Wallets ──

  Future<String?> createSolanaWallet() async {
    _log('=== createSolanaWallet START ===');
    if (_user == null) {
      _log('createSolanaWallet: no user, returning null');
      return null;
    }
    // Check if wallet already exists
    final existing = _user!.embeddedSolanaWallets;
    _log('Existing embedded Solana wallets: ${existing.length}');
    if (existing.isNotEmpty) {
      _log('Wallet already exists: ${existing.first.address}');
      return existing.first.address;
    }

    _log('Creating new embedded Solana wallet...');
    final result = await _user!.createSolanaWallet();
    _log('createSolanaWallet result: ${result.runtimeType}');
    if (result is Success<EmbeddedSolanaWallet>) {
      _log('Wallet created: ${result.value.address}');
      // Refresh user to get updated wallet list
      _user = await _privy.getUser();
      _log('User refreshed, wallets: ${_user?.embeddedSolanaWallets.length}');
      return result.value.address;
    }
    _log('createSolanaWallet FAILED: ${result.runtimeType}');
    return null;
  }

  Future<String?> signMessage(String message) async {
    _log('=== signMessage (embedded wallet) START ===');
    final solWallets = _user?.embeddedSolanaWallets;
    if (solWallets == null || solWallets.isEmpty) {
      _log('signMessage: no embedded wallets available');
      return null;
    }
    _log('Signing with wallet: ${solWallets.first.address}');
    final result = await solWallets.first.provider.signMessage(message);
    _log('signMessage result: ${result.runtimeType}');
    if (result is Success<String>) {
      _log('signMessage SUCCESS: ${result.value}');
      return result.value;
    }
    _log('signMessage FAILED');
    return null;
  }

  /// Sign a Solana transaction with the embedded wallet.
  ///
  /// [transactionBytes] — serialized transaction bytes.
  /// Returns the signed transaction (base64) or null on failure.
  Future<String?> signTransaction(Uint8List transactionBytes) async {
    _log('=== signTransaction (embedded wallet) START ===');
    final solWallets = _user?.embeddedSolanaWallets;
    if (solWallets == null || solWallets.isEmpty) {
      _log('signTransaction: no embedded wallets available');
      return null;
    }
    try {
      _log('Signing tx with wallet: ${solWallets.first.address}');
      final result = await solWallets.first.provider.signTransaction(
        transactionBytes,
      );
      _log('signTransaction result: ${result.runtimeType}');
      if (result is Success<String>) {
        _log('signTransaction SUCCESS');
        return result.value;
      }
      _log('signTransaction FAILED');
      return null;
    } catch (e) {
      _log('signTransaction error: $e');
      return null;
    }
  }

  /// Sign and send a Solana transaction with the embedded wallet.
  ///
  /// [transactionBytes] — serialized transaction bytes.
  /// [cluster] — optional Solana cluster (defaults to Privy's default).
  /// [rpcUrl] — optional RPC URL override.
  /// Returns the transaction signature or null on failure.
  Future<String?> signAndSendTransaction(
    Uint8List transactionBytes, {
    SolanaCluster? cluster,
    String? rpcUrl,
  }) async {
    _log('=== signAndSendTransaction (embedded wallet) START ===');
    final solWallets = _user?.embeddedSolanaWallets;
    if (solWallets == null || solWallets.isEmpty) {
      _log('signAndSendTransaction: no embedded wallets available');
      return null;
    }
    try {
      _log('Sign+send with wallet: ${solWallets.first.address}');
      final result = await solWallets.first.provider.signAndSendTransaction(
        transaction: transactionBytes,
        cluster: cluster ?? _clusterFromRuntime(),
        rpcUrl: rpcUrl ?? RuntimeClientConfig.instance.rpcHttp,
      );
      _log('signAndSendTransaction result: ${result.runtimeType}');
      if (result is Success<String>) {
        _log('signAndSendTransaction SUCCESS: ${result.value}');
        return result.value;
      }
      _log('signAndSendTransaction FAILED');
      return null;
    } catch (e) {
      _log('signAndSendTransaction error: $e');
      return null;
    }
  }

  // ── Session ──

  PrivyUser? getUser() => _user;

  /// Get the current Privy access token (JWT) for backend auth.
  Future<String?> getAccessToken() async {
    if (_user == null) return null;
    try {
      final result = await _user!.getAccessToken();
      if (result is Success<String>) {
        return result.value;
      }
      _log('getAccessToken failed: ${result.runtimeType}');
      return null;
    } catch (e) {
      _log('getAccessToken error: $e');
      return null;
    }
  }

  /// Get the current Privy ID token for server-side wallet signing.
  /// The Privy Flutter SDK exposes this as `identityToken` on PrivyUser.
  ///
  /// By default this is strict and returns `null` if an identity token
  /// is unavailable, so we don't accidentally send an access token to
  /// Privy signer endpoints (which often fails with 400).
  Future<String?> getIdToken({bool allowAccessTokenFallback = false}) async {
    if (_user == null) return null;
    try {
      // 1. Try identity token first (required by Privy server-side signing)
      final token = _user!.identityToken;
      if (token != null && token.isNotEmpty) {
        _log('Privy: Got identity token (${token.length} chars)');
        return token;
      }

      // 2. Refresh user and try again
      _log('getIdToken: identityToken is null/empty, refreshing user...');
      await _user!.refresh();
      final refreshedToken = _user!.identityToken;
      if (refreshedToken != null && refreshedToken.isNotEmpty) {
        _log(
          'Privy: Got identity token after refresh (${refreshedToken.length} chars)',
        );
        return refreshedToken;
      }

      // 3. Optional fallback to access token (not recommended for signer calls)
      if (allowAccessTokenFallback) {
        _log(
          'WARNING: identityToken still null; falling back to access token by request.',
        );
        final accessResult = await _user!.getAccessToken();
        if (accessResult is Success<String>) {
          _log(
            'Privy: fallback access token (${accessResult.value.length} chars)',
          );
          return accessResult.value;
        }
      }

      _log(
        'getIdToken: identity token unavailable (no fallback). '
        'User should re-authenticate.',
      );
      return null;
    } catch (e) {
      _log('getIdToken error: $e');
      return null;
    }
  }

  Future<void> logout() async {
    _log('=== logout START ===');
    await _privy.logout();
    _user = null;
    _log('=== logout END ===');
  }

  Future<void> deleteAccount() async {
    _log('=== deleteAccount START ===');
    // Privy Flutter SDK doesn't expose deleteUser() client-side.
    // Account deletion requires the Privy REST API (server-side).
    // For now, we log out locally. A backend endpoint should call
    // DELETE https://auth.privy.io/api/v1/users/{user_id} with the
    // Privy app secret to fully delete the account.
    await _privy.logout();
    _user = null;
    _log('=== deleteAccount END (local only) ===');
  }
}
