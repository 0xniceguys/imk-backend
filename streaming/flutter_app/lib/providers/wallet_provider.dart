import 'dart:async';
import 'dart:convert';
import 'package:flutter/foundation.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:http/http.dart' as http;
import '../models/wallet_state.dart';
import '../services/api_service.dart';
import '../services/privy_service.dart';
import '../core/constants.dart';
import '../core/runtime_client_config.dart';
import 'auth_provider.dart';

/// Wrapped SOL mint (used for Jupiter price lookup).
const _wrappedSolMint = 'So11111111111111111111111111111111111111112';

class WalletNotifier extends StateNotifier<WalletState> {
  final PrivyService _privy;
  Timer? _autoRefreshTimer;
  bool _isRefreshInFlight = false;

  WalletNotifier(this._privy) : super(const WalletState()) {
    _startAutoRefresh();
  }

  RuntimeClientConfig get _cfg => RuntimeClientConfig.instance;
  String get _skrMint => _cfg.skrMint;
  bool get _isDevnet => _cfg.isDevnet;
  int get _tokenDecimals => _cfg.tokenDecimals;

  static const Duration _autoRefreshEvery = Duration(seconds: 12);

  void _startAutoRefresh() {
    _autoRefreshTimer?.cancel();
    _autoRefreshTimer = Timer.periodic(_autoRefreshEvery, (_) async {
      if (_isRefreshInFlight) return;
      final address = _privy.walletAddress;
      if (address == null || address.isEmpty) return;
      _isRefreshInFlight = true;
      try {
        await _loadWallet(showLoading: false);
      } finally {
        _isRefreshInFlight = false;
      }
    });
  }

  @override
  void dispose() {
    _autoRefreshTimer?.cancel();
    super.dispose();
  }

  Future<void> loadWallet() async {
    await _loadWallet(showLoading: true);
  }

  Future<void> _loadWallet({required bool showLoading}) async {
    final address = _privy.walletAddress;
    if (address == null) {
      state = const WalletState();
      return;
    }

    if (showLoading) {
      state = state.copyWith(
        solanaAddress: address,
        isLoading: true,
        clearError: true,
      );
    } else {
      state = state.copyWith(solanaAddress: address, clearError: true);
    }

    if (kUseMockData) {
      state = WalletState(
        solanaAddress: address,
        solBalance: 1.5,
        seekerBalance: 500.0,
        solUsdValue: 300.0,
        seekerUsdValue: 500.0 * kDevnetSkrPriceUsd,
        seekerUsdPrice: kDevnetSkrPriceUsd,
        seekerSymbol: _cfg.tokenSymbol,
        isDevnet: _cfg.isDevnet,
        usdcBalance: 0,
        isLoading: false,
      );
      return;
    }

    final solFuture = _fetchSolBalance(address, rpcHttp: _cfg.rpcHttp);
    final seekerFuture = _fetchSeekerBalance(
      address,
      rpcHttp: _cfg.rpcHttp,
      seekerMint: _cfg.skrMint,
    );
    final pricesFuture = _fetchTokenPrices(seekerMint: _cfg.skrMint);

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
        seekerUsdPrice: prices['seeker'] ?? 0,
        seekerSymbol: _cfg.tokenSymbol,
        isDevnet: _cfg.isDevnet,
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
    final r = await http
        .get(Uri.parse('https://api.jup.ag/price/v2?ids=$_wrappedSolMint'))
        .timeout(const Duration(seconds: 6));
    if (r.statusCode != 200) return 0;
    final body = jsonDecode(r.body) as Map<String, dynamic>;
    final d = (body['data'] as Map<String, dynamic>?)?[_wrappedSolMint];
    return _parsePrice(d?['price']);
  }

  /// 2. CoinGecko public API (no key, 30 rpm free)
  Future<double> _solPriceCoinGecko() async {
    final r = await http
        .get(
          Uri.parse(
            'https://api.coingecko.com/api/v3/simple/price?ids=solana&vs_currencies=usd',
          ),
        )
        .timeout(const Duration(seconds: 6));
    if (r.statusCode != 200) return 0;
    final body = jsonDecode(r.body) as Map<String, dynamic>;
    return _parsePrice(body['solana']?['usd']);
  }

  /// 3. Binance public ticker (no key, very reliable)
  Future<double> _solPriceBinance() async {
    final r = await http
        .get(
          Uri.parse(
            'https://api.binance.com/api/v3/ticker/price?symbol=SOLUSDT',
          ),
        )
        .timeout(const Duration(seconds: 6));
    if (r.statusCode != 200) return 0;
    final body = jsonDecode(r.body) as Map<String, dynamic>;
    return _parsePrice(body['price']);
  }

