import 'dart:convert';

import 'package:flutter/foundation.dart';
import 'package:flutter_webrtc/flutter_webrtc.dart';
import 'package:http/http.dart' as http;

/// State of the WebRTC player.
enum WebRtcPlayerState {
  idle,
  connecting,
  connected,
  error,
  disposed,
}

/// Service that creates a WebRTC connection to the mediasoup SFU for a live
/// match. Replaces [HlsPlayerService] when the backend signals mode=webrtc.
///
/// Usage:
///   final svc = WebRtcPlayerService(baseUrl: 'https://...');
///   await svc.connect(matchId);
///   // render with: RTCVideoView(svc.renderer)
///   svc.dispose();
class WebRtcPlayerService {
  WebRtcPlayerService({required this.baseUrl});

  final String baseUrl;

  final RTCVideoRenderer renderer = RTCVideoRenderer();
  final ValueNotifier<WebRtcPlayerState> stateNotifier =
      ValueNotifier(WebRtcPlayerState.idle);
  final ValueNotifier<String?> errorNotifier = ValueNotifier(null);

  WebRtcPlayerState get state => stateNotifier.value;

  RTCPeerConnection? _pc;
  bool _disposed = false;

  /// Connect to the match's WebRTC stream.
  Future<void> connect(String matchId) async {
    if (_disposed) return;
    _setState(WebRtcPlayerState.connecting);

    try {
      await renderer.initialize();

      // Create peer connection — receive-only (recvonly)
      final config = <String, dynamic>{
        'iceServers': [
          {'urls': 'stun:stun.l.google.com:19302'},
        ],
        'sdpSemantics': 'unified-plan',
      };
      _pc = await createPeerConnection(config);

      // Add receive-only transceivers for video + audio
      await _pc!.addTransceiver(
        kind: RTCRtpMediaType.RTCRtpMediaTypeVideo,
        init: RTCRtpTransceiverInit(direction: TransceiverDirection.RecvOnly),
      );
      await _pc!.addTransceiver(
        kind: RTCRtpMediaType.RTCRtpMediaTypeAudio,
        init: RTCRtpTransceiverInit(direction: TransceiverDirection.RecvOnly),
      );

      // Attach remote stream to renderer
      _pc!.onTrack = (RTCTrackEvent event) {
        if (event.streams.isNotEmpty) {
          renderer.srcObject = event.streams[0];
          debugPrint('[WebRTCPlayer] 🎥 Track attached: ${event.track.kind}');
        }
      };

      _pc!.onIceConnectionState = (RTCIceConnectionState s) {
        debugPrint('[WebRTCPlayer] ICE state: $s');
        if (s == RTCIceConnectionState.RTCIceConnectionStateConnected ||
            s == RTCIceConnectionState.RTCIceConnectionStateCompleted) {
          _setState(WebRtcPlayerState.connected);
        } else if (s == RTCIceConnectionState.RTCIceConnectionStateFailed) {
          _setError('ICE connection failed');
        }
      };

      // Create SDP offer
      final offer = await _pc!.createOffer({'offerToReceiveVideo': 1, 'offerToReceiveAudio': 1});
      await _pc!.setLocalDescription(offer);
      debugPrint('[WebRTCPlayer] 📤 Sending SDP offer for match=$matchId');

      // POST offer to backend → get answer from mediasoup
      final resp = await http.post(
        Uri.parse('$baseUrl/stream/$matchId/webrtc/offer'),
        headers: {'Content-Type': 'application/json'},
        body: jsonEncode({'sdpOffer': offer.sdp}),
      );

      if (resp.statusCode != 200) {
        throw Exception('Offer rejected: ${resp.statusCode} ${resp.body}');
      }

      final data = jsonDecode(resp.body) as Map<String, dynamic>;
      final sdpAnswer = data['sdpAnswer'] as String;

      await _pc!.setRemoteDescription(RTCSessionDescription(sdpAnswer, 'answer'));
      debugPrint('[WebRTCPlayer] ✅ Connected match=$matchId');

    } catch (e) {
      debugPrint('[WebRTCPlayer] ❌ connect() failed: $e');
      _setError(e.toString());
    }
  }

  void _setState(WebRtcPlayerState s) {
    if (!_disposed) stateNotifier.value = s;
  }

  void _setError(String msg) {
    errorNotifier.value = msg;
    _setState(WebRtcPlayerState.error);
  }

  Future<void> dispose() async {
    _disposed = true;
    _setState(WebRtcPlayerState.disposed);
    try { await _pc?.close(); } catch (_) {}
    _pc = null;
    try { await renderer.dispose(); } catch (_) {}
    stateNotifier.dispose();
    errorNotifier.dispose();
  }
}
