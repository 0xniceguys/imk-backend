class Fighter {
  final String id;
  final String name;
  final String character;
  final String llmModel;
  final String imageAsset;
  final String? imageUrl;
  final String? agentArchitecture;
  final double winRate;
  final int matchesPlayed;
  final int matchesWon;

  const Fighter({
    required this.id,
    required this.name,
    required this.character,
    required this.llmModel,
    this.imageAsset = '',
    this.imageUrl,
    this.agentArchitecture,
    this.winRate = 0,
    this.matchesPlayed = 0,
    this.matchesWon = 0,
  });

  factory Fighter.fromJson(Map<String, dynamic> json) {
    return Fighter(
      id: json['id'] as String,
      name: (json['name'] as String).toUpperCase(),
      character: json['character'] as String? ?? json['name'] as String,
      llmModel: json['llm_model'] as String? ?? '',
      imageUrl: json['image_url'] as String?,
      agentArchitecture: json['agent_architecture'] as String?,
      winRate: (json['win_rate'] as num?)?.toDouble() ?? 0.0,
      matchesPlayed: json['matches_played'] as int? ?? 0,
      matchesWon: json['matches_won'] as int? ?? 0,
    );
  }
}
