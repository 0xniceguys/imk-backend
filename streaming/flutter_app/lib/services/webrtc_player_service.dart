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
  int _connectAttempt = 0;

  /// Connect to the match's LiveKit room as a subscriber.
  Future<void> connect(String matchId) async {
    if (_disposed) {
      debugPrint('[LK] connect() called but service is disposed — ignoring');
      return;
    }

    // Already connected to this match — skip redundant reconnection.
    if (_connectedMatchId == matchId &&
        (state == LiveKitPlayerState.connected ||
         state == LiveKitPlayerState.connecting)) {
      debugPrint('[LK] Already ${state.name} to $matchId — skipping');
      return;
    }

    // Tear down previous connection if switching matches
    if (_room != null) {
      debugPrint('[LK] Tearing down previous room before connecting to $matchId');
      await _disconnectRoom();
    }

    _connectedMatchId = matchId;
    _connectAttempt++;
    final attempt = _connectAttempt;
    _setState(LiveKitPlayerState.connecting);

    final tokenUrl = '$baseUrl/stream/$matchId/livekit/token?participant=flutter-${DateTime.now().millisecondsSinceEpoch}';
    debugPrint('[LK] ── CONNECT START ── match=$matchId attempt=$attempt');
    debugPrint('[LK] Token URL: $tokenUrl');

    final sw = Stopwatch()..start();

    try {
      // 1. Fetch subscriber token from backend (5s timeout)
      final resp = await http.get(Uri.parse(tokenUrl))
          .timeout(const Duration(seconds: 5));
      debugPrint('[LK] Token response: ${resp.statusCode} (${sw.elapsedMilliseconds}ms)');
      if (resp.statusCode != 200) {
        throw Exception('Token fetch failed: ${resp.statusCode} ${resp.body}');
      }
      final data = jsonDecode(resp.body) as Map<String, dynamic>;
      final token = data['token'] as String;
      final url   = data['url'] as String;

      if (_disposed) {
        debugPrint('[LK] Disposed during token fetch — aborting');
        return;
      }
      debugPrint('[LK] Token OK, LiveKit URL=$url (${sw.elapsedMilliseconds}ms)');

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
          debugPrint('[LK] ROOM CONNECTED (${sw.elapsedMilliseconds}ms since connect start)');
          _setState(LiveKitPlayerState.connected);
        })
        ..on<RoomDisconnectedEvent>((event) {
          debugPrint('[LK] ROOM DISCONNECTED reason=${event.reason} state=$state');
          if (!_disposed) _setError('Disconnected: ${event.reason}');
        })
        ..on<TrackSubscribedEvent>((event) {
          final kind = event.track is VideoTrack ? 'VIDEO' : event.track is AudioTrack ? 'AUDIO' : 'OTHER';
          debugPrint('[LK] TRACK SUBSCRIBED kind=$kind from=${event.participant.identity} (${sw.elapsedMilliseconds}ms)');
          if (event.track is VideoTrack) {
            videoTrackNotifier.value = event.track as VideoTrack;
            debugPrint('[LK] >>> VIDEO TRACK ACTIVE — rendering should start now <<<');
          }
        })
        ..on<TrackUnsubscribedEvent>((event) {
          final kind = event.track is VideoTrack ? 'VIDEO' : 'OTHER';
          debugPrint('[LK] TRACK UNSUBSCRIBED kind=$kind');
          if (event.track is VideoTrack && videoTrackNotifier.value == event.track) {
            videoTrackNotifier.value = null;
          }
        })
        ..on<ParticipantConnectedEvent>((event) {
          debugPrint('[LK] PARTICIPANT JOINED: ${event.participant.identity} (${_room!.remoteParticipants.length} remote total)');
        })
        ..on<ParticipantDisconnectedEvent>((event) {
          debugPrint('[LK] PARTICIPANT LEFT: ${event.participant.identity} (${_room!.remoteParticipants.length} remote total)');
        });

      // 3. Connect to LiveKit
      debugPrint('[LK] Calling room.connect() ... (${sw.elapsedMilliseconds}ms)');
      await _room!.connect(url, token);
      debugPrint('[LK] room.connect() returned (${sw.elapsedMilliseconds}ms)');

      // Check if publisher already has tracks (connected before us)
      final participants = _room!.remoteParticipants;
      debugPrint('[LK] Remote participants: ${participants.length}');
      for (final p in participants.values) {
        debugPrint('[LK]   participant=${p.identity} tracks=${p.trackPublications.length}');
        for (final pub in p.trackPublications.values) {
          debugPrint('[LK]     track sid=${pub.sid} kind=${pub.kind} subscribed=${pub.subscribed} track=${pub.track != null}');
          if (pub.track != null && pub.track is VideoTrack) {
            videoTrackNotifier.value = pub.track as VideoTrack;
            debugPrint('[LK] >>> EXISTING VIDEO TRACK FOUND — rendering should start now <<<');
          }
        }
      }

      debugPrint('[LK] ── CONNECT DONE ── match=$matchId total=${sw.elapsedMilliseconds}ms hasVideo=${videoTrack != null}');

    } catch (e) {
      debugPrint('[LK] ── CONNECT FAILED ── match=$matchId error=$e (${sw.elapsedMilliseconds}ms)');
      if (!_disposed) _setError(e.toString());
    }
  }

  /// Reconnect to the same match after an error. Reuses the service instance
  /// instead of creating a new one (avoids listener leaks).
  Future<void> reconnect() async {
    final matchId = _connectedMatchId;
    if (matchId == null || _disposed) {
      debugPrint('[LK] reconnect() skipped — matchId=$matchId disposed=$_disposed');
      return;
    }
    debugPrint('[LK] ── RECONNECT ── match=$matchId (clearing old room)');
    await _disconnectRoom();
    _connectedMatchId = null; // clear so connect() doesn't skip
    errorNotifier.value = null;
    await connect(matchId);
  }

  void _setState(LiveKitPlayerState s) {
    if (!_disposed) {
      debugPrint('[LK] State: ${stateNotifier.value.name} → ${s.name}');
      stateNotifier.value = s;
    }
  }

  void _setError(String msg) {
    debugPrint('[LK] ERROR: $msg');
    errorNotifier.value = msg;
    _setState(LiveKitPlayerState.error);
  }

  Future<void> _disconnectRoom() async {
    debugPrint('[LK] _disconnectRoom() room=${_room != null} listener=${_listener != null}');
    _listener?.dispose();
    _listener = null;
    try { await _room?.disconnect(); } catch (_) {}
    try { await _room?.dispose(); } catch (_) {}
    _room = null;
    videoTrackNotifier.value = null;
  }

  Future<void> dispose() async {
    debugPrint('[LK] dispose() match=$_connectedMatchId');
    _disposed = true;
    _setState(LiveKitPlayerState.disposed);
    await _disconnectRoom();
    stateNotifier.dispose();
    errorNotifier.dispose();
    videoTrackNotifier.dispose();
  }
}
