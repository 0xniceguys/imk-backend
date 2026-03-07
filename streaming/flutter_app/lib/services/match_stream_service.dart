import 'dart:async';
import 'dart:convert';

import 'package:flutter/foundation.dart';
import 'package:web_socket_channel/web_socket_channel.dart';

import '../core/constants.dart';
import '../models/game_state.dart';

void _log(String msg) {
  // ignore: avoid_print
  if (kDebugMode) print('[WS] $msg');
}

/// Manages a WebSocket connection to a live match.
///
/// Receives two types of messages from the backend:
/// - JSON text: game state updates, viewer count, round end, match end
/// - Binary: JPEG frame bytes from the emulator (60fps)
class MatchStreamService {
  WebSocketChannel? _channel;
  StreamSubscription? _sub;
  Timer? _pingTimer;
  String? _matchId;

  // Stream controllers for different message types
  final _gameStateCtrl = StreamController<GameState>.broadcast();
  final _frameCtrl = StreamController<Uint8List>.broadcast();
  final _audioChunkCtrl = StreamController<Uint8List>.broadcast();
  final _viewerCountCtrl = StreamController<int>.broadcast();
  final _matchEndCtrl = StreamController<void>.broadcast();
  final _roundEndCtrl = StreamController<Map<String, dynamic>>.broadcast();
  final _connectionCtrl = StreamController<bool>.broadcast();

  Stream<GameState> get gameStateStream => _gameStateCtrl.stream;
  /// JPEG frame bytes (video)
  Stream<Uint8List> get frameStream => _frameCtrl.stream;
  /// Raw Opus/OGG audio chunks — wire up to a player (e.g. just_audio)
  Stream<Uint8List> get audioChunkStream => _audioChunkCtrl.stream;
  Stream<int> get viewerCountStream => _viewerCountCtrl.stream;
  Stream<void> get matchEndStream => _matchEndCtrl.stream;
  Stream<Map<String, dynamic>> get roundEndStream => _roundEndCtrl.stream;
  Stream<bool> get connectionStream => _connectionCtrl.stream;

  bool get isConnected => _channel != null;
  bool get hasGivenUp => _reconnectAttempts >= _maxReconnects;
  String? get matchId => _matchId;

  void connect(String matchId) {
    if (_matchId == matchId && _channel != null) return;
    disconnect();
    _reconnectAttempts = 0; // Reset counter on fresh connect

    _matchId = matchId;
    final url = '$kWsBaseUrl/ws/match/$matchId';
    _log('Connecting to $url');

    try {
      _channel = WebSocketChannel.connect(Uri.parse(url));
      _connectionCtrl.add(true);

      _sub = _channel!.stream.listen(
        _onMessage,
        onError: (error) {
          _log('WebSocket error: $error');
          _connectionCtrl.add(false);
          _scheduleReconnect(matchId);
        },
        onDone: () {
          // Read close code BEFORE nulling channel
          final code = _channel?.closeCode;
          _log('WebSocket closed (code=$code)');
          _connectionCtrl.add(false);
          _scheduleReconnect(matchId, closeCode: code);
        },
        cancelOnError: false,
      );

      // Keepalive ping every 15s to prevent idle disconnects
      _pingTimer?.cancel();
      _pingTimer = Timer.periodic(const Duration(seconds: 15), (_) {
        sendPing();
      });
    } catch (e) {
      _log('Connect failed: $e');
      _connectionCtrl.add(false);
      _scheduleReconnect(matchId);
    }
  }

  void _onMessage(dynamic message) {
    if (message is String) {
      _handleText(message);
    } else if (message is List<int>) {
      final bytes = Uint8List.fromList(message);
      if (bytes.isEmpty) return;
      // Audio messages are prefixed with 0x01.
      // Video frames are raw JPEG bytes — JPEG always starts with 0xFF (0xD8),
      // so these are unambiguous without any prefix byte.
      if (bytes[0] == 0x01) {
        // Strip the 0x01 prefix and emit the Opus/OGG payload
        _audioChunkCtrl.add(bytes.sublist(1));
      } else {
        // Raw JPEG — pass through as-is to frameStream
        _frameCtrl.add(bytes);
      }
    }
  }

  void _handleText(String text) {
    try {
      final json = jsonDecode(text) as Map<String, dynamic>;
      final type = json['type'] as String?;

      switch (type) {
        case 'connected':
          _log('Connected to match $_matchId (viewers: ${json['viewer_count']})');
          _viewerCountCtrl.add(json['viewer_count'] as int? ?? 0);
          final gs = json['game_state'] as Map<String, dynamic>?;
          if (gs != null) {
            _gameStateCtrl.add(GameState.fromJson(gs));
          }

        case 'game_state':
          _gameStateCtrl.add(GameState.fromJson(json));

        case 'viewer_count':
          _viewerCountCtrl.add(json['count'] as int? ?? 0);

        case 'round_end':
          _log('Round ended: ${json['p1_won'] == true ? "P1" : "P2"} won');
          _roundEndCtrl.add(json);

        case 'match_ended':
          _log('Match ended');
          _matchEndCtrl.add(null);

        case 'pong':
          break;

        default:
          _log('Unknown message type: $type');
      }
    } catch (e) {
      _log('Failed to parse text message: $e');
    }
  }

  Timer? _reconnectTimer;
  int _reconnectAttempts = 0;
  static const _maxReconnects = 5;

  void _scheduleReconnect(String matchId, {int? closeCode}) {
    // 4004 = no active runner — the match is over. Emit matchEnd so the
    // screen navigates to post-match rather than showing "Connecting..." forever.
    if (closeCode == 4004) {
      _log('Match $matchId has no active runner (4004) — emitting matchEnd');
      _matchEndCtrl.add(null);
      return;
    }
    if (_reconnectAttempts >= _maxReconnects) {
      _log('Max reconnect attempts ($_maxReconnects) reached for $matchId');
      return;
    }

    // Exponential backoff: 3s, 6s, 12s, 24s, 48s
    final delaySeconds = 3 * (1 << _reconnectAttempts.clamp(0, 4));
    _reconnectAttempts++;
    _reconnectTimer?.cancel();
    _log('Reconnecting to $matchId in ${delaySeconds}s (attempt $_reconnectAttempts)...');
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

  /// Resets the reconnect counter and retries the connection manually.
  /// Call this when the UI shows a "tap to retry" button.
  void resetAndReconnect(String matchId) {
    _reconnectAttempts = 0;
    _matchId = null; // force re-connect even if same matchId
    connect(matchId);
  }

  void disconnect() {
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
  }

  void dispose() {
    disconnect();
    _gameStateCtrl.close();
    _frameCtrl.close();
    _audioChunkCtrl.close();
    _viewerCountCtrl.close();
    _matchEndCtrl.close();
    _roundEndCtrl.close();
    _connectionCtrl.close();
  }
}
