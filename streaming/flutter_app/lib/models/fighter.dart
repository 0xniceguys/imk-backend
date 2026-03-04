import '../core/constants.dart';

class Fighter {
  final String id;
  final String name;
  final String slug;
  final String character;
  final int characterId;
  final String llmModel;
  final String? imageUrl;
  final String? agentArchitecture;
  final String description;
  final String origin;
  final String specialMove;
  final String fightStyle;
  final int rank;
  final double winRate;
  final int matchesPlayed;
  final int matchesWon;
  final DateTime? createdAt;

  const Fighter({
    required this.id,
    required this.name,
    required this.slug,
    required this.character,
    this.characterId = 0,
    required this.llmModel,
    this.imageUrl,
    this.agentArchitecture,
    this.description = '',
    this.origin = '',
    this.specialMove = '',
    this.fightStyle = '',
    this.rank = 0,
    this.winRate = 0,
    this.matchesPlayed = 0,
    this.matchesWon = 0,
    this.createdAt,
  });

  String? get resolvedImageUrl {
    final raw = imageUrl?.trim();
    if (raw == null || raw.isEmpty) return null;
    if (raw.startsWith('http://') || raw.startsWith('https://')) return raw;
    return '$kApiOrigin$raw';
  }

  int get matchesLost => (matchesPlayed - matchesWon).clamp(0, matchesPlayed);

  factory Fighter.fromJson(Map<String, dynamic> json) {
    return Fighter(
      id: json['id'] as String? ?? '',
      name: (json['name'] as String? ?? '').toUpperCase(),
      slug: json['slug'] as String? ?? '',
      character: json['character'] as String? ?? json['name'] as String? ?? '',
      characterId: json['character_id'] as int? ?? 0,
      llmModel: json['llm_model'] as String? ?? '',
      imageUrl: json['image_url'] as String?,
      agentArchitecture: json['agent_architecture'] as String?,
      description: json['description'] as String? ?? '',
      origin: json['origin'] as String? ?? '',
      specialMove: json['special_move'] as String? ?? '',
      fightStyle: json['fight_style'] as String? ?? '',
      rank: json['rank'] as int? ?? 0,
      winRate: (json['win_rate'] as num?)?.toDouble() ?? 0.0,
      matchesPlayed: json['matches_played'] as int? ?? 0,
      matchesWon: json['matches_won'] as int? ?? 0,
      createdAt: json['created_at'] != null
          ? DateTime.tryParse(json['created_at'] as String)
          : null,
    );
  }
}
