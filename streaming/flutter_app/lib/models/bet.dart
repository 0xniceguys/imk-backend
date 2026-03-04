enum BetStatus { active, won, lost, cancelled, claimed }

class Bet {
  final String id;
  final String matchId;
  final String fighterId;
  final String fighterName;
  final String opponentName;
  final double amount;
  final String currency;
  final double oddsAtPlacement;
  final BetStatus status;
  final DateTime placedAt;
  final String? txSignature;      // place_bet tx signature
  final String? claimTxSignature; // claim tx signature
  final double? payout;
  final String? onChainSide;      // "A" or "B"

  const Bet({
    required this.id,
    required this.matchId,
    required this.fighterId,
    required this.fighterName,
    required this.opponentName,
    required this.amount,
    this.currency = 'SKR',
    required this.oddsAtPlacement,
    required this.status,
    required this.placedAt,
    this.txSignature,
    this.claimTxSignature,
    this.payout,
    this.onChainSide,
  });

  factory Bet.fromJson(Map<String, dynamic> json) {
    return Bet(
      id: json['id'] as String,
      matchId: json['match_id'] as String,
      fighterId: json['fighter_id'] as String,
      fighterName: json['fighter_name'] as String? ?? '',
      opponentName: json['opponent_name'] as String? ?? '',
      amount: (json['amount'] as num).toDouble(),
      currency: json['currency'] as String? ?? 'SKR',
      oddsAtPlacement: (json['odds_at_placement'] as num?)?.toDouble() ?? 0.0,
      status: _parseStatus(json['status'] as String),
      placedAt: DateTime.parse(json['placed_at'] as String),
      txSignature: json['tx_signature'] as String?,
      claimTxSignature: json['claim_tx_signature'] as String?,
      payout: (json['payout'] as num?)?.toDouble(),
      onChainSide: json['on_chain_side'] as String?,
    );
  }

  static BetStatus _parseStatus(String s) {
    switch (s) {
      case 'won':
        return BetStatus.won;
      case 'lost':
        return BetStatus.lost;
      case 'cancelled':
        return BetStatus.cancelled;
      case 'claimed':
        return BetStatus.claimed;
      default:
        return BetStatus.active;
    }
  }

  bool get isClaimable => status == BetStatus.won && onChainSide != null;
  bool get isClaimed   => status == BetStatus.claimed;

  /// Solana Explorer link for the place_bet transaction
  String? get explorerUrl => txSignature != null
      ? 'https://explorer.solana.com/tx/$txSignature?cluster=devnet'
      : null;
}
