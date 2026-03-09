import 'dart:async';
import 'dart:convert';

import 'package:flutter/foundation.dart';
import 'package:web_socket_channel/web_socket_channel.dart';

import '../core/constants.dart';
import '../models/game_state.dart';

/// Manages a WebSocket connection to a live match.
///
/// Since video+audio is now served via HLS, the WebSocket carries only:
///   - JSON text: game_state, viewer_count, round_end, match_ended, connected, pong
///
/// Binary messages are ignored (they may arrive from old backend versions).
class MatchStreamService {
  WebSocketChannel? _channel;
  StreamSubscription? _sub;
  Timer? _pingTimer;
  String? _matchId;
  bool _isTerminal = false;
  bool _isDisposed = false;
  bool _isConnecting = false;
  bool _hasConnectedEvent = false;
  DateTime _lastReconnectScheduleAt = DateTime.fromMillisecondsSinceEpoch(0);

  // Stream controllers for different message types
  final _gameStateCtrl = StreamController<GameState>.broadcast();
  final _viewerCountCtrl = StreamController<int>.broadcast();
  final _matchEndCtrl = StreamController<void>.broadcast();
  final _roundEndCtrl = StreamController<Map<String, dynamic>>.broadcast();
  final _streamingStateCtrl = StreamController<Map<String, dynamic>>.broadcast();
  final _connectionCtrl = StreamController<bool>.broadcast();

  Stream<GameState> get gameStateStream => _gameStateCtrl.stream;
  Stream<int> get viewerCountStream => _viewerCountCtrl.stream;
  Stream<void> get matchEndStream => _matchEndCtrl.stream;
  Stream<Map<String, dynamic>> get roundEndStream => _roundEndCtrl.stream;
  Stream<Map<String, dynamic>> get streamingStateStream => _streamingStateCtrl.stream;
  Stream<bool> get connectionStream => _connectionCtrl.stream;

  bool get isConnected => _hasConnectedEvent && _channel != null && _sub != null;
  bool get isConnecting => _isConnecting;
  bool get hasGivenUp => _reconnectAttempts >= _maxReconnects;
  String? get matchId => _matchId;

  void connect(String matchId) {
    if (_matchId == matchId && _channel != null) return;
    disconnect();
    _reconnectAttempts = 0;
    _upcoming4004Count = 0;
    _msgCount = 0;
    _statsTimer?.cancel();
    _statsTimer = Timer.periodic(const Duration(seconds: 5), (_) {
      debugPrint('[Stream] Stats | match=$matchId msgs=${_msgCount}/5s');
      _msgCount = 0;
    });

    _matchId = matchId;
    _isConnecting = true;
    _hasConnectedEvent = false;
    final url = '$kWsBaseUrl/ws/match/$matchId';
    debugPrint('[Stream] Connecting → $url');

    try {
      _channel = WebSocketChannel.connect(Uri.parse(url));
      _connectionCtrl.add(true);
      debugPrint('[Stream] WebSocket channel created for match $matchId');

      _sub = _channel!.stream.listen(
        _onMessage,
        onError: (error) {
          debugPrint('[Stream] ❌ WebSocket error: $error');
          _connectionCtrl.add(false);
          _scheduleReconnect(matchId);
        },
        onDone: () {
          final code = _channel?.closeCode;
          debugPrint('[Stream] WebSocket closed (code=$code) for match $matchId');
          _connectionCtrl.add(false);
          _scheduleReconnect(matchId, closeCode: code);
        },
        cancelOnError: false,
      );

      // Keepalive ping every 15s to prevent idle disconnects
      _pingTimer?.cancel();
      _pingTimer = Timer.periodic(const Duration(seconds: 15), (_) => sendPing());
    } catch (e, st) {
      debugPrint('[Stream] ❌ Connect failed: $e\n$st');
      _connectionCtrl.add(false);
      _scheduleReconnect(matchId);
    }
  }

  int _msgCount = 0;
  Timer? _statsTimer;

  void _onMessage(dynamic message) {
    _msgCount++;
    if (message is String) {
      _handleText(message);
    }
    // Binary messages ignored — video+audio served via HLS
  }

