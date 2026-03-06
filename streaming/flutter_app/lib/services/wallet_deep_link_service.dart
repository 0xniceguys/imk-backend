import 'dart:async';
import 'dart:convert';

import 'package:flutter/foundation.dart';

import 'package:pinenacl/x25519.dart' as nacl;
import 'package:url_launcher/url_launcher.dart';

import '../utils/base58.dart';

void _log(String msg) {
  // ignore: avoid_print
  if (kDebugMode) print('[WalletDeepLink] $msg');
}

/// Supported external Solana wallets.
enum SolanaWallet {
  phantom('https://phantom.app/ul/v1'),
  solflare('https://solflare.com/ul/v1');

  const SolanaWallet(this.baseUrl);
  final String baseUrl;
}

/// Handles Phantom / Solflare deep link wallet connection protocol.
///
/// Flow:
///  1. [connect] → opens wallet app, user approves, returns wallet address
///  2. [signMessage] → sends message to wallet, user signs, returns signature
///
/// Both Phantom and Solflare implement the same NaCl-encrypted deep link
/// protocol (X25519 key exchange + XSalsa20-Poly1305).
class WalletDeepLinkService {
  nacl.PrivateKey? _dappKey;
  nacl.Box? _sharedSecret;
  String? _session;
  String? _walletAddress;
  SolanaWallet? _activeWallet;

  Completer<Uri>? _pendingCallback;

  // ── Cold-start buffering ─────────────────────────────────────────────────
  // When the OS kills the app and Phantom redirects back as a cold start,
  // the deep link arrives in main() BEFORE connect() or signMessage() creates
  // the _pendingCallback Completer.  We store it here and replay it the
  // moment a Completer is registered.
  Uri? _bufferedDeepLink;

  /// The wallet address obtained after [connect].
  String? get walletAddress => _walletAddress;

  /// Whether we have an active connection session.
  bool get isConnected => _session != null && _walletAddress != null;

  // ── Deep link handler (called from app_links listener) ──────────────────

  /// Route an incoming deep link URI to the pending operation.
  ///
  /// If no operation is waiting yet (cold-start race), the URI is buffered
  /// and replayed automatically when [connect] or [signMessage] arms the
  /// Completer.
  void handleDeepLink(Uri uri) {
    _log('Received deep link: $uri');

    // Check for error responses from wallet
    final errorCode = uri.queryParameters['errorCode'];
    if (errorCode != null) {
      final errorMsg =
          uri.queryParameters['errorMessage'] ?? 'Wallet rejected request';
      _log('Wallet error: $errorCode - $errorMsg');
      if (_pendingCallback != null && !_pendingCallback!.isCompleted) {
        _pendingCallback!.completeError(Exception(errorMsg));
        _pendingCallback = null;
      } else {
        // No one is listening yet — clear any buffer so we don't replay error
        _bufferedDeepLink = null;
      }
      return;
    }

    if (_pendingCallback != null && !_pendingCallback!.isCompleted) {
      _log('Completing pending callback with: $uri');
      _pendingCallback!.complete(uri);
      _pendingCallback = null;
    } else {
      // No Completer yet — buffer for replay
      _log('No pending callback — buffering deep link for replay: $uri');
      _bufferedDeepLink = uri;
    }
  }

  // ── Completer registration helper ────────────────────────────────────────

  /// Create a fresh [Completer] and immediately replay any buffered deep link.
  Completer<Uri> _arm() {
    _pendingCallback = Completer<Uri>();
    if (_bufferedDeepLink != null) {
      final buffered = _bufferedDeepLink!;
      _bufferedDeepLink = null;
      _log('Replaying buffered deep link: $buffered');
      // Schedule microsecond-later to ensure the caller can await before resolve
      Future.microtask(() => handleDeepLink(buffered));
    }
    return _pendingCallback!;
  }

  // ── Connect ──────────────────────────────────────────────────────────────

