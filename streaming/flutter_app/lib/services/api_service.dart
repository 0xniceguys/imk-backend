import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'package:flutter/foundation.dart';
import 'package:http/http.dart' as http;
import '../core/api_exception.dart';
import '../core/constants.dart';
import '../models/match.dart';
import '../models/fighter.dart';
import '../models/bet.dart';
import '../models/match_bet_feed_item.dart';

void _log(String msg) {
  // ignore: avoid_print
  if (kDebugMode) print('[API] $msg');
}

/// Handle HTTP errors and throw ApiException
Never _handleError(http.Response resp, String endpoint) {
  _log('$endpoint failed: ${resp.statusCode} ${resp.body}');
  try {
    final json = jsonDecode(resp.body) as Map<String, dynamic>;
    throw ApiException.fromJson(json, resp.statusCode);
  } catch (e) {
    if (e is ApiException) rethrow;
    // If body is not valid JSON, create generic error
    throw ApiException(
      code: 'HttpError',
      message: 'Request failed with status ${resp.statusCode}',
      statusCode: resp.statusCode,
    );
  }
}

class ApiService {
  final http.Client _client;
  String? _authToken;

  ApiService({http.Client? client}) : _client = client ?? http.Client();

  void setAuthToken(String? token) {
    _authToken = token;
  }

  Map<String, String> get _headers => {
    'Content-Type': 'application/json',
    if (_authToken != null) 'Authorization': 'Bearer $_authToken',
  };

  // ── Matches ──

  Future<List<Match>> fetchMatches({String? status}) async {
    final uri = Uri.parse(
      '$kApiBaseUrl/matches/',
    ).replace(queryParameters: status != null ? {'status': status} : null);
    _log('GET $uri');
    try {
      final resp = await _client.get(uri, headers: _headers);
      if (resp.statusCode != 200) {
        _log('fetchMatches failed: ${resp.statusCode} ${resp.body}');
        return [];
      }
      final list = jsonDecode(resp.body) as List;
      return list
          .whereType<Map<String, dynamic>>()
          .map((j) => Match.fromJson(j))
          .toList();
    } catch (e) {
      _log('fetchMatches error: $e');
      return [];
    }
  }

  Future<Match?> fetchMatch(String matchId) async {
    final uri = Uri.parse('$kApiBaseUrl/matches/$matchId');
    _log('GET $uri');
    try {
      final resp = await _client.get(uri, headers: _headers);
      if (resp.statusCode != 200) return null;
      return Match.fromJson(jsonDecode(resp.body) as Map<String, dynamic>);
    } catch (e) {
      _log('fetchMatch error: $e');
      return null;
    }
  }

  Future<List<MatchBetFeedItem>> fetchMatchBetFeed(
    String matchId, {
    int limit = 20,
  }) async {
    final uri = Uri.parse(
      '$kApiBaseUrl/matches/$matchId/bet-feed',
    ).replace(queryParameters: {'limit': '$limit'});
    _log('GET $uri');
    try {
      final resp = await _client.get(uri, headers: _headers);
      if (resp.statusCode != 200) return [];
      final list = jsonDecode(resp.body) as List;
      return list
          .whereType<Map<String, dynamic>>()
          .map((j) => MatchBetFeedItem.fromJson(j))
          .toList();
    } catch (e) {
      _log('fetchMatchBetFeed error: $e');
      return [];
    }
  }

  // ── Fighters ──

  Future<List<Fighter>> fetchFighters() async {
    final uri = Uri.parse('$kApiBaseUrl/fighters/');
    _log('GET $uri');
    try {
      final resp = await _client.get(uri, headers: _headers);
      if (resp.statusCode != 200) return [];
      final list = jsonDecode(resp.body) as List;
      return list
          .whereType<Map<String, dynamic>>()
          .map((j) => Fighter.fromJson(j))
          .toList();
    } catch (e) {
      _log('fetchFighters error: $e');
      return [];
    }
  }

  Future<Map<String, dynamic>?> fetchFighterStats(String fighterId) async {
    final uri = Uri.parse('$kApiBaseUrl/fighters/$fighterId/stats');
    _log('GET $uri');
    try {
      final resp = await _client.get(uri, headers: _headers);
      if (resp.statusCode != 200) return null;
      return jsonDecode(resp.body) as Map<String, dynamic>;
    } catch (e) {
      _log('fetchFighterStats error: $e');
      return null;
    }
  }