  /// 4. Kraken public ticker (no key)
  Future<double> _solPriceKraken() async {
    final r = await http
        .get(Uri.parse('https://api.kraken.com/0/public/Ticker?pair=SOLUSD'))
        .timeout(const Duration(seconds: 6));
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
    final r = await http
        .get(
          Uri.parse(
            'https://api.dexscreener.com/tokens/v1/solana/$_wrappedSolMint',
          ),
        )
        .timeout(const Duration(seconds: 6));
    if (r.statusCode != 200) return 0;
    final body = jsonDecode(r.body);
    final pairs = (body is List ? body : (body as Map)['pairs']) as List?;
    if (pairs == null || pairs.isEmpty) return 0;
    // Sort by liquidity and take the best pair
    pairs.sort(
      (a, b) => (b['liquidity']?['usd'] as num? ?? 0).compareTo(
        a['liquidity']?['usd'] as num? ?? 0,
      ),
    );
    return _parsePrice(pairs.first['priceUsd']);
  }

  /// 6. CoinPaprika (no key)
  Future<double> _solPriceCoinPaprika() async {
    final r = await http
        .get(
          Uri.parse(
            'https://api.coinpaprika.com/v1/tickers/sol-solana?quotes=USD',
          ),
        )
        .timeout(const Duration(seconds: 6));
    if (r.statusCode != 200) return 0;
    final body = jsonDecode(r.body) as Map<String, dynamic>;
    return _parsePrice(body['quotes']?['USD']?['price']);
  }

  // ── SKR price sources ─────────────────────────────────────────────────

  /// 1. Jupiter Price v2 for SKR mint.
  Future<double> _skrPriceJupiter() async {
    final r = await http
        .get(Uri.parse('https://api.jup.ag/price/v2?ids=$_skrMint'))
        .timeout(const Duration(seconds: 6));
    if (r.statusCode != 200) return 0;
    final body = jsonDecode(r.body) as Map<String, dynamic>;
    final d = (body['data'] as Map<String, dynamic>?)?[_skrMint];
    return _parsePrice(d?['price']);
  }

  /// 2. DexScreener by mint address (best liquidity pair).
  Future<double> _skrPriceDexScreener() async {
    final r = await http
        .get(
          Uri.parse('https://api.dexscreener.com/tokens/v1/solana/$_skrMint'),
        )
        .timeout(const Duration(seconds: 6));
    if (r.statusCode != 200) return 0;
    final body = jsonDecode(r.body);
    final pairs = (body is List ? body : (body as Map)['pairs']) as List?;
    if (pairs == null || pairs.isEmpty) return 0;
    pairs.sort(
      (a, b) => (b['liquidity']?['usd'] as num? ?? 0).compareTo(
        a['liquidity']?['usd'] as num? ?? 0,
      ),
    );
    return _parsePrice(pairs.first['priceUsd']);
  }

  /// 3. GeckoTerminal on-chain API (no key, Solana network).
  Future<double> _skrPriceGeckoTerminal() async {
    final r = await http
        .get(
          Uri.parse(
            'https://api.geckoterminal.com/api/v2/networks/solana/tokens/$_skrMint',
          ),
        )
        .timeout(const Duration(seconds: 6));
    if (r.statusCode != 200) return 0;
    final body = jsonDecode(r.body) as Map<String, dynamic>;
    return _parsePrice(body['data']?['attributes']?['price_usd']);
  }

  /// 4. Raydium price API (no key).
  Future<double> _skrPriceRaydium() async {
    final r = await http
        .get(Uri.parse('https://api-v3.raydium.io/mint/price?mints=$_skrMint'))
        .timeout(const Duration(seconds: 6));
    if (r.statusCode != 200) return 0;
    final body = jsonDecode(r.body) as Map<String, dynamic>;
    final data = body['data'] as Map<String, dynamic>?;
    return _parsePrice(data?[_skrMint]);
  }

  /// 5. CoinPaprika fallback for SKR symbol.
  Future<double> _skrPriceCoinPaprika() async {
    final r = await http
        .get(
          Uri.parse(
            'https://api.coinpaprika.com/v1/tickers/skr-seeker?quotes=USD',
          ),
        )
        .timeout(const Duration(seconds: 6));
    if (r.statusCode != 200) return 0;
    final body = jsonDecode(r.body) as Map<String, dynamic>;
    return _parsePrice(body['quotes']?['USD']?['price']);
  }

  // ── Orchestrator ────────────────────────────────────────────────────────

  /// Race multiple price sources in parallel. Returns the first non-zero
  /// result, or 0 if all fail or all return 0. Worst case = 1 timeout window
  /// (6 s), not N × timeout (up to 36 s with a sequential loop).
  Future<double> _racePrice(
    Map<String, Future<double> Function()> sources,
    String label,
  ) async {
    if (sources.isEmpty) return 0;

    final completer = Completer<double>();
    var remaining = sources.length;

    for (final entry in sources.entries) {
      entry.value().then((p) {
        if (p > 0 && !completer.isCompleted) {
          debugPrint('[Wallet] $label price via ${entry.key}: \$$p');
          completer.complete(p);
        }
      }).catchError((e) {
        debugPrint('[Wallet] $label price ${entry.key} error: $e');
      }).whenComplete(() {
        remaining--;
        if (remaining == 0 && !completer.isCompleted) {
          // All sources returned 0 or failed.
          completer.complete(0.0);
        }
      });
    }

    return completer.future;
  }