  /// Opens the wallet app and requests connection approval.
  /// Returns the user's Solana wallet address.
  Future<String> connect({
    SolanaWallet wallet = SolanaWallet.phantom,
    String cluster = 'mainnet-beta',
  }) async {
    _activeWallet = wallet;
    _dappKey = nacl.PrivateKey.generate();
    final dappPubBytes = Uint8List.fromList(_dappKey!.publicKey);
    final dappPubKeyB58 = base58Encode(dappPubBytes);

    _log('connect: dappPub (FULL)=$dappPubKeyB58');
    _log('connect: dappPub bytes=${dappPubBytes.length}');

    final connectUrl = Uri.parse('${wallet.baseUrl}/connect').replace(
      queryParameters: {
        'app_url': 'https://immortalkombat.com',
        'dapp_encryption_public_key': dappPubKeyB58,
        'redirect_link': 'imk://callback/connect',
        'cluster': cluster,
      },
    );

    _log('Opening ${wallet.name} connect: $connectUrl');

    // Arm Completer BEFORE launching URL so buffered cold-start links replay
    final completer = _arm();

    final launched = await launchUrl(
      connectUrl,
      mode: LaunchMode.externalApplication,
    );
    if (!launched) {
      _pendingCallback = null;
      throw Exception('${wallet.name} could not be opened. Is it installed?');
    }

    // Wait for wallet to redirect back
    final callbackUri = await completer.future.timeout(
      const Duration(minutes: 2),
      onTimeout: () {
        throw Exception('Wallet connection timed out');
      },
    );

    // Parse and decrypt response
    final phantomPkB58 =
        callbackUri.queryParameters['phantom_encryption_public_key'];
    final nonceB58 = callbackUri.queryParameters['nonce'];
    final dataB58 = callbackUri.queryParameters['data'];
    if (phantomPkB58 == null || nonceB58 == null || dataB58 == null) {
      throw Exception('Wallet response missing required parameters');
    }

    final theirPublicKey = base58Decode(phantomPkB58);

    // Create shared secret box (reused for all subsequent encrypt/decrypt)
    _sharedSecret = nacl.Box(
      myPrivateKey: _dappKey!,
      theirPublicKey: nacl.PublicKey(theirPublicKey),
    );

    final nonce = base58Decode(nonceB58);
    final encryptedData = base58Decode(dataB58);

    final decrypted = _decryptWith(nonce, encryptedData);
    final json = jsonDecode(utf8.decode(decrypted)) as Map<String, dynamic>;

    _session = json['session'] as String;
    _walletAddress = json['public_key'] as String;

    _log('=== CONNECT SUCCESS ===');
    _log('Wallet address: $_walletAddress');
    _log('Session (${_session!.length} chars): $_session');
    _log('Their PK: ${theirPublicKey.length} bytes');
    _log('Our PK: $dappPubKeyB58');
    _log('Cluster param sent: $cluster');

    // Self-test: verify our encrypt→decrypt round-trip works
    final testPayload = Uint8List.fromList(utf8.encode('{"test":"hello"}'));
    final testNonce = nacl.PineNaClUtils.randombytes(24);
    final testEnc = _sharedSecret!.encrypt(testPayload, nonce: testNonce);
    final testCipher = testEnc.cipherText.asTypedList;
    final testNonceOut = testEnc.nonce.asTypedList;
    final testDec = _decryptWith(testNonceOut, testCipher);
    final roundTrip = utf8.decode(testDec);
    _log(
      'Encrypt self-test: ${roundTrip == '{"test":"hello"}' ? "PASS" : "FAIL: $roundTrip"}',
    );

    return _walletAddress!;
  }

  // ── Sign Message ─────────────────────────────────────────────────────────