  Future<List<Map<String, dynamic>>> fetchFighterMatches(
    String fighterId, {
    int limit = 10,
  }) async {
    final uri = Uri.parse(
      '$kApiBaseUrl/fighters/$fighterId/matches',
    ).replace(queryParameters: {'limit': '$limit'});
    _log('GET $uri');
    try {
      final resp = await _client.get(uri, headers: _headers);
      if (resp.statusCode != 200) return [];
      final list = jsonDecode(resp.body) as List;
      return list.whereType<Map<String, dynamic>>().toList();
    } catch (e) {
      _log('fetchFighterMatches error: $e');
      return [];
    }
  }

  Future<Map<String, dynamic>?> fetchFighterVs(
    String fighterId,
    String opponentId,
  ) async {
    final uri = Uri.parse('$kApiBaseUrl/fighters/$fighterId/vs/$opponentId');
    _log('GET $uri');
    try {
      final resp = await _client.get(uri, headers: _headers);
      if (resp.statusCode != 200) return null;
      return jsonDecode(resp.body) as Map<String, dynamic>;
    } catch (e) {
      _log('fetchFighterVs error: $e');
      return null;
    }
  }

  Future<List<Bet>> fetchMyBets() async {
    final uri = Uri.parse('$kApiBaseUrl/bets/mine');
    _log('GET $uri');
    try {
      final resp = await _client.get(uri, headers: _headers);
      if (resp.statusCode != 200) return [];
      final list = jsonDecode(resp.body) as List;
      return list
          .whereType<Map<String, dynamic>>()
          .map((j) => Bet.fromJson(j))
          .toList();
    } catch (e) {
      _log('fetchMyBets error: $e');
      return [];
    }
  }

  Future<Bet> placeBet({
    required String matchId,
    required String fighterId,
    required double amount,
    required String side, // "A" or "B"
    required String privyJwt, // legacy server-signing flow
  }) async {
    final uri = Uri.parse('$kApiBaseUrl/bets/');
    _log('POST $uri matchId=$matchId side=$side');
    try {
      final resp = await _client
          .post(
            uri,
            headers: _headers,
            body: jsonEncode({
              'match_id': matchId,
              'fighter_id': fighterId,
              'amount': amount,
              'side': side,
              'privy_jwt': privyJwt,
            }),
          )
          .timeout(const Duration(seconds: 45)); // longer for on-chain tx

      if (resp.statusCode != 200 && resp.statusCode != 201) {
        _handleError(resp, 'placeBet');
      }
      return Bet.fromJson(jsonDecode(resp.body) as Map<String, dynamic>);
    } on SocketException {
      throw ApiException.networkError();
    } on TimeoutException {
      throw ApiException.timeout();
    } on ApiException {
      rethrow;
    } catch (e) {
      _log('placeBet error: $e');
      throw ApiException.unexpected('Failed to place bet: $e');
    }
  }

  /// Step 1: Prepare an unsigned bet transaction for client-side signing.
  Future<String> prepareBet({
    required String matchId,
    required String fighterId,
    required double amount,
    required String side, // "A" or "B"
  }) async {
    final uri = Uri.parse('$kApiBaseUrl/bets/prepare');
    _log('POST $uri matchId=$matchId side=$side');
    try {
      final resp = await _client
          .post(
            uri,
            headers: _headers,
            body: jsonEncode({
              'match_id': matchId,
              'fighter_id': fighterId,
              'amount': amount,
              'side': side,
            }),
          )
          .timeout(const Duration(seconds: 30));

      if (resp.statusCode != 200 && resp.statusCode != 201) {
        _handleError(resp, 'prepareBet');
      }

      final data = jsonDecode(resp.body) as Map<String, dynamic>;
      final tx = data['transaction_base64'] as String?;
      if (tx == null || tx.isEmpty) {
        throw ApiException(
          code: 'MissingField',
          message: 'Server response missing transaction_base64',
          statusCode: resp.statusCode,
        );
      }
      return tx;
    } on SocketException {
      throw ApiException.networkError();
    } on TimeoutException {
      throw ApiException.timeout();
    } on ApiException {
      rethrow;
    } catch (e) {
      _log('prepareBet error: $e');
      throw ApiException.unexpected('Failed to prepare bet transaction: $e');
    }
  }

