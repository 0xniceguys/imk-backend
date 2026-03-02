import 'fighter.dart';
import 'odds.dart';

enum MatchStatus { upcoming, live, completed, cancelled }

class Match {
  final String id;
  final Fighter fighter1;
  final Fighter fighter2;
  final MatchStatus status;
  final String? streamUrl;
  final DateTime scheduledAt;
  final DateTime? completedAt;
  final String? winnerId;
  final double totalPool;
  final int activeBets;
  final Odds odds;
  final String label;
  final int bestOf;
  final int currentRound;
  final int roundsWonP1;
  final int roundsWonP2;
  final bool bettingOpen;

  const Match({
    required this.id,
    required this.fighter1,
    required this.fighter2,
    required this.status,
    this.streamUrl,
    required this.scheduledAt,
    this.completedAt,
    this.winnerId,
    this.totalPool = 0,
    this.activeBets = 0,
    required this.odds,
    this.label = 'MK4-Classic',
    this.bestOf = 3,
    this.currentRound = 1,
    this.roundsWonP1 = 0,
    this.roundsWonP2 = 0,
    this.bettingOpen = false,
  });

  factory Match.fromJson(Map<String, dynamic> json) {
    final oddsJson = json['odds'] as Map<String, dynamic>?;
    final odds = oddsJson != null ? Odds.fromJson(oddsJson) : Odds.zero;

    return Match(
      id: json['id'] as String,
      fighter1: Fighter.fromJson(json['fighter1'] as Map<String, dynamic>),
      fighter2: Fighter.fromJson(json['fighter2'] as Map<String, dynamic>),
      status: _parseStatus(json['status'] as String),
      streamUrl: json['stream_url'] as String?,
      scheduledAt: DateTime.parse(json['scheduled_at'] as String),
      completedAt: json['completed_at'] != null
          ? DateTime.parse(json['completed_at'] as String)
          : null,
      winnerId: json['winner_id'] as String?,
      totalPool: (odds.fighter1PoolPct + odds.fighter2PoolPct) > 0
          ? (oddsJson?['total_pool'] as num?)?.toDouble() ?? 0.0
          : 0.0,
      activeBets: (oddsJson?['active_bets'] as int?) ?? 0,
      odds: odds,
      label: json['label'] as String? ?? 'MK4-Classic',
      bestOf: json['best_of'] as int? ?? 3,
      currentRound: json['current_round'] as int? ?? 1,
      roundsWonP1: json['rounds_won_p1'] as int? ?? 0,
      roundsWonP2: json['rounds_won_p2'] as int? ?? 0,
      bettingOpen: json['betting_open'] as bool? ?? false,
    );
  }

  static MatchStatus _parseStatus(String s) {
    switch (s) {
      case 'live':
        return MatchStatus.live;
      case 'completed':
        return MatchStatus.completed;
      case 'cancelled':
        return MatchStatus.cancelled;
      default:
        return MatchStatus.upcoming;
    }
  }
}
