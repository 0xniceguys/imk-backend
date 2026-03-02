import 'dart:convert';
import 'package:flutter/foundation.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:http/http.dart' as http;
import '../models/wallet_state.dart';
import '../services/api_service.dart';
import '../services/privy_service.dart';
import '../core/constants.dart';
import 'auth_provider.dart';

/// Solana RPC endpoint — switches to devnet when USE_DEVNET=true.
final _solanaRpc = kUseDevnet
    ? 'https://api.devnet.solana.com'
    : 'https://api.mainnet-beta.solana.com';

/// SEEKER token mint (mainnet) or USDC devnet mint for testing.
final _seekerMint = kUseDevnet
    ? '4zMMC9srt5Ri5X14GAgXhaHii3GnPAEERYPJgZJDncDU' // USDC on devnet
    : 'SKRbvo6Gf7GondiT3BbTfuRDPqLWei4j2Qy2NPGZhW3'; // SEEKER mainnet

/// Wrapped SOL mint (used for Jupiter price lookup).
const _wrappedSolMint = 'So11111111111111111111111111111111111111112';

class WalletNotifier extends StateNotifier<WalletState> {
  final PrivyService _privy;

  WalletNotifier(this._privy) : super(const WalletState());

  Future<void> loadWallet() async {
    final address = _privy.walletAddress;
    if (address == null) {
      state = const WalletState();
      return;
    }

    state = state.copyWith(solanaAddress: address, isLoading: true);

    if (kUseMockData) {
      state = WalletState(
        solanaAddress: address,
        solBalance: 1.5,
        seekerBalance: 500.0,
        solUsdValue: 300.0,
        seekerUsdValue: 50.0,
        usdcBalance: 0,
        isLoading: false,
      );
      return;
    }

    final solFuture = _fetchSolBalance(address);
    final seekerFuture = _fetchSeekerBalance(address);
    final pricesFuture = _fetchTokenPrices();

    try {
      final solBalance = await solFuture;
      final seekerBalance = await seekerFuture;
      final prices = await pricesFuture;

      state = WalletState(
        solanaAddress: address,
        solBalance: solBalance,
        seekerBalance: seekerBalance,
        solUsdValue: solBalance * (prices['sol'] ?? 0),
        seekerUsdValue: seekerBalance * (prices['seeker'] ?? 0),
        usdcBalance: 0,
        isLoading: false,
      );
    } catch (_) {
      state = WalletState(
        solanaAddress: address,
        isLoading: false,
      );
    }
  }

  // ── Price fetch ─────────────────────────────────────────────────────────

  // Jupiter v2 returns `price` as a String or double — handle both.
  static double _parsePrice(dynamic v) {
    if (v is num) return v.toDouble();
    if (v is String) return double.tryParse(v) ?? 0;
    return 0;
  }