  /// Step 3: Broadcast a signed bet transaction and persist DB state.
  Future<Bet> broadcastBet({
    required String matchId,
    required String signedTransactionBase64,
  }) async {
    final uri = Uri.parse('$kApiBaseUrl/bets/broadcast');
    _log('POST $uri matchId=$matchId');
    try {
      final resp = await _client
          .post(
            uri,
            headers: _headers,
            body: jsonEncode({
              'match_id': matchId,
              'signed_transaction_base64': signedTransactionBase64,
            }),
          )
          .timeout(const Duration(seconds: 45));

      if (resp.statusCode != 200 && resp.statusCode != 201) {
        _handleError(resp, 'broadcastBet');
      }
      return Bet.fromJson(jsonDecode(resp.body) as Map<String, dynamic>);
    } on SocketException {
      throw ApiException.networkError();
    } on TimeoutException {
      throw ApiException.timeout();
    } on ApiException {
      rethrow;
    } catch (e) {
      _log('broadcastBet error: $e');
      throw ApiException.unexpected('Failed to broadcast bet transaction: $e');
    }
  }

  /// Claim a won bet's SKR payout on-chain via Privy.
  /// Returns the Solana transaction signature.
  Future<String> claimBet({
    required String betId,
    required String privyJwt,
  }) async {
    final uri = Uri.parse('$kApiBaseUrl/bets/$betId/claim');
    _log('POST $uri betId=$betId');
    try {
      final resp = await _client
          .post(
            uri,
            headers: _headers,
            body: jsonEncode({'privy_jwt': privyJwt}),
          )
          .timeout(const Duration(seconds: 45));

      if (resp.statusCode != 200 && resp.statusCode != 201) {
        _handleError(resp, 'claimBet');
      }
      final data = jsonDecode(resp.body) as Map<String, dynamic>;
      final sig = data['tx_signature'] as String?;
      if (sig == null) {
        throw ApiException(
          code: 'MissingField',
          message: 'Server response missing tx_signature',
          statusCode: resp.statusCode,
        );
      }
      return sig;
    } on SocketException {
      throw ApiException.networkError();
    } on TimeoutException {
      throw ApiException.timeout();
    } on ApiException {
      rethrow;
    } catch (e) {
      _log('claimBet error: $e');
      throw ApiException.unexpected('Failed to claim bet: $e');
    }
  }

  /// Step 1: Prepare an unsigned claim transaction for client-side signing.
  Future<String> prepareClaim({required String betId}) async {
    final uri = Uri.parse('$kApiBaseUrl/bets/$betId/claim/prepare');
    _log('POST $uri');
    try {
      final resp = await _client
          .post(uri, headers: _headers)
          .timeout(const Duration(seconds: 30));

      if (resp.statusCode != 200 && resp.statusCode != 201) {
        _handleError(resp, 'prepareClaim');
      }

      final data = jsonDecode(resp.body) as Map<String, dynamic>;
      final tx = data['transaction_base64'] as String?;
      if (tx == null || tx.isEmpty) {
        throw ApiException(
          code: 'MissingField',
          message: 'Server response missing transaction_base64',
          statusCode: resp.statusCode,
        );
      }
      return tx;
    } on SocketException {
      throw ApiException.networkError();
    } on TimeoutException {
      throw ApiException.timeout();
    } on ApiException {
      rethrow;
    } catch (e) {
      _log('prepareClaim error: $e');
      throw ApiException.unexpected('Failed to prepare claim transaction: $e');
    }
  }

