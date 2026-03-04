/// Live game state received via WebSocket from the backend.
class GameState {
  final int frameId;
  final int p1Health;
  final int p2Health;
  final double p1HealthPct;
  final double p2HealthPct;
  final int timer;
  final double p1X;
  final double p2X;
  final int currentRound;
  final int roundsWonP1;
  final int roundsWonP2;
  final int bestOf;
  // Round lifecycle (from backend broadcast)
  final bool roundOver;
  final bool p1Won;
  // Combat signals (from training update 2026-03-04)
  final double p1Action;
  final double p2Action;
  final double p1YVel;
  final double p1Hitstun;
  final double p2Hitstun;
  final double p1Airborne;
  final double p2Airborne;

  const GameState({
    this.frameId = 0,
    this.p1Health = 160,
    this.p2Health = 160,
    this.p1HealthPct = 1.0,
    this.p2HealthPct = 1.0,
    this.timer = 99,
    this.p1X = 0,
    this.p2X = 0,
    this.currentRound = 1,
    this.roundsWonP1 = 0,
    this.roundsWonP2 = 0,
    this.bestOf = 3,
    this.roundOver = false,
    this.p1Won = false,
    this.p1Action = 0.0,
    this.p2Action = 0.0,
    this.p1YVel = 0.0,
    this.p1Hitstun = 0.0,
    this.p2Hitstun = 0.0,
    this.p1Airborne = 0.0,
    this.p2Airborne = 0.0,
  });

  factory GameState.fromJson(Map<String, dynamic> json) {
    return GameState(
      frameId: json['frame_id'] as int? ?? 0,
      p1Health: json['p1_health'] as int? ?? 160,
      p2Health: json['p2_health'] as int? ?? 160,
      p1HealthPct: (json['p1_health_pct'] as num?)?.toDouble() ?? 1.0,
      p2HealthPct: (json['p2_health_pct'] as num?)?.toDouble() ?? 1.0,
      timer: json['timer'] as int? ?? 99,
      p1X: (json['p1_x'] as num?)?.toDouble() ?? 0.0,
      p2X: (json['p2_x'] as num?)?.toDouble() ?? 0.0,
      currentRound: json['current_round'] as int? ?? 1,
      roundsWonP1: json['rounds_won_p1'] as int? ?? 0,
      roundsWonP2: json['rounds_won_p2'] as int? ?? 0,
      bestOf: json['best_of'] as int? ?? 3,
      roundOver: json['round_over'] as bool? ?? false,
      p1Won: json['p1_won'] as bool? ?? false,
      p1Action: (json['p1_action'] as num?)?.toDouble() ?? 0.0,
      p2Action: (json['p2_action'] as num?)?.toDouble() ?? 0.0,
      p1YVel: (json['p1_y_vel'] as num?)?.toDouble() ?? 0.0,
      p1Hitstun: (json['p1_hitstun'] as num?)?.toDouble() ?? 0.0,
      p2Hitstun: (json['p2_hitstun'] as num?)?.toDouble() ?? 0.0,
      p1Airborne: (json['p1_airborne'] as num?)?.toDouble() ?? 0.0,
      p2Airborne: (json['p2_airborne'] as num?)?.toDouble() ?? 0.0,
    );
  }
}
