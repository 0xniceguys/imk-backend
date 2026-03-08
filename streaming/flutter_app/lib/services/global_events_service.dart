import 'dart:async';
import 'dart:convert';

import 'package:flutter/foundation.dart';
import 'package:web_socket_channel/web_socket_channel.dart';

import '../core/constants.dart';

/// Service to listen for global match events (when ANY match goes live).
/// This solves the race condition where matches start/end before Flutter can connect.
class GlobalEventsService {
  WebSocketChannel? _channel;
  StreamSubscription? _sub;
  Timer? _pingTimer;
  bool _disposed = false;

  final _matchStatusCtrl = StreamController<Map<String, dynamic>>.broadcast();
  Stream<Map<String, dynamic>> get matchStatusStream => _matchStatusCtrl.stream;

  bool get isConnected => _channel != null;

  void connect() {
    if (_disposed) return;
    disconnect();

    final url = '$kWsBaseUrl/ws/events';
    debugPrint('[GlobalEvents] Connecting → $url');

    try {
      _channel = WebSocketChannel.connect(Uri.parse(url));
      debugPrint('[GlobalEvents] Channel created');

      _sub = _channel!.stream.listen(
        _onMessage,
        onError: (error) {
          debugPrint('[GlobalEvents] ❌ Error: $error');
          _scheduleReconnect();
        },
        onDone: () {
          debugPrint('[GlobalEvents] Closed');
          _scheduleReconnect();
        },
        cancelOnError: false,
      );

      // Send ping every 15s to keep alive
      _pingTimer?.cancel();
      _pingTimer = Timer.periodic(const Duration(seconds: 15), (_) {
        _channel?.sink.add(jsonEncode({'type': 'ping'}));
      });
    } catch (e) {
      debugPrint('[GlobalEvents] ❌ Connect failed: $e');
      _scheduleReconnect();
    }
  }

  void _onMessage(dynamic message) {
    if (message is! String) return;

    try {
      final json = jsonDecode(message) as Map<String, dynamic>;
      final type = json['type'] as String?;

      switch (type) {
        case 'connected':
          debugPrint('[GlobalEvents] ✅ Connected');
          break;

        case 'match_status_changed':
          final matchId = json['match_id'] as String?;
          final status = json['status'] as String?;
          debugPrint('[GlobalEvents] 🎮 Match $matchId → $status');
          _matchStatusCtrl.add(json);
          break;

        case 'pong':
          // Keep-alive response
          break;

        default:
          debugPrint('[GlobalEvents] Unknown type: $type');
      }
    } catch (e) {
      debugPrint('[GlobalEvents] Parse error: $e');
    }
  }

  Timer? _reconnectTimer;
  int _reconnectAttempts = 0;

  void _scheduleReconnect() {
    if (_disposed) return;
    _reconnectTimer?.cancel();

    if (_reconnectAttempts >= 5) {
      debugPrint('[GlobalEvents] Max reconnects reached');
      return;
    }

    final delay = 2 * (1 << _reconnectAttempts.clamp(0, 3));
    _reconnectAttempts++;

    debugPrint('[GlobalEvents] Reconnecting in ${delay}s...');
    _reconnectTimer = Timer(Duration(seconds: delay), connect);
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
    _reconnectAttempts = 0;
  }

  void dispose() {
    _disposed = true;
    disconnect();
    _matchStatusCtrl.close();
  }
}