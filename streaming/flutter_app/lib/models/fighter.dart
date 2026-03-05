class Fighter {
  final String id;
  final String name;
  final String character;
  final int characterId;
  final String llmModel;
  final String imageAsset;
  final String? imageUrl;
  final String? agentArchitecture;
  final double winRate;
  final int matchesPlayed;
  final int matchesWon;
  // Rich display data
  final String? description;
  final String? origin;
  final String? specialMove;
  final String? fightStyle;
  final int? rank;

  const Fighter({
    required this.id,
    required this.name,
    required this.character,
    this.characterId = 0,
    required this.llmModel,
    this.imageAsset = '',
    this.imageUrl,
    this.agentArchitecture,
    this.winRate = 0,
    this.matchesPlayed = 0,
    this.matchesWon = 0,
    this.description,
    this.origin,
    this.specialMove,
    this.fightStyle,
    this.rank,
  });

  int get losses => matchesPlayed - matchesWon;

  /// Resolved image URL: prepend the base URL for relative backend paths.
  String? resolvedImageUrl(String baseUrl) {
    if (imageUrl == null || imageUrl!.isEmpty) return null;
    if (imageUrl!.startsWith('http')) return imageUrl;
    return '$baseUrl$imageUrl';
  }

  factory Fighter.fromJson(Map<String, dynamic> json) {
    final name = json['name'] as String? ?? 'UNKNOWN';
    return Fighter(
      id: json['id'] as String? ?? '',
      name: name.toUpperCase(),
      character: json['character'] as String? ?? name,
      characterId: json['character_id'] as int? ?? 0,
      llmModel: json['llm_model'] as String? ?? '',
      imageUrl: json['image_url'] as String?,
      agentArchitecture: json['agent_architecture'] as String?,
      winRate: (json['win_rate'] as num?)?.toDouble() ?? 0.0,
      matchesPlayed: json['matches_played'] as int? ?? 0,
      matchesWon: json['matches_won'] as int? ?? 0,
      description: json['description'] as String?,
      origin: json['origin'] as String?,
      specialMove: json['special_move'] as String?,
      fightStyle: json['fight_style'] as String?,
      rank: json['rank'] as int?,
    );
  }
}
