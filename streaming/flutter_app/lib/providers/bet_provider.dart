import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../models/bet.dart';
import 'match_provider.dart';

class BetNotifier extends StateNotifier<List<Bet>> {
  final Ref _ref;

  BetNotifier(this._ref) : super([]);

  Future<void> refresh() async {
    final api = _ref.read(apiServiceProvider);
    final bets = await api.fetchMyBets();
    state = bets;
  }

  Future<Bet?> placeBet({
    required String matchId,
    required String fighterId,
    required double amount,
  }) async {
    final api = _ref.read(apiServiceProvider);
    final bet = await api.placeBet(
      matchId: matchId,
      fighterId: fighterId,
      amount: amount,
    );
    if (bet != null) {
      state = [bet, ...state];
    }
    return bet;
  }

  List<Bet> get activeBets =>
      state.where((b) => b.status == BetStatus.active).toList();

  List<Bet> get wonBets =>
      state.where((b) => b.status == BetStatus.won).toList();

  List<Bet> get lostBets =>
      state.where((b) => b.status == BetStatus.lost).toList();
}

final betProvider = StateNotifierProvider<BetNotifier, List<Bet>>(
  (ref) => BetNotifier(ref),
);
