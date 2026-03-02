import 'dart:math' as math;
import 'package:flutter/material.dart';
import '../../core/palette.dart';

/// A branded loading spinner: a rotating gold arc with a trailing fade.
/// Drop-in replacement for CircularProgressIndicator throughout the app.
class IKLoader extends StatefulWidget {
  const IKLoader({super.key, this.size = 32, this.color = Palette.gold});
  final double size;
  final Color color;

  @override
  State<IKLoader> createState() => _IKLoaderState();
}

class _IKLoaderState extends State<IKLoader>
    with SingleTickerProviderStateMixin {
  late final AnimationController _ctrl;

  @override
  void initState() {
    super.initState();
    _ctrl = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 900),
    )..repeat();
  }

  @override
  void dispose() {
    _ctrl.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return SizedBox(
      width: widget.size,
      height: widget.size,
      child: AnimatedBuilder(
        animation: _ctrl,
        builder: (context, child) => CustomPaint(
          painter: _ArcPainter(
            progress: _ctrl.value,
            color: widget.color,
            strokeWidth: widget.size * 0.1,
          ),
        ),
      ),
    );
  }
}

class _ArcPainter extends CustomPainter {
  _ArcPainter({
    required this.progress,
    required this.color,
    required this.strokeWidth,
  });

  final double progress;
  final Color color;
  final double strokeWidth;

  @override
  void paint(Canvas canvas, Size size) {
    final center = Offset(size.width / 2, size.height / 2);
    final radius = (size.width - strokeWidth) / 2;

    // Trailing fade arc (ghost)
    final ghostPaint = Paint()
      ..color = color.withValues(alpha: 0.18)
      ..strokeWidth = strokeWidth
      ..style = PaintingStyle.stroke
      ..strokeCap = StrokeCap.round;
    canvas.drawCircle(center, radius, ghostPaint);

    // Sweeping arc — 270° sweep, rotates full circle
    final arcPaint = Paint()
      ..shader = SweepGradient(
        colors: [
          color.withValues(alpha: 0.0),
          color.withValues(alpha: 0.7),
          color,
        ],
        stops: const [0.0, 0.7, 1.0],
        startAngle: 0,
        endAngle: math.pi * 2,
        transform: GradientRotation(math.pi * 2 * progress),
      ).createShader(Rect.fromCircle(center: center, radius: radius))
      ..strokeWidth = strokeWidth
      ..style = PaintingStyle.stroke
      ..strokeCap = StrokeCap.round;

    const sweepAngle = math.pi * 1.5; // 270°
    final startAngle = math.pi * 2 * progress - math.pi / 2;
    canvas.drawArc(
      Rect.fromCircle(center: center, radius: radius),
      startAngle,
      sweepAngle,
      false,
      arcPaint,
    );

    // Bright leading dot
    final dotPaint = Paint()
      ..color = color
      ..style = PaintingStyle.fill;
    final dotAngle = startAngle + sweepAngle;
    final dotX = center.dx + radius * math.cos(dotAngle);
    final dotY = center.dy + radius * math.sin(dotAngle);
    canvas.drawCircle(Offset(dotX, dotY), strokeWidth / 2, dotPaint);
  }

  @override
  bool shouldRepaint(_ArcPainter old) =>
      old.progress != progress || old.color != color;
}
