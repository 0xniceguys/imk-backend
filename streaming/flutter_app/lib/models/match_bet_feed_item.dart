class MatchBetFeedItem {
  final String walletMasked;
  final String side;
  final String fighterName;
  final double amount;
  final String status;
  final DateTime? placedAt;

  const MatchBetFeedItem({
    required this.walletMasked,
    required this.side,
    required this.fighterName,
    required this.amount,
    required this.status,
    this.placedAt,
  });

  factory MatchBetFeedItem.fromJson(Map<String, dynamic> json) {
    return MatchBetFeedItem(
      walletMasked: json['wallet_masked'] as String? ?? 'Unknown',
      side: (json['side'] as String? ?? '').toUpperCase(),
      fighterName: json['fighter_name'] as String? ?? 'Fighter',
      amount: (json['amount'] as num?)?.toDouble() ?? 0.0,
      status: json['status'] as String? ?? 'active',
      placedAt: json['placed_at'] != null
          ? DateTime.tryParse(json['placed_at'] as String)
          : null,
    );
  }
}

