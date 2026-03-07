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

/// How long to delay video frames so they stay in sync with the HLS audio
/// pipeline (2s init wait + ~500ms ExoPlayer startup buffer).
/// Tune this if audio and video drift on device.
const _kVideoDelayMs = 2500;

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
    _reconnectAttempts = 0;
    _videoFrameCount = 0;
    _audioChunkCount = 0;
    _statsTimer?.cancel();
    // Log stats every 5s: video fps, audio chunks/s, total bytes
    _statsTimer = Timer.periodic(const Duration(seconds: 5), (_) {
      debugPrint(
        '[Stream] Stats | match=$matchId '
        'video=${_videoFrameCount}f/5s '
        'audio=${_audioChunkCount}chunks/5s',
      );
      _videoFrameCount = 0;
      _audioChunkCount = 0;
    });

    _matchId = matchId;
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
      _pingTimer = Timer.periodic(const Duration(seconds: 15), (_) {
        sendPing();
      });
    } catch (e, st) {
      debugPrint('[Stream] ❌ Connect failed: $e\n$st');
      _connectionCtrl.add(false);
      _scheduleReconnect(matchId);
    }
  }

  // Diagnostic counters (reset every 5s)
  int _videoFrameCount = 0;
  int _audioChunkCount = 0;
  Timer? _statsTimer;

  void _onMessage(dynamic message) {
    if (message is String) {
      _handleText(message);
    } else if (message is List<int>) {
      final bytes = Uint8List.fromList(message);
      if (bytes.isEmpty) {
        debugPrint('[Stream] ⚠ Empty binary message received');
        return;
      }

      final first = bytes[0];
      final size = bytes.length;

      if (first == 0x01) {
        // Audio: 0x01 prefix + Opus/OGG payload
        _audioChunkCount++;
        debugPrint('[Audio] chunk #$_audioChunkCount size=${size}B payload=${size - 1}B');
        _audioChunkCtrl.add(bytes.sublist(1));
      } else if (first == 0x00) {
        // Video with legacy 0x00 prefix — strip it (backward compat with old backend)
        _videoFrameCount++;
        final payload = bytes.sublist(1);
        debugPrint('[Video] frame #$_videoFrameCount (legacy 0x00 prefix) size=${payload.length}B');
        if (payload.isNotEmpty) _addDelayedFrame(payload);
      } else if (first == 0xFF && size > 1 && bytes[1] == 0xD8) {
        // Raw JPEG — current backend (no prefix)
        _videoFrameCount++;
        debugPrint('[Video] frame #$_videoFrameCount (raw JPEG) size=${size}B');
        _addDelayedFrame(bytes);
      } else {
        // Unknown format — log the first 8 bytes for diagnosis
        final preview = bytes.take(8).map((b) => '0x${b.toRadixString(16).padLeft(2, '0')}').join(' ');
        debugPrint('[Stream] ⚠ Unknown binary type first=0x${first.toRadixString(16)} size=${size}B preview=[$preview]');
      }
    } else {
      debugPrint('[Stream] ⚠ Unexpected message type: ${message.runtimeType}');
    }
  }

  /// Schedules a frame to be emitted after [_kVideoDelayMs], keeping video
  /// in sync with the HLS audio pipeline which has a similar fixed latency.
  void _addDelayedFrame(Uint8List frame) {
    Future.delayed(const Duration(milliseconds: _kVideoDelayMs), () {
      if (!_frameCtrl.isClosed) _frameCtrl.add(frame);
    });
  }

  void _handleText(String text) {
    try {
      final json = jsonDecode(text) as Map<String, dynamic>;
      final type = json['type'] as String?;
      debugPrint('[Stream] JSON type=$type len=${text.length}');

      switch (type) {
        case 'connected':
          debugPrint('[Stream] ✅ Connected to match $_matchId viewers=${json['viewer_count']}');
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
          debugPrint('[Stream] 🥊 Round ended: ${json['p1_won'] == true ? "P1" : "P2"} won p1=${json['p1_health']} p2=${json['p2_health']}');
          _roundEndCtrl.add(json);

        case 'match_ended':
          debugPrint('[Stream] 🏁 Match ended');
          _matchEndCtrl.add(null);

        case 'pong':
          break;

        default:
          _log('Unknown message type: $type');
      }
    } catch (e, st) {
      debugPrint('[Stream] ❌ Failed to parse text: $e\n$st\nRaw: ${text.substring(0, text.length.clamp(0, 200))}');
    }
  }

  Timer? _reconnectTimer;
  int _reconnectAttempts = 0;
  static const _maxReconnects = 5;

  void _scheduleReconnect(String matchId, {int? closeCode}) {
    // 4004 = no active runner — the match is over. Emit matchEnd so the
    // screen navigates to post-match rather than showing "Connecting..." forever.
    if (closeCode == 4004) {
      debugPrint('[Stream] Match $matchId has no runner (4004) — going to post-match');
      _matchEndCtrl.add(null);
      return;
    }
    if (_reconnectAttempts >= _maxReconnects) {
      debugPrint('[Stream] ⛔ Max reconnects ($_maxReconnects) reached for $matchId — giving up');
      return;
    }

    // Exponential backoff: 3s, 6s, 12s, 24s, 48s
    final delaySeconds = 3 * (1 << _reconnectAttempts.clamp(0, 4));
    _reconnectAttempts++;
    _reconnectTimer?.cancel();
    debugPrint('[Stream] Reconnecting to $matchId in ${delaySeconds}s (attempt $_reconnectAttempts/$_maxReconnects)...');
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
    debugPrint('[Stream] Disconnected');
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
