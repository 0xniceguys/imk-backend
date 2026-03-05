import 'package:flutter/foundation.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../core/api_exception.dart';
import '../models/bet.dart';
import 'match_provider.dart';

void _log(String msg) {
  // ignore: avoid_print
  if (kDebugMode) print('[Bet] $msg');
}

class BetNotifier extends StateNotifier<List<Bet>> {
  final Ref _ref;

  BetNotifier(this._ref) : super([]);

  Future<void> refresh() async {
    final api = _ref.read(apiServiceProvider);
    final bets = await api.fetchMyBets();
    state = bets;
  }

  /// Place a bet on-chain via Privy server-side signing.
  ///
  /// [side] is "A" for fighter1, "B" for fighter2.
  /// [privyJwt] is the user's Privy access token from PrivyProvider.
  Future<Bet?> placeBet({
    required String matchId,
    required String fighterId,
    required double amount,
    required String side,
    required String privyJwt,
  }) async {
    try {
      final api = _ref.read(apiServiceProvider);
      final bet = await api.placeBet(
        matchId: matchId,
        fighterId: fighterId,
        amount: amount,
        side: side,
        privyJwt: privyJwt,
      );
      state = [bet, ...state];
      return bet;
    } on ApiException catch (e) {
      _log('placeBet failed: ${e.code} ${e.message}');
      rethrow;
    } catch (e) {
      _log('placeBet unexpected error: $e');
      rethrow;
    }
  }

  /// Claim a won bet's SKR payout via Privy.
  Future<String?> claimBet({
    required String betId,
    required String privyJwt,
  }) async {
    try {
      final api = _ref.read(apiServiceProvider);
      final txSig = await api.claimBet(betId: betId, privyJwt: privyJwt);
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

  List<Bet> get claimableBets =>
      state.where((b) => b.isClaimable).toList();

  List<Bet> get claimedBets =>
      state.where((b) => b.isClaimed).toList();
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