  /// Sends a message to the wallet app for signing.
  /// Returns the base58-encoded signature.
  Future<String> signMessage(String message, {SolanaWallet? wallet}) async {
    wallet ??= _activeWallet ?? SolanaWallet.phantom;

    if (_session == null || _sharedSecret == null || _dappKey == null) {
      throw Exception('Not connected. Call connect() first.');
    }

    // Build payload to encrypt (matching phantom_connect package format)
    final messageBytes = Uint8List.fromList(utf8.encode(message));
    final msgB58 = base58Encode(messageBytes);
    _log(
      'signMessage: message length=${message.length} '
      'msgB58 length=${msgB58.length}',
    );
    _log(
      'signMessage: session=${_session!.substring(0, 20)}... '
      '(${_session!.length} chars)',
    );

    final payloadMap = {
      'session': _session,
      'message': msgB58,
      'display': 'utf8',
    };
    final payloadJson = jsonEncode(payloadMap);
    _log('signMessage: payload JSON length=${payloadJson.length}');

    // Encrypt payload with explicit nonce (matching phantom_connect approach)
    final encNonce = nacl.PineNaClUtils.randombytes(24);
    final payloadBytes = Uint8List.fromList(utf8.encode(payloadJson));
    final encrypted = _sharedSecret!.encrypt(payloadBytes, nonce: encNonce);
    final encCipher = encrypted.cipherText.asTypedList;
    _log(
      'signMessage: nonce=${encNonce.length}B '
      'cipher=${encCipher.length}B (plaintext=${payloadBytes.length}B)',
    );

    final dappPubB58 = base58Encode(Uint8List.fromList(_dappKey!.publicKey));
    final nonceB58 = base58Encode(Uint8List.fromList(encNonce));
    final payloadB58 = base58Encode(Uint8List.fromList(encCipher));

    _log('signMessage: dappPub (FULL)=$dappPubB58');

    final signUrl = Uri(
      scheme: 'https',
      host: wallet == SolanaWallet.phantom ? 'phantom.app' : 'solflare.com',
      path: '/ul/v1/signMessage',
      queryParameters: {
        'dapp_encryption_public_key': dappPubB58,
        'nonce': nonceB58,
        'redirect_link': 'imk://callback/sign',
        'payload': payloadB58,
      },
    );

    _log('signMessage: nonce b58=${nonceB58.length} chars');
    _log('signMessage: payload b58=${payloadB58.length} chars');
    _log('signMessage: URL total=${signUrl.toString().length} chars');
    _log('signMessage: full URL=$signUrl');

    // Arm Completer BEFORE launching URL so buffered cold-start links replay
    final completer = _arm();
    await launchUrl(signUrl, mode: LaunchMode.externalApplication);

    // Wait for wallet to redirect back
    final callbackUri = await completer.future.timeout(
      const Duration(minutes: 2),
      onTimeout: () {
        throw Exception('Message signing timed out');
      },
    );

    // Decrypt response
    final respNonceB58 = callbackUri.queryParameters['nonce'];
    final respDataB58 = callbackUri.queryParameters['data'];
    if (respNonceB58 == null || respDataB58 == null) {
      throw Exception('Sign response missing required parameters');
    }
    final respNonce = base58Decode(respNonceB58);
    final respData = base58Decode(respDataB58);

    _log('signMessage: decrypting response...');
    _log(
      'signMessage: respNonce=${respNonce.length}B respData=${respData.length}B',
    );
    final decrypted = _decryptWith(respNonce, respData);
    final jsonStr = utf8.decode(decrypted);
    _log('signMessage: decrypted response JSON: $jsonStr');
    final json = jsonDecode(jsonStr) as Map<String, dynamic>;

    final signature = json['signature'] as String;
    _log('signMessage SUCCESS');
    _log('signMessage: signature=$signature');
    _log('signMessage: signature length=${signature.length} chars');
    // Decode to check byte length (ed25519 sig = 64 bytes)
    final sigBytes = base58Decode(signature);
    _log(
      'signMessage: signature decoded=${sigBytes.length} bytes (expect 64 for ed25519)',
    );
    return signature;
  }

  /// Disconnect / reset state.
  void disconnect() {
    _dappKey = null;
    _sharedSecret = null;
    _session = null;
    _walletAddress = null;
    _activeWallet = null;
    _pendingCallback = null;
    _bufferedDeepLink = null;
  }

  // ── NaCl encryption helpers ──────────────────────────────────────────────

  Uint8List _decryptWith(Uint8List nonce, Uint8List ciphertext) {
    return _sharedSecret!.decrypt(
      nacl.ByteList(ciphertext),
      nonce: Uint8List.fromList(nonce),
    );
  }
}
