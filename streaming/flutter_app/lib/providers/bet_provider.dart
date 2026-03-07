import 'dart:convert';
import 'dart:math' as math;

import 'package:flutter/foundation.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../core/api_exception.dart';
import '../core/runtime_client_config.dart';
import '../models/bet.dart';
import 'auth_provider.dart';
import 'match_provider.dart';
import 'wallet_provider.dart';

void _log(String msg) {
  // ignore: avoid_print
  if (kDebugMode) print('[Bet] $msg');
}

class BetNotifier extends StateNotifier<List<Bet>> {
  final Ref _ref;

  BetNotifier(this._ref) : super([]);

  Future<void> _refreshAuthHeaderOrThrow() async {
    final privy = _ref.read(privyServiceProvider);
    final token = await privy.getAccessToken();
    if (token == null || token.isEmpty) {
      throw const ApiException(
        code: 'Unauthorized',
        message: 'Session expired. Please log in again.',
        statusCode: 401,
      );
    }
    _ref.read(apiServiceProvider).setAuthToken(token);
  }

  Future<void> refresh() async {
    final api = _ref.read(apiServiceProvider);
    final bets = await api.fetchMyBets();
    state = bets;
  }

  double _baseUnitsToUi(int baseUnits, int decimals) {
    if (decimals <= 0) return baseUnits.toDouble();
    return baseUnits / math.pow(10, decimals);
  }

  String _trimAmount(double value) {
    final s = value.toStringAsFixed(6);
    return s.replaceFirst(RegExp(r'\.?0+$'), '');
  }

  /// Place a bet on-chain via client-side signing (prepare -> sign -> broadcast).
  ///
  /// [side] is "A" for fighter1, "B" for fighter2.
  Future<Bet?> placeBet({
    required String matchId,
    required String fighterId,
    required double amount,
    required String side,
  }) async {
    try {
      final cfg = RuntimeClientConfig.instance;
      final minUi = _baseUnitsToUi(cfg.minBetBaseUnits, cfg.tokenDecimals);
      final maxUi = _baseUnitsToUi(cfg.maxBetBaseUnits, cfg.tokenDecimals);
      const eps = 1e-9;
      if (amount + eps < minUi) {
        throw ApiException(
          code: 'ValidationError',
          message: 'Minimum bet is ${_trimAmount(minUi)} ${cfg.tokenSymbol}',
          statusCode: 400,
        );
      }
      if (amount - eps > maxUi) {
        throw ApiException(
          code: 'ValidationError',
          message: 'Maximum bet is ${_trimAmount(maxUi)} ${cfg.tokenSymbol}',
          statusCode: 400,
        );
      }

      await _refreshAuthHeaderOrThrow();
      final api = _ref.read(apiServiceProvider);
      final wallet = _ref.read(walletProvider.notifier);

      final unsignedTxBase64 = await api.prepareBet(
        matchId: matchId,
        fighterId: fighterId,
        amount: amount,
        side: side,
      );
      final txBytes = base64Decode(unsignedTxBase64);
      final signedTxBase64 = await wallet.signTransaction(txBytes);
      if (signedTxBase64 == null || signedTxBase64.isEmpty) {
        throw const ApiException(
          code: 'SignFailed',
          message: 'Failed to sign transaction',
          statusCode: 400,
        );
      }

      final bet = await api.broadcastBet(
        matchId: matchId,
        signedTransactionBase64: signedTxBase64,
      );
      state = [bet, ...state];
      // Refresh match pools/odds shortly after a successful on-chain bet.
      await _ref.read(matchProvider.notifier).refresh();
      return bet;
    } on ApiException catch (e) {
      _log('placeBet failed: ${e.code} ${e.message}');
      rethrow;
    } catch (e) {
      _log('placeBet unexpected error: $e');
      rethrow;
    }
  }

  /// Claim a won bet's payout via client-side signing (prepare -> sign -> broadcast).
  Future<String?> claimBet({required String betId}) async {
    try {
      await _refreshAuthHeaderOrThrow();
      final api = _ref.read(apiServiceProvider);
      final wallet = _ref.read(walletProvider.notifier);

      final unsignedTxBase64 = await api.prepareClaim(betId: betId);
      final txBytes = base64Decode(unsignedTxBase64);
      final signedTxBase64 = await wallet.signTransaction(txBytes);
      if (signedTxBase64 == null || signedTxBase64.isEmpty) {
        throw const ApiException(
          code: 'SignFailed',
          message: 'Failed to sign claim transaction',
          statusCode: 400,
        );
      }

      final txSig = await api.broadcastClaim(
        betId: betId,
        signedTransactionBase64: signedTxBase64,
      );
      // Refresh to pick up CLAIMED status
      await refresh();
      return txSig;
    } on ApiException catch (e) {
      _log('claimBet failed: ${e.code} ${e.message}');
      rethrow;
    } catch (e) {
      _log('claimBet unexpected error: $e');
      rethrow;
    }
  }

  List<Bet> get activeBets =>
      state.where((b) => b.status == BetStatus.active).toList();

  List<Bet> get wonBets =>
      state.where((b) => b.status == BetStatus.won).toList();

  List<Bet> get lostBets =>
      state.where((b) => b.status == BetStatus.lost).toList();

  List<Bet> get claimableBets => state.where((b) => b.isClaimable).toList();

  List<Bet> get claimedBets => state.where((b) => b.isClaimed).toList();
}

class BetSummary {
  final int totalBets;
  final double totalWagered;
  final double totalWon;
  final double netPnl;
  final double winRate;

  const BetSummary({
    required this.totalBets,
    required this.totalWagered,
    required this.totalWon,
    required this.netPnl,
    required this.winRate,
  });

  factory BetSummary.fromJson(Map<String, dynamic> json) {
    return BetSummary(
      totalBets: json['total_bets'] as int? ?? 0,
      totalWagered: (json['total_wagered'] as num?)?.toDouble() ?? 0,
      totalWon: (json['total_won'] as num?)?.toDouble() ?? 0,
      netPnl: (json['net_pnl'] as num?)?.toDouble() ?? 0,
      winRate: (json['win_rate'] as num?)?.toDouble() ?? 0,
    );
  }
}

final betProvider = StateNotifierProvider<BetNotifier, List<Bet>>(
  (ref) => BetNotifier(ref),
);

final betSummaryProvider = FutureProvider<BetSummary?>((ref) async {
  final api = ref.read(apiServiceProvider);
  final raw = await api.fetchBetsSummary();
  if (raw == null) return null;
  return BetSummary.fromJson(raw);
});
