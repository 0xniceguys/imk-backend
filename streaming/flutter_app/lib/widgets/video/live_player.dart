import 'package:flutter/material.dart';
import 'package:video_player/video_player.dart';
import '../../core/palette.dart';

class LivePlayer extends StatefulWidget {
  const LivePlayer({super.key, required this.streamUrl});

  final String streamUrl;

  @override
  State<LivePlayer> createState() => _LivePlayerState();
}

class _LivePlayerState extends State<LivePlayer> {
  late VideoPlayerController _controller;
  bool _initialized = false;
  bool _hasError = false;

  @override
  void initState() {
    super.initState();
    _initPlayer();
  }

  Future<void> _initPlayer() async {
    _controller = VideoPlayerController.networkUrl(
      Uri.parse(widget.streamUrl),
    );
    try {
      await _controller.initialize();
      _controller.play();
      if (mounted) setState(() => _initialized = true);
    } catch (_) {
      if (mounted) setState(() => _hasError = true);
    }
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    if (_hasError) {
      return AspectRatio(
        aspectRatio: 4 / 3,
        child: Container(
          color: Palette.black,
          child: Center(
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                const Icon(Icons.error_outline,
                    color: Palette.muted, size: 40),
                const SizedBox(height: 8),
                Text('Stream unavailable',
                    style: TextStyle(color: Palette.muted, fontSize: 14)),
              ],
            ),
          ),
        ),
      );
    }

    if (!_initialized) {
      return const AspectRatio(
        aspectRatio: 4 / 3,
        child: ColoredBox(
          color: Palette.black,
          child: Center(
            child: CircularProgressIndicator(color: Palette.gold),
          ),
        ),
      );
    }

    return AspectRatio(
      aspectRatio: _controller.value.aspectRatio,
      child: GestureDetector(
        onTap: () {
          if (_controller.value.isPlaying) {
            _controller.pause();
          } else {
            _controller.play();
          }
          setState(() {});
        },
        child: Stack(
          alignment: Alignment.center,
          children: [
            VideoPlayer(_controller),
            if (!_controller.value.isPlaying)
              const Icon(Icons.play_circle_fill,
                  color: Palette.gold, size: 56),
          ],
        ),
      ),
    );
  }
}
