import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../services/api_service.dart';
import 'match_provider.dart';

// ── Fighter Stats Provider ──
// Fetches computed stats for a single fighter via GET /api/fighters/{id}/stats

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
