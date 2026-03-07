class Odds {
  final double fighter1Odds;
  final double fighter2Odds;
  final double fighter1PoolPct;
  final double fighter2PoolPct;
  final double fighter1Pool;
  final double fighter2Pool;
  final double totalPool;
  final int activeBets;

  const Odds({
    required this.fighter1Odds,
    required this.fighter2Odds,
    required this.fighter1PoolPct,
    required this.fighter2PoolPct,
    required this.fighter1Pool,
    required this.fighter2Pool,
    required this.totalPool,
    required this.activeBets,
  });

  factory Odds.fromJson(Map<String, dynamic> json) {
    return Odds(
      fighter1Odds: (json['fighter1_odds'] as num?)?.toDouble() ?? 2.0,
      fighter2Odds: (json['fighter2_odds'] as num?)?.toDouble() ?? 2.0,
      fighter1PoolPct: (json['fighter1_pool_pct'] as num?)?.toDouble() ?? 0.5,
      fighter2PoolPct: (json['fighter2_pool_pct'] as num?)?.toDouble() ?? 0.5,
      totalPool: (json['total_pool'] as num?)?.toDouble() ?? 0.0,
      fighter1Pool:
          (json['fighter1_pool'] as num?)?.toDouble() ??
          (((json['total_pool'] as num?)?.toDouble() ?? 0.0) *
              ((json['fighter1_pool_pct'] as num?)?.toDouble() ?? 0.5)),
      fighter2Pool:
          (json['fighter2_pool'] as num?)?.toDouble() ??
          (((json['total_pool'] as num?)?.toDouble() ?? 0.0) *
              ((json['fighter2_pool_pct'] as num?)?.toDouble() ?? 0.5)),
      activeBets: (json['active_bets'] as int?) ?? 0,
    );
  }

  static const zero = Odds(
    fighter1Odds: 2.0,
    fighter2Odds: 2.0,
    fighter1PoolPct: 0.5,
    fighter2PoolPct: 0.5,
    fighter1Pool: 0.0,
    fighter2Pool: 0.0,
    totalPool: 0.0,
    activeBets: 0,
  );
}
