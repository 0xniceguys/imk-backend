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
/// video+audio tracks. Replaces [HlsPlayerService] when the backend
/// signals mode=livekit.
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
  bool _disposed = false;

  /// Connect to the match's LiveKit room as a subscriber.
  Future<void> connect(String matchId) async {
    if (_disposed) return;
    _setState(LiveKitPlayerState.connecting);

    try {
      // 1. Fetch subscriber token from backend
      final resp = await http.get(
        Uri.parse('$baseUrl/stream/$matchId/livekit/token?participant=flutter-${DateTime.now().millisecondsSinceEpoch}'),
      );
      if (resp.statusCode != 200) {
        throw Exception('Token fetch failed: ${resp.statusCode} ${resp.body}');
      }
      final data = jsonDecode(resp.body) as Map<String, dynamic>;
      final token = data['token'] as String;
      final url   = data['url'] as String;

      debugPrint('[LiveKitPlayer] 🔑 Token received, connecting to $url room=$matchId');

      // 2. Create room and connect
      _room = Room();

      // Listen for tracks from the publisher
      _room!.addListener(_RoomListener(
        onTrackSubscribed: (RemoteTrackPublication pub, RemoteParticipant participant) {
          if (pub.track is VideoTrack) {
            videoTrackNotifier.value = pub.track as VideoTrack;
            debugPrint('[LiveKitPlayer] 🎥 Video track subscribed from ${participant.identity}');
          }
          if (pub.track is AudioTrack) {
            debugPrint('[LiveKitPlayer] 🔊 Audio track subscribed from ${participant.identity}');
          }
        },
        onConnected: () {
          _setState(LiveKitPlayerState.connected);
          debugPrint('[LiveKitPlayer] ✅ Connected to room=$matchId');
        },
        onDisconnected: (DisconnectReason? reason) {
          debugPrint('[LiveKitPlayer] ❌ Disconnected: $reason');
          if (!_disposed) _setError('Disconnected: $reason');
        },
      ));

      // 3. Connect to LiveKit
      await _room!.connect(
        url,
        token,
        roomOptions: const RoomOptions(
          adaptiveStream: true,
          dynacast: true,
          defaultAudioPublishOptions: AudioPublishOptions(dtx: true),
        ),
      );

      // Check if publisher already has tracks (connected before us)
      for (final p in _room!.remoteParticipants.values) {
        for (final pub in p.trackPublications.values) {
          if (pub.track != null && pub.track is VideoTrack) {
            videoTrackNotifier.value = pub.track as VideoTrack;
            debugPrint('[LiveKitPlayer] 🎥 Found existing video track from ${p.identity}');
          }
        }
      }

    } catch (e) {
      debugPrint('[LiveKitPlayer] ❌ connect() failed: $e');
      _setError(e.toString());
    }
  }

  void _setState(LiveKitPlayerState s) {
    if (!_disposed) stateNotifier.value = s;
  }

  void _setError(String msg) {
    errorNotifier.value = msg;
    _setState(LiveKitPlayerState.error);
  }

  Future<void> dispose() async {
    _disposed = true;
    _setState(LiveKitPlayerState.disposed);
    try { await _room?.disconnect(); } catch (_) {}
    try { await _room?.dispose(); } catch (_) {}
    _room = null;
    stateNotifier.dispose();
    errorNotifier.dispose();
    videoTrackNotifier.dispose();
  }
}

/// Simple room event listener.
class _RoomListener extends RoomListener {
  _RoomListener({
    this.onTrackSubscribed,
    this.onConnected,
    this.onDisconnected,
  });

  final void Function(RemoteTrackPublication, RemoteParticipant)? onTrackSubscribed;
  final void Function()? onConnected;
  final void Function(DisconnectReason?)? onDisconnected;
}
