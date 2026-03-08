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

class StreamingStateEvent {
  const StreamingStateEvent({
    required this.matchId,
    required this.status,
    this.hlsUrl,
    required this.raw,
  });

  final String? matchId;
  final String status;
  final String? hlsUrl;
  final Map<String, dynamic> raw;

  bool get isReady => status == 'ready';

  factory StreamingStateEvent.fromJson(Map<String, dynamic> json) {
    final status = (json['status'] ?? json['state'] ?? json['stream_status'])
        ?.toString()
        .trim()
        .toLowerCase();
    final hlsUrl = json['hls_url']?.toString().trim();
    return StreamingStateEvent(
      matchId: json['match_id']?.toString(),
      status: status == null || status.isEmpty ? 'unknown' : status,
      hlsUrl: hlsUrl == null || hlsUrl.isEmpty ? null : hlsUrl,
      raw: json,
    );
  }
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
  bool _isTerminal = false;
  bool _isDisposed = false;
  bool _isConnecting = false;
  bool _hasConnectedEvent = false;
  DateTime _lastReconnectScheduleAt = DateTime.fromMillisecondsSinceEpoch(0);

  // Stream controllers for different message types
  final _gameStateCtrl = StreamController<GameState>.broadcast();
  final _frameCtrl = StreamController<Uint8List>.broadcast();
  final _viewerCountCtrl = StreamController<int>.broadcast();
  final _matchEndCtrl = StreamController<void>.broadcast();
  final _roundEndCtrl = StreamController<Map<String, dynamic>>.broadcast();
  final _streamingStateCtrl = StreamController<StreamingStateEvent>.broadcast();
  final _connectionCtrl = StreamController<bool>.broadcast();

  Stream<GameState> get gameStateStream => _gameStateCtrl.stream;
  Stream<Uint8List> get frameStream => _frameCtrl.stream;
  Stream<int> get viewerCountStream => _viewerCountCtrl.stream;
  Stream<void> get matchEndStream => _matchEndCtrl.stream;
  Stream<Map<String, dynamic>> get roundEndStream => _roundEndCtrl.stream;
  Stream<StreamingStateEvent> get streamingStateStream =>
      _streamingStateCtrl.stream;
  Stream<bool> get connectionStream => _connectionCtrl.stream;

  bool get isConnected => _hasConnectedEvent && _channel != null && _sub != null;
  bool get isConnecting => _isConnecting;
  bool get hasGivenUp =>
      _reconnectAttempts >= _maxReconnects ||
      _runnerNotReadyAttempts >= _maxRunnerNotReadyReconnects;
  String? get matchId => _matchId;