  void _handleText(String text) {
    try {
      final json = jsonDecode(text) as Map<String, dynamic>;
      final type = json['type'] as String?;

      switch (type) {
        case 'connected':
          try {
            debugPrint('[Stream] ✅ Connected to match $_matchId viewers=${json['viewer_count']}');
            _viewerCountCtrl.add(json['viewer_count'] as int? ?? 0);

            final gs = json['game_state'] as Map<String, dynamic>?;
            if (gs != null) {
              try {
                _gameStateCtrl.add(GameState.fromJson(gs));
              } catch (e) {
                debugPrint('[Stream] ⚠️ Failed to parse game_state in connected msg: $e');
              }
            }

            // Forward streaming state so globalHlsPreloaderProvider fires on
            // cold opens / reconnects (the backend includes this in the
            // connected handshake when a stream is already ready).
            final streamingState = json['streaming_state'] as String?;
            if (streamingState != null) {
              debugPrint('[Stream] 📺 Initial streaming state from connected: $streamingState');
              if (streamingState == 'ready' && _matchId != null) {
                // Build a canonical hls_url so the preloader has everything it needs.
                final hlsUrlHint = '/stream/$_matchId/stream.m3u8';
                _streamingStateCtrl.add({
                  'state': streamingState,
                  'hls_url': hlsUrlHint,
                });
              } else if (streamingState == 'initializing' || streamingState == 'error') {
                _streamingStateCtrl.add({'state': streamingState});
              }
            }
          } catch (e, st) {
            debugPrint('[Stream] ❌ Error handling connected message: $e\n$st');
          }

        case 'game_state':
          _gameStateCtrl.add(GameState.fromJson(json));

        case 'viewer_count':
          _viewerCountCtrl.add(json['count'] as int? ?? 0);

        case 'streaming_state':
          final state = json['state'] as String?;
          final hlsUrl = json['hls_url'] as String?;
          final error = json['error'] as String?;
          debugPrint('[Stream] 📺 Streaming state: $state ${hlsUrl != null ? "($hlsUrl)" : ""}${error != null ? " ERROR: $error" : ""}');
          _streamingStateCtrl.add(json);

        case 'round_end':
          final p1Won = json['p1_won'] == true;
          final roundNum = json['current_round'];
          final matchOver = json['match_over'] == true;
          debugPrint(
            '[Stream] 🥊 Round $roundNum ended: ${p1Won ? "P1" : "P2"} won '
            '(match_over=$matchOver, timestamp=${DateTime.now().toIso8601String()})',
          );
          _roundEndCtrl.add(json);

        case 'match_ended':
          debugPrint('[Stream] 🏁 match_ended received at ${DateTime.now().toIso8601String()} — Flutter will wait for stream to 404 before navigating');
          _matchEndCtrl.add(null);

        case 'pong':
          break;

        default:
          debugPrint('[Stream] Unknown message type: $type');
      }
    } catch (e, st) {
      debugPrint('[Stream] ❌ Failed to parse: $e\n$st');
    }
  }

  Timer? _reconnectTimer;
  int _reconnectAttempts = 0;
  static const _maxReconnects = 10;

  // How long to keep retrying a "no runner yet" (4004) situation.
  // A match can take up to 90s from upcoming → live (60s countdown + startup).
  // We retry every 5s for up to 18 attempts = 90s of patience.
  static const _upcomingRetrySeconds = 5;
  static const _maxUpcomingRetries = 18;
  int _upcoming4004Count = 0;

  void _scheduleReconnect(String matchId, {int? closeCode}) {
    _reconnectTimer?.cancel();

    if (closeCode == 4004) {
      // 4004 = "no active runner for this match right now"
      // This happens when:
      //   (A) match is upcoming and the runner hasn't started yet → retry
      //   (B) match has ended → eventually emit matchEnd after N retries
      _upcoming4004Count++;
      if (_upcoming4004Count <= _maxUpcomingRetries) {
        debugPrint(
          '[Stream] 4004 — waiting for runner '
          '(attempt $_upcoming4004Count/$_maxUpcomingRetries, '
          'retry in ${_upcomingRetrySeconds}s)...',
        );
        _reconnectTimer = Timer(const Duration(seconds: _upcomingRetrySeconds), () {
          if (_matchId == matchId) {
            _channel = null;
            _sub?.cancel();
            _sub = null;
            connect(matchId);
          }
        });
        return;
      }
      // After N retries with 4004 — runner never started, treat as ended
      debugPrint(
        '[Stream] 4004 after $_upcoming4004Count attempts — '
        'runner never started, treating as match ended',
      );
      _matchEndCtrl.add(null);
      return;
    }

    // Any other close code: exponential backoff reconnect
    if (_reconnectAttempts >= _maxReconnects) {
      debugPrint('[Stream] ⛔ Max reconnects ($_maxReconnects) reached for $matchId — giving up');
      return;
    }

    final delaySeconds = 3 * (1 << _reconnectAttempts.clamp(0, 4));
    _reconnectAttempts++;
    debugPrint(
      '[Stream] Reconnecting in ${delaySeconds}s '
      '(attempt $_reconnectAttempts/$_maxReconnects)...',
    );
    _reconnectTimer = Timer(Duration(seconds: delaySeconds), () {
      if (_matchId == matchId) {
        _channel = null;
        _sub?.cancel();
        _sub = null;
        connect(matchId);
      }
    });
  }

  void sendPing() {
    _channel?.sink.add(jsonEncode({'type': 'ping'}));
  }

  /// Resets reconnect counter and retries connection — use for "tap to retry" UI.
  void resetAndReconnect(String matchId) {
    _reconnectAttempts = 0;
    _upcoming4004Count = 0;
    _matchId = null;
    connect(matchId);
  }

  void disconnect() {
    _statsTimer?.cancel();
    _statsTimer = null;
    _reconnectTimer?.cancel();
    _reconnectTimer = null;
    _pingTimer?.cancel();
    _pingTimer = null;
    _sub?.cancel();
    _sub = null;
    _channel?.sink.close();
    _channel = null;
    _matchId = null;
    _reconnectAttempts = 0;
    _upcoming4004Count = 0;
    debugPrint('[Stream] Disconnected');
  }

  void dispose() {
    _isDisposed = true;
    disconnect();
    _gameStateCtrl.close();
    _viewerCountCtrl.close();
    _matchEndCtrl.close();
    _roundEndCtrl.close();
    _streamingStateCtrl.close();
    _connectionCtrl.close();
  }
}