  Future<Map<String, double>> _fetchTokenPrices({
    required String seekerMint,
  }) async {
    // ── SOL: race all sources in parallel ────────────────────────────────
    var solPrice = await _racePrice(
      {
        'Jupiter': _solPriceJupiter,
        'CoinGecko': _solPriceCoinGecko,
        'Binance': _solPriceBinance,
        'Kraken': _solPriceKraken,
        'DexScreener': _solPriceDexScreener,
        'CoinPaprika': _solPriceCoinPaprika,
      },
      'SOL',
    );

    if (solPrice <= 0 && _lastKnownSolPrice > 0) {
      solPrice = _lastKnownSolPrice;
      debugPrint('[Wallet] SOL price: using cached \$$solPrice');
    } else if (solPrice > 0) {
      _lastKnownSolPrice = solPrice;
    }

    // ── SKR: race all sources in parallel ────────────────────────────────
    double seekerPrice;
    if (_isDevnet) {
      seekerPrice = kDevnetSkrPriceUsd;
    } else {
      seekerPrice = await _racePrice(
        {
          'Jupiter': _skrPriceJupiter,
          'DexScreener': _skrPriceDexScreener,
          'GeckoTerminal': _skrPriceGeckoTerminal,
          'Raydium': _skrPriceRaydium,
          'CoinPaprika': _skrPriceCoinPaprika,
        },
        'SKR',
      );

      if (seekerPrice <= 0 && _lastKnownSeekerPrice > 0) {
        seekerPrice = _lastKnownSeekerPrice;
        debugPrint('[Wallet] SKR price: using cached \$$seekerPrice');
      } else if (seekerPrice > 0) {
        _lastKnownSeekerPrice = seekerPrice;
      }
    }

    return {'sol': solPrice, 'seeker': seekerPrice};
  }

  // ── Balance fetch ───────────────────────────────────────────────────────

  Future<double> _fetchSolBalance(
    String address, {
    required String rpcHttp,
  }) async {
    final response = await http.post(
      Uri.parse(rpcHttp),
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

  Future<double> _fetchSeekerBalance(
    String address, {
    required String rpcHttp,
    required String seekerMint,
  }) async {
    try {
      final response = await http.post(
        Uri.parse(rpcHttp),
        headers: {'Content-Type': 'application/json'},
        body: jsonEncode({
          'jsonrpc': '2.0',
          'id': 2,
          'method': 'getTokenAccountsByOwner',
          'params': [
            address,
            {'mint': seekerMint},
            {'encoding': 'jsonParsed', 'commitment': 'confirmed'},
          ],
        }),
      );
      if (response.statusCode == 200) {
        final data = jsonDecode(response.body);
        final accounts = data['result']?['value'] as List?;
        if (accounts != null && accounts.isNotEmpty) {
          BigInt rawTotal = BigInt.zero;
          for (final account in accounts) {
            final tokenAmount =
                account['account']?['data']?['parsed']?['info']?['tokenAmount']
                    as Map<String, dynamic>?;
            if (tokenAmount == null) continue;
            final raw = tokenAmount['amount']?.toString() ?? '0';
            rawTotal += BigInt.tryParse(raw) ?? BigInt.zero;
          }
          return _baseUnitsToUi(rawTotal, _tokenDecimals);
        }
      }
    } catch (_) {}
    return 0;
  }

  double _baseUnitsToUi(BigInt raw, int decimals) {
    if (raw == BigInt.zero) return 0;
    if (decimals <= 0) return raw.toDouble();
    final scale = BigInt.from(10).pow(decimals);
    final whole = raw ~/ scale;
    final fraction = raw % scale;
    return whole.toDouble() + (fraction.toDouble() / scale.toDouble());
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

      await _loadWallet(showLoading: false);

      // Check if balance actually changed
      if (state.solBalance != prevSol || state.seekerBalance != prevSeeker) {
        debugPrint('[Wallet] Balance updated after ${i + 1} retries');
        return;
      }

      delay *= 2; // exponential backoff: 2s → 4s → 8s
    }

    debugPrint(
      '[Wallet] Balance unchanged after $maxAttempts retries — '
      'may update on next manual refresh',
    );
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
      debugPrint(
        '[Wallet] No Privy access token available — withdraw may fail',
      );
    }
  }
}

final walletProvider = StateNotifierProvider<WalletNotifier, WalletState>(
  (ref) => WalletNotifier(ref.read(privyServiceProvider)),
);
