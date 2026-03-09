import 'dart:convert';

import 'package:flutter/foundation.dart';
import 'package:livekit_client/livekit_client.dart';
import 'package:http/http.dart' as http;

/// State of the LiveKit player.
enum LiveKitPlayerState {
  idle,
  connecting,
  connected,
  error,
  disposed,
}

/// Service that connects to a LiveKit room and renders the publisher's
/// video+audio tracks via LiveKit WebRTC.
///
/// Usage:
///   final svc = LiveKitPlayerService(baseUrl: 'https://...');
///   await svc.connect(matchId);
///   // render with: VideoTrackRenderer(svc.videoTrack!)
///   svc.dispose();
class LiveKitPlayerService {
  LiveKitPlayerService({required this.baseUrl});

  final String baseUrl;

  final ValueNotifier<LiveKitPlayerState> stateNotifier =
      ValueNotifier(LiveKitPlayerState.idle);
  final ValueNotifier<String?> errorNotifier = ValueNotifier(null);
  final ValueNotifier<VideoTrack?> videoTrackNotifier = ValueNotifier(null);

  LiveKitPlayerState get state => stateNotifier.value;
  VideoTrack? get videoTrack => videoTrackNotifier.value;

  Room? _room;
  EventsListener<RoomEvent>? _listener;
  bool _disposed = false;
  String? _connectedMatchId;

  /// Connect to the match's LiveKit room as a subscriber.
  Future<void> connect(String matchId) async {
    if (_disposed) return;

    // Already connected to this match — skip redundant reconnection.
    if (_connectedMatchId == matchId &&
        (state == LiveKitPlayerState.connected ||
         state == LiveKitPlayerState.connecting)) {
      debugPrint('[LiveKitPlayer] Already connected/connecting to $matchId — skipping');
      return;
    }

    // Tear down previous connection if switching matches
    if (_room != null) {
      await _disconnectRoom();
    }

    _connectedMatchId = matchId;
    _setState(LiveKitPlayerState.connecting);

    try {
      // 1. Fetch subscriber token from backend (5s timeout)
      final resp = await http.get(
        Uri.parse('$baseUrl/stream/$matchId/livekit/token?participant=flutter-${DateTime.now().millisecondsSinceEpoch}'),
      ).timeout(const Duration(seconds: 5));
      if (resp.statusCode != 200) {
        throw Exception('Token fetch failed: ${resp.statusCode} ${resp.body}');
      }
      final data = jsonDecode(resp.body) as Map<String, dynamic>;
      final token = data['token'] as String;
      final url   = data['url'] as String;

      if (_disposed) return;
      debugPrint('[LiveKitPlayer] Token received, connecting to $url room=$matchId');

      // 2. Create room and set up event listener
      _room = Room(
        roomOptions: const RoomOptions(
          adaptiveStream: true,
          dynacast: true,
          defaultAudioPublishOptions: AudioPublishOptions(dtx: true),
        ),
      );
      _listener = _room!.createListener();

      _listener!
        ..on<RoomConnectedEvent>((_) {
          _setState(LiveKitPlayerState.connected);
          debugPrint('[LiveKitPlayer] Connected to room=$matchId');
        })
        ..on<RoomDisconnectedEvent>((event) {
          debugPrint('[LiveKitPlayer] Disconnected: ${event.reason}');
          if (!_disposed) _setError('Disconnected: ${event.reason}');
        })
        ..on<TrackSubscribedEvent>((event) {
          if (event.track is VideoTrack) {
            videoTrackNotifier.value = event.track as VideoTrack;
            debugPrint('[LiveKitPlayer] Video track subscribed from ${event.participant.identity}');
          }
          if (event.track is AudioTrack) {
            debugPrint('[LiveKitPlayer] Audio track subscribed from ${event.participant.identity}');
          }
        })
        ..on<TrackUnsubscribedEvent>((event) {
          if (event.track is VideoTrack && videoTrackNotifier.value == event.track) {
            videoTrackNotifier.value = null;
            debugPrint('[LiveKitPlayer] Video track unsubscribed');
          }
        });

      // 3. Connect to LiveKit
      await _room!.connect(url, token);

      // Check if publisher already has tracks (connected before us)
      for (final p in _room!.remoteParticipants.values) {
        for (final pub in p.trackPublications.values) {
          if (pub.track != null && pub.track is VideoTrack) {
            videoTrackNotifier.value = pub.track as VideoTrack;
            debugPrint('[LiveKitPlayer] Found existing video track from ${p.identity}');
          }
        }
      }

    } catch (e) {
      debugPrint('[LiveKitPlayer] connect() failed: $e');
      if (!_disposed) _setError(e.toString());
    }
  }

  /// Reconnect to the same match after an error. Reuses the service instance
  /// instead of creating a new one (avoids listener leaks).
  Future<void> reconnect() async {
    final matchId = _connectedMatchId;
    if (matchId == null || _disposed) return;
    debugPrint('[LiveKitPlayer] Reconnecting to $matchId');
    await _disconnectRoom();
    _connectedMatchId = null; // clear so connect() doesn't skip
    errorNotifier.value = null;
    await connect(matchId);
  }

  void _setState(LiveKitPlayerState s) {
    if (!_disposed) stateNotifier.value = s;
  }

  void _setError(String msg) {
    errorNotifier.value = msg;
    _setState(LiveKitPlayerState.error);
  }

  Future<void> _disconnectRoom() async {
    _listener?.dispose();
    _listener = null;
    try { await _room?.disconnect(); } catch (_) {}
    try { await _room?.dispose(); } catch (_) {}
    _room = null;
    videoTrackNotifier.value = null;
  }

  Future<void> dispose() async {
    _disposed = true;
    _setState(LiveKitPlayerState.disposed);
    await _disconnectRoom();
    stateNotifier.dispose();
    errorNotifier.dispose();
    videoTrackNotifier.dispose();
  }
}