  /// Step 3: Broadcast a signed claim transaction.
  Future<String> broadcastClaim({
    required String betId,
    required String signedTransactionBase64,
  }) async {
    final uri = Uri.parse('$kApiBaseUrl/bets/$betId/claim/broadcast');
    _log('POST $uri');
    try {
      final resp = await _client
          .post(
            uri,
            headers: _headers,
            body: jsonEncode({
              'signed_transaction_base64': signedTransactionBase64,
            }),
          )
          .timeout(const Duration(seconds: 45));

      if (resp.statusCode != 200 && resp.statusCode != 201) {
        _handleError(resp, 'broadcastClaim');
      }
      final data = jsonDecode(resp.body) as Map<String, dynamic>;
      final sig = data['tx_signature'] as String?;
      if (sig == null || sig.isEmpty) {
        throw ApiException(
          code: 'MissingField',
          message: 'Server response missing tx_signature',
          statusCode: resp.statusCode,
        );
      }
      return sig;
    } on SocketException {
      throw ApiException.networkError();
    } on TimeoutException {
      throw ApiException.timeout();
    } on ApiException {
      rethrow;
    } catch (e) {
      _log('broadcastClaim error: $e');
      throw ApiException.unexpected(
        'Failed to broadcast claim transaction: $e',
      );
    }
  }

  // ── Auth ──

  Future<Map<String, dynamic>?> login(
    String privyToken, {
    String? walletAddress,
    String? email,
  }) async {
    final uri = Uri.parse('$kApiBaseUrl/auth/login');
    _log('POST $uri walletAddress=$walletAddress email=$email');
    try {
      final resp = await _client.post(
        uri,
        headers: {'Content-Type': 'application/json'},
        body: jsonEncode({
          'token': privyToken,
          // ignore: use_null_aware_elements
          if (walletAddress != null) 'walletAddress': walletAddress,
          // ignore: use_null_aware_elements
          if (email != null) 'email': email,
        }),
      );
      if (resp.statusCode != 200) {
        _log('login failed: ${resp.statusCode} ${resp.body}');
        return null;
      }
      return jsonDecode(resp.body) as Map<String, dynamic>;
    } catch (e) {
      _log('login error: $e');
      return null;
    }
  }

  Future<Map<String, dynamic>?> updateDisplayName(String name) async {
    final uri = Uri.parse('$kApiBaseUrl/auth/me');
    try {
      final resp = await _client.patch(
        uri,
        headers: _headers,
        body: jsonEncode({'display_name': name}),
      );
      if (resp.statusCode != 200) {
        _log('updateDisplayName failed: ${resp.statusCode}');
        return null;
      }
      return jsonDecode(resp.body) as Map<String, dynamic>;
    } catch (e) {
      _log('updateDisplayName error: $e');
      return null;
    }
  }

  Future<Map<String, dynamic>?> fetchBetsSummary() async {
    final uri = Uri.parse('$kApiBaseUrl/bets/summary');
    try {
      final resp = await _client.get(uri, headers: _headers);
      if (resp.statusCode != 200) return null;
      return jsonDecode(resp.body) as Map<String, dynamic>;
    } catch (e) {
      _log('fetchBetsSummary error: $e');
      return null;
    }
  }

  Future<Map<String, dynamic>?> fetchClientConfig() async {
    final uri = Uri.parse('$kApiBaseUrl/client-config');
    _log('GET $uri');
    try {
      final resp = await _client.get(uri, headers: _headers);
      if (resp.statusCode != 200) return null;
      final decoded = jsonDecode(resp.body);
      if (decoded is Map<String, dynamic>) return decoded;
      return null;
    } catch (e) {
      _log('fetchClientConfig error: $e');
      return null;
    }
  }

  // ── Stream (HTTP polling fallback) ──

  Future<List<Map<String, dynamic>>> fetchLiveStreams() async {
    final uri = Uri.parse('$kApiBaseUrl/stream/live');
    try {
      final resp = await _client.get(uri, headers: _headers);
      if (resp.statusCode != 200) return [];
      final list = jsonDecode(resp.body) as List;
      return list.whereType<Map<String, dynamic>>().toList();
    } catch (e) {
      _log('fetchLiveStreams error: $e');
      return [];
    }
  }

  // ── Wallet ──

