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
// Fetch enough rows to cover all completed matches for the fighter so
// match-history totals stay aligned with /fighters/{id}/stats.

final fighterMatchesProvider = FutureProvider.family<
    List<Map<String, dynamic>>,
    String>((ref, fighterId) async {
  final api = ref.read(apiServiceProvider);
  Map<String, dynamic>? stats;
  try {
    stats = await ref.watch(fighterStatsProvider(fighterId).future);
  } catch (_) {
    stats = null;
  }

  final played = (stats?['matches_played'] as num?)?.toInt() ?? 100;
  var limit = played <= 0 ? 100 : played;
  if (limit < 100) limit = 100;
  if (limit > 2000) limit = 2000;

  return api.fetchFighterMatches(fighterId, limit: limit);
});

// ── Fighter VS Provider ──
// GET /api/fighters/{id}/vs/{opponent_id} — head-to-head stats from matches table

final fighterVsProvider = FutureProvider.family<
    Map<String, dynamic>?,
    FighterVsParams>((ref, params) async {
  final api = ref.read(apiServiceProvider);
  return api.fetchFighterVs(params.fighterId, params.opponentId);
});