  void connect(String matchId) {
    if (_isDisposed) return;
    if (_matchId == matchId) {
      if (_isTerminal) {
        _log('Skip connect for $matchId: terminal state');
        return;
      }
      if (_isConnecting || _channel != null || _sub != null) {
        _log('Skip connect for $matchId: already connecting/connected');
        return;
      }
      if (_reconnectTimer?.isActive ?? false) {
        _log('Skip connect for $matchId: reconnect already scheduled');
        return;
      }
    }

    final isNewMatch = _matchId != matchId;
    if (isNewMatch) {
      _reconnectAttempts = 0;
      _runnerNotReadyAttempts = 0;
      _isTerminal = false;
      _lastReconnectScheduleAt = DateTime.fromMillisecondsSinceEpoch(0);
    }

    _reconnectTimer?.cancel();
    _reconnectTimer = null;
    _pingTimer?.cancel();
    _pingTimer = null;
    _sub?.cancel();
    _sub = null;
    _channel?.sink.close();
    _channel = null;

    _matchId = matchId;
    _isConnecting = true;
    _hasConnectedEvent = false;
    final url = '$kWsBaseUrl/ws/match/$matchId';
    _log('Connecting to $url');

    try {
      _channel = WebSocketChannel.connect(Uri.parse(url));

      _sub = _channel!.stream.listen(
        _onMessage,
        onError: (error) {
          _log('WebSocket error: $error');
          _isConnecting = false;
          _hasConnectedEvent = false;
          _pingTimer?.cancel();
          _pingTimer = null;
          _sub = null;
          _channel = null;
          _connectionCtrl.add(false);
          _scheduleReconnect(matchId);
        },
        onDone: () {
          // Read close code BEFORE nulling channel
          final code = _channel?.closeCode;
          _log('WebSocket closed (code=$code)');
          _isConnecting = false;
          _hasConnectedEvent = false;
          _pingTimer?.cancel();
          _pingTimer = null;
          _sub = null;
          _channel = null;
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
      _isConnecting = false;
      _hasConnectedEvent = false;
      _connectionCtrl.add(false);
      _scheduleReconnect(matchId);
    }
  }

  void _onMessage(dynamic message) {
    if (message is String) {
      _handleText(message);
    } else if (message is List<int>) {
      // In HLS mode we usually don't consume WS JPEG frames. Skip allocations
      // when nobody is listening to reduce GC/CPU pressure on mobile.
      if (!_frameCtrl.hasListener) return;
      if (message is Uint8List) {
        _frameCtrl.add(message);
      } else {
        _frameCtrl.add(Uint8List.fromList(message));
      }
    }
  }

  void _handleText(String text) {
    try {
      final json = jsonDecode(text) as Map<String, dynamic>;
      final type = json['type'] as String?;

      switch (type) {
        case 'connected':
          _isConnecting = false;
          _isTerminal = false;
          _hasConnectedEvent = true;
          _reconnectAttempts = 0;
          _runnerNotReadyAttempts = 0;
          _connectionCtrl.add(true);
          _log(
            'Connected to match $_matchId (viewers: ${json['viewer_count']})',
          );
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
          _isConnecting = false;
          _isTerminal = true;
          _hasConnectedEvent = false;
          _reconnectTimer?.cancel();
          _reconnectTimer = null;
          _log('Match ended for $_matchId');
          _matchEndCtrl.add(null);

        case 'streaming_state':
          final evt = StreamingStateEvent.fromJson(json);
          _log(
            'Streaming state for $_matchId: ${evt.status} (hls_url=${evt.hlsUrl})',
          );
          _streamingStateCtrl.add(evt);

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
  int _runnerNotReadyAttempts = 0;
  static const _maxReconnects = 5;
  static const _maxRunnerNotReadyReconnects = 30;
  static const _minReconnectScheduleGapMs = 450;

  void _scheduleReconnect(String matchId, {int? closeCode}) {
    if (_isDisposed) return;
    if (_matchId != matchId) return;
    if (_isTerminal) {
      _log('Reconnect blocked for $matchId: terminal state');
      return;
    }
    if (_reconnectTimer?.isActive ?? false) {
      _log('Reconnect already scheduled for $matchId');
      return;
    }
    final now = DateTime.now();
    if (now.difference(_lastReconnectScheduleAt).inMilliseconds <
        _minReconnectScheduleGapMs) {
      _log('Reconnect debounced for $matchId');
      return;
    }
    _lastReconnectScheduleAt = now;

    // 4004 is also returned during startup windows before the runner is ready.
    // Keep retrying quickly instead of treating it as an ended-match signal.
    if (closeCode == 4004) {
      if (_runnerNotReadyAttempts >= _maxRunnerNotReadyReconnects) {
        _log(
          'Runner-not-ready retries ($_maxRunnerNotReadyReconnects) reached for $matchId',
        );
        return;
      }
      _runnerNotReadyAttempts++;
      _reconnectTimer?.cancel();
      _log(
        'Runner not ready for $matchId (4004). Retrying in 1s '
        '(attempt $_runnerNotReadyAttempts/$_maxRunnerNotReadyReconnects)...',
      );
      _reconnectTimer = Timer(const Duration(seconds: 1), () {
        if (_matchId == matchId) {
          connect(matchId);
        }
      });
      return;
    }

    _runnerNotReadyAttempts = 0;
    if (_reconnectAttempts >= _maxReconnects) {
      _log('Max reconnect attempts ($_maxReconnects) reached for $matchId');
      return;
    }

    // Exponential backoff: 3s, 6s, 12s, 24s, 48s
    final delaySeconds = 3 * (1 << _reconnectAttempts.clamp(0, 4));
    _reconnectAttempts++;
    _reconnectTimer?.cancel();
    _log(
      'Reconnecting to $matchId in ${delaySeconds}s (attempt $_reconnectAttempts)...',
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

  void markTerminal(String matchId) {
    if (_matchId != matchId) return;
    _isConnecting = false;
    _isTerminal = true;
    _hasConnectedEvent = false;
    _reconnectTimer?.cancel();
    _reconnectTimer = null;
    _pingTimer?.cancel();
    _pingTimer = null;
    _sub?.cancel();
    _sub = null;
    _channel?.sink.close();
    _channel = null;
    _connectionCtrl.add(false);
    _log('Marked terminal for $matchId');
  }

  /// Resets the reconnect counter and retries the connection manually.
  /// Call this when the UI shows a "tap to retry" button.
  void resetAndReconnect(String matchId) {
    _reconnectAttempts = 0;
    _runnerNotReadyAttempts = 0;
    _isConnecting = false;
    _hasConnectedEvent = false;
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
    _runnerNotReadyAttempts = 0;
    _isConnecting = false;
    _hasConnectedEvent = false;
    _isTerminal = false;
  }

  void dispose() {
    _isDisposed = true;
    disconnect();
    _gameStateCtrl.close();
    _frameCtrl.close();
    _viewerCountCtrl.close();
    _matchEndCtrl.close();
    _roundEndCtrl.close();
    _streamingStateCtrl.close();
    _connectionCtrl.close();
  }
}