  Future<Map<String, double>> _fetchTokenPrices() async {
    // Always fetch real prices regardless of devnet flag — devnet SOL still
    // has a real USD value (same token) and USDC is always $1.
    double solPrice = 0;
    double seekerPrice = 0;

    // ── SOL price via Jupiter v2, with CoinGecko fallback ───────────────
    try {
      final jupResp = await http.get(Uri.parse(
        'https://api.jup.ag/price/v2?ids=$_wrappedSolMint',
      )).timeout(const Duration(seconds: 8));

      if (jupResp.statusCode == 200) {
        final body = jsonDecode(jupResp.body) as Map<String, dynamic>;
        final mintData = (body['data'] as Map<String, dynamic>?)?[_wrappedSolMint];
        solPrice = _parsePrice(mintData?['price']);
        debugPrint('[Wallet] Jupiter SOL price: $solPrice (raw: ${mintData?['price']})');
      } else {
        debugPrint('[Wallet] Jupiter SOL price failed: ${jupResp.statusCode} ${jupResp.body}');
      }
    } catch (e) {
      debugPrint('[Wallet] Jupiter SOL price error: $e');
    }

    // CoinGecko fallback if Jupiter returned 0
    if (solPrice == 0) {
      try {
        final cgResp = await http.get(Uri.parse(
          'https://api.coingecko.com/api/v3/simple/price?ids=solana&vs_currencies=usd',
        )).timeout(const Duration(seconds: 8));
        if (cgResp.statusCode == 200) {
          final body = jsonDecode(cgResp.body) as Map<String, dynamic>;
          solPrice = _parsePrice(body['solana']?['usd']);
          debugPrint('[Wallet] CoinGecko SOL price fallback: $solPrice');
        }
      } catch (e) {
        debugPrint('[Wallet] CoinGecko SOL price error: $e');
      }
    }

    // ── SEEKER / USDC price ──────────────────────────────────────────────
    if (kUseDevnet) {
      // USDC devnet is always $1
      seekerPrice = 1.0;
    } else {
      try {
        final jupResp = await http.get(Uri.parse(
          'https://api.jup.ag/price/v2?ids=$_seekerMint',
        )).timeout(const Duration(seconds: 8));
        if (jupResp.statusCode == 200) {
          final body = jsonDecode(jupResp.body) as Map<String, dynamic>;
          final mintData = (body['data'] as Map<String, dynamic>?)?[_seekerMint];
          seekerPrice = _parsePrice(mintData?['price']);
          debugPrint('[Wallet] Jupiter SEEKER price: $seekerPrice');
        } else {
          debugPrint('[Wallet] Jupiter SEEKER price failed: ${jupResp.statusCode}');
        }
      } catch (e) {
        debugPrint('[Wallet] Jupiter SEEKER price error: $e');
      }
    }

    return {'sol': solPrice, 'seeker': seekerPrice};
  }

  // ── Balance fetch ───────────────────────────────────────────────────────

  Future<double> _fetchSolBalance(String address) async {
    final response = await http.post(
      Uri.parse(_solanaRpc),
      headers: {'Content-Type': 'application/json'},
      body: jsonEncode({
        'jsonrpc': '2.0',
        'id': 1,
        'method': 'getBalance',
        'params': [address],
      }),
    );
    if (response.statusCode == 200) {
      final data = jsonDecode(response.body);
      final lamports = data['result']?['value'] as int? ?? 0;
      return lamports / 1e9;
    }
    return 0;
  }

  Future<double> _fetchSeekerBalance(String address) async {
    try {
      final response = await http.post(
        Uri.parse(_solanaRpc),
        headers: {'Content-Type': 'application/json'},
        body: jsonEncode({
          'jsonrpc': '2.0',
          'id': 2,
          'method': 'getTokenAccountsByOwner',
          'params': [
            address,
            {'mint': _seekerMint},
            {'encoding': 'jsonParsed'},
          ],
        }),
      );
      if (response.statusCode == 200) {
        final data = jsonDecode(response.body);
        final accounts = data['result']?['value'] as List?;
        if (accounts != null && accounts.isNotEmpty) {
          final tokenAmount = accounts[0]['account']['data']['parsed']['info']
              ['tokenAmount'];
          return (tokenAmount['uiAmount'] as num?)?.toDouble() ?? 0;
        }
      }
    } catch (_) {}
    return 0;
  }

  // ── Public methods ───────────────────────────────────────────────────────

  Future<void> refreshBalance() async => loadWallet();

  Future<String?> signMessage(String message) async {
    return _privy.signMessage(message);
  }

  Future<String?> signTransaction(Uint8List transactionBytes) async {
    return _privy.signTransaction(transactionBytes);
  }

  Future<String?> signAndSendTransaction(Uint8List transactionBytes) async {
    final sig = await _privy.signAndSendTransaction(transactionBytes);
    if (sig != null) await refreshBalance();
    return sig;
  }

  void updateBalance(double sol) {
    state = state.copyWith(solBalance: sol);
  }

  /// Refreshes the Privy access token and sets it on [ApiService] before
  /// making sensitive calls (e.g. withdraw), so stale tokens don't cause 401s.
  Future<void> syncAuthToken(ApiService api) async {
    final token = await _privy.getAccessToken();
    if (token != null) {
      api.setAuthToken(token);
      debugPrint('[Wallet] Auth token refreshed before API call');
    } else {
      debugPrint('[Wallet] No Privy access token available — withdraw may fail');
    }
  }
}

final walletProvider = StateNotifierProvider<WalletNotifier, WalletState>(
  (ref) => WalletNotifier(ref.read(privyServiceProvider)),
);