  /// Step 1: Get unsigned transaction from backend
  Future<String> prepareWithdraw({
    required String token, // "sol" or "seeker"
    required String toAddress,
    required double amount,
  }) async {
    final uri = Uri.parse('$kApiBaseUrl/wallet/withdraw/prepare');
    _log('POST $uri token=$token amount=$amount');
    try {
      final resp = await _client
          .post(
            uri,
            headers: _headers,
            body: jsonEncode({
              'token': token,
              'to_address': toAddress,
              'amount': amount,
            }),
          )
          .timeout(const Duration(seconds: 30));

      _log('POST $uri → ${resp.statusCode}');

      if (resp.statusCode == 200) {
        final data = jsonDecode(resp.body) as Map<String, dynamic>;
        final tx = data['transaction_base64'] as String?;
        if (tx == null) {
          throw ApiException(
            code: 'MissingField',
            message: 'Server response missing transaction_base64',
            statusCode: resp.statusCode,
          );
        }
        return tx;
      }

      _handleError(resp, 'prepareWithdraw');
    } on SocketException {
      throw ApiException.networkError();
    } on TimeoutException {
      throw ApiException.timeout();
    } on ApiException {
      rethrow;
    } catch (e) {
      _log('prepareWithdraw error: $e');
      throw ApiException.unexpected('Failed to prepare transaction: $e');
    }
  }

  /// Step 3: Broadcast signed transaction to Solana
  Future<String> broadcastWithdraw({
    required String signedTransactionBase64,
  }) async {
    final uri = Uri.parse('$kApiBaseUrl/wallet/withdraw/broadcast');
    _log('POST $uri (broadcasting signed tx)');
    try {
      final resp = await _client
          .post(
            uri,
            headers: _headers,
            body: jsonEncode({
              'signed_transaction_base64': signedTransactionBase64,
            }),
          )
          .timeout(const Duration(seconds: 30));

      _log('POST $uri → ${resp.statusCode}');

      if (resp.statusCode == 200) {
        final data = jsonDecode(resp.body) as Map<String, dynamic>;
        final sig = data['tx_signature'] as String?;
        if (sig == null) {
          throw ApiException(
            code: 'MissingField',
            message: 'Server response missing tx_signature',
            statusCode: resp.statusCode,
          );
        }
        return sig;
      }

      _handleError(resp, 'broadcastWithdraw');
    } on SocketException {
      throw ApiException.networkError();
    } on TimeoutException {
      throw ApiException.timeout();
    } on ApiException {
      rethrow;
    } catch (e) {
      _log('broadcastWithdraw error: $e');
      throw ApiException.unexpected('Failed to broadcast transaction: $e');
    }
  }

  /// Legacy withdraw endpoint (kept for backward compatibility)
  /// DEPRECATED: Use prepareWithdraw + sign + broadcastWithdraw instead
  @Deprecated('Use prepareWithdraw, sign with Privy, then broadcastWithdraw')
  Future<String> withdrawFunds({
    required String token, // "sol" or "seeker"
    required String toAddress,
    required double amount,
  }) async {
    final uri = Uri.parse('$kApiBaseUrl/wallet/withdraw');
    _log('POST $uri token=$token amount=$amount');
    try {
      final resp = await _client
          .post(
            uri,
            headers: _headers,
            body: jsonEncode({
              'token': token,
              'to_address': toAddress,
              'amount': amount,
            }),
          )
          .timeout(const Duration(seconds: 30));

      _log('POST $uri → ${resp.statusCode} ${resp.body}');

      if (resp.statusCode == 200) {
        final data = jsonDecode(resp.body) as Map<String, dynamic>;
        final sig = data['tx_signature'] as String?;
        if (sig == null) {
          throw ApiException(
            code: 'MissingField',
            message: 'Server response missing tx_signature',
            statusCode: resp.statusCode,
          );
        }
        return sig;
      }

      _handleError(resp, 'withdrawFunds');
    } on SocketException {
      throw ApiException.networkError();
    } on TimeoutException {
      throw ApiException.timeout();
    } on ApiException {
      rethrow;
    } catch (e) {
      _log('withdrawFunds error: $e');
      throw ApiException.unexpected('Failed to withdraw: $e');
    }
  }

  /// Get the URL for polling a match frame as PNG.
  String frameUrl(String matchId) => '$kApiBaseUrl/stream/$matchId/frame';

  void dispose() => _client.close();
}
