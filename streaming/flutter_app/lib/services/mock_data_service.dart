import '../core/constants.dart';
import '../models/fighter.dart';
import '../models/match.dart';
import '../models/bet.dart';
import '../models/odds.dart';

class MockDataService {
  static const fighters = [
    Fighter(
      id: 'sub-zero',
      name: 'SUB-ZERO',
      character: 'Sub-Zero',
      llmModel: 'Claude Opus 4.6',
      imageAsset: Assets.fighterLeft,
      winRate: 0.62,
      matchesPlayed: 4151,
      matchesWon: 2574,
    ),
    Fighter(
      id: 'sonya',
      name: 'SONIYA',
      character: 'Sonya Blade',
      llmModel: 'ChatGPT 5.1 Codex',
      imageAsset: Assets.fighterCenter,
      winRate: 0.10,
      matchesPlayed: 4151,
      matchesWon: 415,
    ),
    Fighter(
      id: 'scorpion',
      name: 'SCORPION',
      character: 'Scorpion',
      llmModel: 'Gemini Ultra 2',
      imageAsset: Assets.fighterRight,
      winRate: 0.45,
      matchesPlayed: 3200,
      matchesWon: 1440,
    ),
    Fighter(
      id: 'johnny',
      name: 'JOHNNY CAGE',
      character: 'Johnny Cage',
      llmModel: 'ChatGPT 5.1 Codex',
      imageAsset: Assets.battleLeft,
      winRate: 0.55,
      matchesPlayed: 2800,
      matchesWon: 1540,
    ),
    Fighter(
      id: 'raiden',
      name: 'RAIDEN',
      character: 'Raiden',
      llmModel: 'Opus 4.6',
      imageAsset: Assets.battleRight,
      winRate: 0.71,
      matchesPlayed: 3500,
      matchesWon: 2485,
    ),
  ];

  static final matches = [
    Match(
      id: 'match-1',
      fighter1: fighters[0],
      fighter2: fighters[2],
      status: MatchStatus.live,
      totalPool: 13515,
      activeBets: 34,
      scheduledAt: DateTime.now().subtract(const Duration(minutes: 15)),
      odds: const Odds(
        fighter1Odds: 1.8,
        fighter2Odds: 2.2,
        fighter1PoolPct: 0.55,
        fighter2PoolPct: 0.45,
      ),
      label: 'MK4-Classic',
    ),
    Match(
      id: 'match-2',
      fighter1: fighters[3],
      fighter2: fighters[4],
      status: MatchStatus.upcoming,
      totalPool: 8200,
      activeBets: 22,
      scheduledAt: DateTime.now().add(const Duration(hours: 2)),
      odds: const Odds(
        fighter1Odds: 2.1,
        fighter2Odds: 1.7,
        fighter1PoolPct: 0.42,
        fighter2PoolPct: 0.58,
      ),
      label: 'MK4-Classic',
    ),
    Match(
      id: 'match-3',
      fighter1: fighters[1],
      fighter2: fighters[0],
      status: MatchStatus.upcoming,
      totalPool: 5100,
      activeBets: 18,
      scheduledAt: DateTime.now().add(const Duration(hours: 5)),
      odds: const Odds(
        fighter1Odds: 3.2,
        fighter2Odds: 1.3,
        fighter1PoolPct: 0.25,
        fighter2PoolPct: 0.75,
      ),
      label: 'MK4-Classic',
    ),
  ];

  static final userBets = [
    Bet(
      id: 'bet-1',
      matchId: 'match-1',
      fighterId: 'johnny',
      fighterName: 'Cage',
      opponentName: 'Sub-Zero',
      amount: 1515,
      oddsAtPlacement: 1.8,
      status: BetStatus.active,
      placedAt: DateTime.now().subtract(const Duration(hours: 1)),
    ),
    Bet(
      id: 'bet-2',
      matchId: 'match-old-1',
      fighterId: 'raiden',
      fighterName: 'Cage',
      opponentName: 'Sub-Zero',
      amount: 1515,
      oddsAtPlacement: 2.1,
      status: BetStatus.won,
      placedAt: DateTime.now().subtract(const Duration(days: 2)),
      payout: 3181.5,
    ),
    Bet(
      id: 'bet-3',
      matchId: 'match-old-2',
      fighterId: 'sub-zero',
      fighterName: 'Cage',
      opponentName: 'Sub-Zero',
      amount: 1515,
      oddsAtPlacement: 1.5,
      status: BetStatus.lost,
      placedAt: DateTime.now().subtract(const Duration(days: 5)),
    ),
    Bet(
      id: 'bet-4',
      matchId: 'match-old-3',
      fighterId: 'sonya',
      fighterName: 'Cage',
      opponentName: 'Sub-Zero',
      amount: 1515,
      oddsAtPlacement: 3.2,
      status: BetStatus.lost,
      placedAt: DateTime.now().subtract(const Duration(days: 7)),
    ),
  ];
}
