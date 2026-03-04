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

    state = state.copyWith(
      solanaAddress: address,
      isLoading: true,
      clearError: true,
    );

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
    } catch (e) {
      debugPrint('[Wallet] loadWallet error: $e');
      // Preserve last known good balances if we had them
      state = state.copyWith(
        solanaAddress: address,
        isLoading: false,
        errorMessage: 'Failed to load wallet balances. Tap refresh to retry.',
      );
    }
  }

  // ── Price fetch ─────────────────────────────────────────────────────────

  // Cache of last known good prices so we never display zero on transient failures.
  static double _lastKnownSolPrice = 0;
  static double _lastKnownSeekerPrice = 0;

  // Jupiter v2 / DexScreener / misc APIs return price as String or double.
  static double _parsePrice(dynamic v) {
    if (v is num) return v.toDouble();
    if (v is String) return double.tryParse(v) ?? 0;
    return 0;
  }

  // ── Individual price sources — all keyless / free ─────────────────────

  /// 1. Jupiter Price v2  (often 401 but keep as first attempt)
  Future<double> _solPriceJupiter() async {
    final r = await http.get(Uri.parse(
      'https://api.jup.ag/price/v2?ids=$_wrappedSolMint',
    )).timeout(const Duration(seconds: 6));
    if (r.statusCode != 200) return 0;
    final body = jsonDecode(r.body) as Map<String, dynamic>;
    final d = (body['data'] as Map<String, dynamic>?)?[_wrappedSolMint];
    return _parsePrice(d?['price']);
  }

  /// 2. CoinGecko public API (no key, 30 rpm free)
  Future<double> _solPriceCoinGecko() async {
    final r = await http.get(Uri.parse(
      'https://api.coingecko.com/api/v3/simple/price?ids=solana&vs_currencies=usd',
    )).timeout(const Duration(seconds: 6));
    if (r.statusCode != 200) return 0;
    final body = jsonDecode(r.body) as Map<String, dynamic>;
    return _parsePrice(body['solana']?['usd']);
  }

  /// 3. Binance public ticker (no key, very reliable)
  Future<double> _solPriceBinance() async {
    final r = await http.get(Uri.parse(
      'https://api.binance.com/api/v3/ticker/price?symbol=SOLUSDT',
    )).timeout(const Duration(seconds: 6));
    if (r.statusCode != 200) return 0;
    final body = jsonDecode(r.body) as Map<String, dynamic>;
    return _parsePrice(body['price']);
  }

  /// 4. Kraken public ticker (no key)
  Future<double> _solPriceKraken() async {
    final r = await http.get(Uri.parse(
      'https://api.kraken.com/0/public/Ticker?pair=SOLUSD',
    )).timeout(const Duration(seconds: 6));
    if (r.statusCode != 200) return 0;
    final body = jsonDecode(r.body) as Map<String, dynamic>;
    final result = body['result'] as Map<String, dynamic>?;
    if (result == null || result.isEmpty) return 0;
    // Kraken nests under the pair key e.g. "SOLUSD"
    final pairData = result.values.first as Map<String, dynamic>?;
    final lastTrades = pairData?['c'] as List?;
    return _parsePrice(lastTrades?.first);
  }

  /// 5. DexScreener (no key, 300 rpm)  — SOL via wrapped-SOL/USDC pair
  Future<double> _solPriceDexScreener() async {
    final r = await http.get(Uri.parse(
      'https://api.dexscreener.com/tokens/v1/solana/$_wrappedSolMint',
    )).timeout(const Duration(seconds: 6));
    if (r.statusCode != 200) return 0;
    final body = jsonDecode(r.body);
    final pairs = (body is List ? body : (body as Map)['pairs']) as List?;
    if (pairs == null || pairs.isEmpty) return 0;
    // Sort by liquidity and take the best pair
    pairs.sort((a, b) =>
        (b['liquidity']?['usd'] as num? ?? 0)
            .compareTo(a['liquidity']?['usd'] as num? ?? 0));
    return _parsePrice(pairs.first['priceUsd']);
  }

  /// 6. CoinPaprika (no key)
  Future<double> _solPriceCoinPaprika() async {
    final r = await http.get(Uri.parse(
      'https://api.coinpaprika.com/v1/tickers/sol-solana?quotes=USD',
    )).timeout(const Duration(seconds: 6));
    if (r.statusCode != 200) return 0;
    final body = jsonDecode(r.body) as Map<String, dynamic>;
    return _parsePrice(body['quotes']?['USD']?['price']);
  }

  // ── SEEKER price sources ──────────────────────────────────────────────

  /// 1. Jupiter Price v2 for SEEKER
  Future<double> _seekerPriceJupiter() async {
    final r = await http.get(Uri.parse(
      'https://api.jup.ag/price/v2?ids=$_seekerMint',
    )).timeout(const Duration(seconds: 6));
    if (r.statusCode != 200) return 0;
    final body = jsonDecode(r.body) as Map<String, dynamic>;
    final d = (body['data'] as Map<String, dynamic>?)?[_seekerMint];
    return _parsePrice(d?['price']);
  }

  /// 2. DexScreener — SEEKER by mint address (best liquidity pair)
  Future<double> _seekerPriceDexScreener() async {
    final r = await http.get(Uri.parse(
      'https://api.dexscreener.com/tokens/v1/solana/$_seekerMint',
    )).timeout(const Duration(seconds: 6));
    if (r.statusCode != 200) return 0;
    final body = jsonDecode(r.body);
    final pairs = (body is List ? body : (body as Map)['pairs']) as List?;
    if (pairs == null || pairs.isEmpty) return 0;
    pairs.sort((a, b) =>
        (b['liquidity']?['usd'] as num? ?? 0)
            .compareTo(a['liquidity']?['usd'] as num? ?? 0));
    return _parsePrice(pairs.first['priceUsd']);
  }

  /// 3. GeckoTerminal on-chain API (no key, Solana network)
  Future<double> _seekerPriceGeckoTerminal() async {
    final r = await http.get(Uri.parse(
      'https://api.geckoterminal.com/api/v2/networks/solana/tokens/$_seekerMint',
    )).timeout(const Duration(seconds: 6));
    if (r.statusCode != 200) return 0;
    final body = jsonDecode(r.body) as Map<String, dynamic>;
    return _parsePrice(
        body['data']?['attributes']?['price_usd']);
  }

  /// 4. Raydium price API (no key)
  Future<double> _seekerPriceRaydium() async {
    final r = await http.get(Uri.parse(
      'https://api-v3.raydium.io/mint/price?mints=$_seekerMint',
    )).timeout(const Duration(seconds: 6));
    if (r.statusCode != 200) return 0;
    final body = jsonDecode(r.body) as Map<String, dynamic>;
    final data = body['data'] as Map<String, dynamic>?;
    return _parsePrice(data?[_seekerMint]);
  }

  /// 5. CoinPaprika — SEEKER (symbol-based search fallback)
  Future<double> _seekerPriceCoinPaprika() async {
    // SEEKER is listed on CoinPaprika as skr-seeker
    final r = await http.get(Uri.parse(
      'https://api.coinpaprika.com/v1/tickers/skr-seeker?quotes=USD',
    )).timeout(const Duration(seconds: 6));
    if (r.statusCode != 200) return 0;
    final body = jsonDecode(r.body) as Map<String, dynamic>;
    return _parsePrice(body['quotes']?['USD']?['price']);
  }

  // ── Orchestrator ────────────────────────────────────────────────────────

  Future<Map<String, double>> _fetchTokenPrices() async {
    double solPrice = 0;
    double seekerPrice = 0;

    // ── SOL: try each source in order, stop on first non-zero result ──────
    final solSources = <String, Future<double> Function()>{
      'Jupiter': _solPriceJupiter,
      'CoinGecko': _solPriceCoinGecko,
      'Binance': _solPriceBinance,
      'Kraken': _solPriceKraken,
      'DexScreener': _solPriceDexScreener,
      'CoinPaprika': _solPriceCoinPaprika,
    };

    for (final entry in solSources.entries) {
      if (solPrice > 0) break;
      try {
        final p = await entry.value();
        if (p > 0) {
          solPrice = p;
          debugPrint('[Wallet] SOL price via ${entry.key}: \$$solPrice');
        } else {
          debugPrint('[Wallet] SOL price ${entry.key}: no data');
        }
      } catch (e) {
        debugPrint('[Wallet] SOL price ${entry.key} error: $e');
      }
    }

    // Fall back to last known good price
    if (solPrice <= 0 && _lastKnownSolPrice > 0) {
      solPrice = _lastKnownSolPrice;
      debugPrint('[Wallet] SOL price: using cached \$$solPrice');
    } else if (solPrice > 0) {
      _lastKnownSolPrice = solPrice;
    }

    // ── SEEKER: try each source, stop on first non-zero result ────────────
    if (kUseDevnet) {
      seekerPrice = 1.0; // USDC devnet is always $1
    } else {
      final seekerSources = <String, Future<double> Function()>{
        'Jupiter': _seekerPriceJupiter,
        'DexScreener': _seekerPriceDexScreener,
        'GeckoTerminal': _seekerPriceGeckoTerminal,
        'Raydium': _seekerPriceRaydium,
        'CoinPaprika': _seekerPriceCoinPaprika,
      };

      for (final entry in seekerSources.entries) {
        if (seekerPrice > 0) break;
        try {
          final p = await entry.value();
          if (p > 0) {
            seekerPrice = p;
            debugPrint('[Wallet] SEEKER price via ${entry.key}: \$$seekerPrice');
          } else {
            debugPrint('[Wallet] SEEKER price ${entry.key}: no data');
          }
        } catch (e) {
          debugPrint('[Wallet] SEEKER price ${entry.key} error: $e');
        }
      }

      if (seekerPrice <= 0 && _lastKnownSeekerPrice > 0) {
        seekerPrice = _lastKnownSeekerPrice;
        debugPrint('[Wallet] SEEKER price: using cached \$$seekerPrice');
      } else if (seekerPrice > 0) {
        _lastKnownSeekerPrice = seekerPrice;
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
        'params': [
          address,
          {'commitment': 'confirmed'},
        ],
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
            {'encoding': 'jsonParsed', 'commitment': 'confirmed'},
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

  /// Polls for an updated balance with exponential backoff.
  /// Compares against the current (pre-withdrawal) balance snapshot and stops
  /// once the balance actually changes, or after [maxAttempts] tries.
  Future<void> refreshWithRetry({int maxAttempts = 3}) async {
    final address = _privy.walletAddress;
    if (address == null) return;

    final prevSol = state.solBalance;
    final prevSeeker = state.seekerBalance;
    int delay = 2;

    for (int i = 0; i < maxAttempts; i++) {
      debugPrint('[Wallet] Retry ${i + 1}/$maxAttempts — waiting ${delay}s...');
      await Future.delayed(Duration(seconds: delay));

      await loadWallet();

      // Check if balance actually changed
      if (state.solBalance != prevSol || state.seekerBalance != prevSeeker) {
        debugPrint('[Wallet] Balance updated after ${i + 1} retries');
        return;
      }

      delay *= 2; // exponential backoff: 2s → 4s → 8s
    }

    debugPrint('[Wallet] Balance unchanged after $maxAttempts retries — '
        'may update on next manual refresh');
  }

  Future<String?> signMessage(String message) async {
    return _privy.signMessage(message);
  }

  Future<String?> signTransaction(Uint8List transactionBytes) async {
    return _privy.signTransaction(transactionBytes);
  }

  Future<String?> signAndSendTransaction(Uint8List transactionBytes) async {
    final sig = await _privy.signAndSendTransaction(transactionBytes);
    if (sig != null) await refreshWithRetry();
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
