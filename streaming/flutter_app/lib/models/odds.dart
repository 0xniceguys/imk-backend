class Odds {
  final double fighter1Odds;
  final double fighter2Odds;
  final double fighter1PoolPct;
  final double fighter2PoolPct;

  const Odds({
    required this.fighter1Odds,
    required this.fighter2Odds,
    required this.fighter1PoolPct,
    required this.fighter2PoolPct,
  });

  factory Odds.fromJson(Map<String, dynamic> json) {
    return Odds(
      fighter1Odds: (json['fighter1_odds'] as num?)?.toDouble() ?? 2.0,
      fighter2Odds: (json['fighter2_odds'] as num?)?.toDouble() ?? 2.0,
      fighter1PoolPct: (json['fighter1_pool_pct'] as num?)?.toDouble() ?? 0.5,
      fighter2PoolPct: (json['fighter2_pool_pct'] as num?)?.toDouble() ?? 0.5,
    );
  }

  static const zero = Odds(
    fighter1Odds: 2.0,
    fighter2Odds: 2.0,
    fighter1PoolPct: 0.5,
    fighter2PoolPct: 0.5,
  );
}
