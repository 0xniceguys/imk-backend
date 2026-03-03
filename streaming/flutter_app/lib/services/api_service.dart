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
    final uri = Uri.parse('$kApiBaseUrl/matches/').replace(
        queryParameters: status != null ? {'status': status} : null);
    _log('GET $uri');
    try {
      final resp = await _client.get(uri, headers: _headers);
      if (resp.statusCode != 200) {
        _log('fetchMatches failed: ${resp.statusCode} ${resp.body}');
        return [];
      }
      final list = jsonDecode(resp.body) as List;
      return list
          .map((j) => Match.fromJson(j as Map<String, dynamic>))
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

  // ── Fighters ──

  Future<List<Fighter>> fetchFighters() async {
    final uri = Uri.parse('$kApiBaseUrl/fighters/');
    _log('GET $uri');
    try {
      final resp = await _client.get(uri, headers: _headers);
      if (resp.statusCode != 200) return [];
      final list = jsonDecode(resp.body) as List;
      return list
          .map((j) => Fighter.fromJson(j as Map<String, dynamic>))
          .toList();
    } catch (e) {
      _log('fetchFighters error: $e');
      return [];
    }
  }

  // ── Bets ──

  Future<List<Bet>> fetchMyBets() async {
    final uri = Uri.parse('$kApiBaseUrl/bets/mine');
    _log('GET $uri');
    try {
      final resp = await _client.get(uri, headers: _headers);
      if (resp.statusCode != 200) return [];
      final list = jsonDecode(resp.body) as List;
      return list
          .map((j) => Bet.fromJson(j as Map<String, dynamic>))
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
  }) async {
    final uri = Uri.parse('$kApiBaseUrl/bets/');
    _log('POST $uri');
    try {
      final resp = await _client.post(
        uri,
        headers: _headers,
        body: jsonEncode({
          'match_id': matchId,
          'fighter_id': fighterId,
          'amount': amount,
        }),
      ).timeout(const Duration(seconds: 30));

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

  // ── Auth ──

  Future<Map<String, dynamic>?> login(String privyToken,
      {String? walletAddress}) async {
    final uri = Uri.parse('$kApiBaseUrl/auth/login');
    _log('POST $uri walletAddress=$walletAddress');
    try {
      final resp = await _client.post(
        uri,
        headers: {'Content-Type': 'application/json'},
        body: jsonEncode({
          'token': privyToken,
          if (walletAddress != null) 'wallet_address': walletAddress,
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

  // ── Stream (HTTP polling fallback) ──

  Future<List<Map<String, dynamic>>> fetchLiveStreams() async {
    final uri = Uri.parse('$kApiBaseUrl/stream/live');
    try {
      final resp = await _client.get(uri, headers: _headers);
      if (resp.statusCode != 200) return [];
      final list = jsonDecode(resp.body) as List;
      return list.cast<Map<String, dynamic>>();
    } catch (e) {
      _log('fetchLiveStreams error: $e');
      return [];
    }
  }

  // ── Wallet ──

  Future<String> withdrawFunds({
    required String token,    // "sol" or "seeker"
    required String toAddress,
    required double amount,
  }) async {
    final uri = Uri.parse('$kApiBaseUrl/wallet/withdraw');
    _log('POST $uri token=$token amount=$amount');
    try {
      final resp = await _client.post(
        uri,
        headers: _headers,
        body: jsonEncode({'token': token, 'to_address': toAddress, 'amount': amount}),
      ).timeout(const Duration(seconds: 30));

      _log('POST $uri → ${resp.statusCode} ${resp.body}');

      if (resp.statusCode == 200) {
        return jsonDecode(resp.body)['tx_signature'] as String;
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
