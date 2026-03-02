import 'package:flutter/material.dart';
import '../../core/palette.dart';

/// Skeleton shimmer placeholder for list items and cards.
/// Shows an animated dark-gold sweep to signal loading state.
class IKShimmer extends StatefulWidget {
  const IKShimmer({
    super.key,
    required this.width,
    required this.height,
    this.radius = 4,
  });

  final double width;
  final double height;
  final double radius;

  @override
  State<IKShimmer> createState() => _IKShimmerState();
}

class _IKShimmerState extends State<IKShimmer>
    with SingleTickerProviderStateMixin {
  late final AnimationController _ctrl;
  late final Animation<double> _anim;

  @override
  void initState() {
    super.initState();
    _ctrl = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 1400),
    )..repeat();
    _anim = CurvedAnimation(parent: _ctrl, curve: Curves.easeInOut);
  }

  @override
  void dispose() {
    _ctrl.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return AnimatedBuilder(
      animation: _anim,
      builder: (context, child) {
        return Container(
          width: widget.width,
          height: widget.height,
          decoration: BoxDecoration(
            borderRadius: BorderRadius.circular(widget.radius),
            gradient: LinearGradient(
              begin: Alignment.centerLeft,
              end: Alignment.centerRight,
              colors: const [
                Color(0xFF1A1A1A),
                Color(0xFF252520),
                Color(0xFF2E2A14),
                Color(0xFF252520),
                Color(0xFF1A1A1A),
              ],
              stops: [
                0.0,
                (_anim.value - 0.3).clamp(0.0, 1.0),
                _anim.value.clamp(0.0, 1.0),
                (_anim.value + 0.3).clamp(0.0, 1.0),
                1.0,
              ],
            ),
          ),
        );
      },
    );
  }
}

/// A pre-built arena card skeleton (matches ArenaCard proportions)
class ArenaCardSkeleton extends StatelessWidget {
  const ArenaCardSkeleton({super.key});

  @override
  Widget build(BuildContext context) {
    return Container(
      margin: const EdgeInsets.symmetric(horizontal: 16),
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: Palette.cardBg,
        borderRadius: BorderRadius.circular(8),
        border: Border.all(color: Palette.border),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              const IKShimmer(width: 52, height: 14, radius: 3),
              const SizedBox(width: 8),
              const IKShimmer(width: 80, height: 14, radius: 3),
              const Spacer(),
              const IKShimmer(width: 48, height: 14, radius: 3),
            ],
          ),
          const SizedBox(height: 16),
          Row(
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            children: [
              const IKShimmer(width: 90, height: 90, radius: 6),
              Column(
                children: [
                  const IKShimmer(width: 40, height: 20, radius: 4),
                  const SizedBox(height: 4),
                  const IKShimmer(width: 30, height: 12, radius: 3),
                ],
              ),
              const IKShimmer(width: 90, height: 90, radius: 6),
            ],
          ),
          const SizedBox(height: 12),
          const IKShimmer(width: double.infinity, height: 36, radius: 4),
        ],
      ),
    );
  }
}
