import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../screens/fighter_details_screen.dart' show FighterVsParams;
import 'match_provider.dart';

// ── Fighter Stats Provider ──
// GET /api/fighters/{id}/stats — 10 computed stats from matches + bets tables

final fighterStatsProvider = FutureProvider.family<
    Map<String, dynamic>?,
    String>((ref, fighterId) async {
  final api = ref.read(apiServiceProvider);
  return api.fetchFighterStats(fighterId);
});

// ── Fighter Match History Provider ──
// GET /api/fighters/{id}/matches?limit=10

final fighterMatchesProvider = FutureProvider.family<
    List<Map<String, dynamic>>,
    String>((ref, fighterId) async {
  final api = ref.read(apiServiceProvider);
  return api.fetchFighterMatches(fighterId, limit: 10);
});

// ── Fighter VS Provider ──
// GET /api/fighters/{id}/vs/{opponent_id} — head-to-head stats from matches table

final fighterVsProvider = FutureProvider.family<
    Map<String, dynamic>?,
    FighterVsParams>((ref, params) async {
  final api = ref.read(apiServiceProvider);
  return api.fetchFighterVs(params.fighterId, params.opponentId);
});
