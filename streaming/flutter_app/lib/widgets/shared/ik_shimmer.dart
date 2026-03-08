import 'package:flutter/material.dart';

/// Skeleton shimmer placeholder — animated dark-gold sweep, no rounded corners.
class IKShimmer extends StatefulWidget {
  const IKShimmer({
    super.key,
    required this.width,
    required this.height,
    this.radius = 0,
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
        final v = _anim.value;
        return Container(
          width: widget.width,
          height: widget.height,
          decoration: BoxDecoration(
            borderRadius: widget.radius > 0
                ? BorderRadius.circular(widget.radius)
                : null,
            gradient: LinearGradient(
              begin: Alignment.centerLeft,
              end: Alignment.centerRight,
              colors: const [
                Color(0xFF181818),
                Color(0xFF252520),
                Color(0xFF2C2818),
                Color(0xFF252520),
                Color(0xFF181818),
              ],
              stops: [
                0.0,
                (v - 0.3).clamp(0.0, 1.0),
                v.clamp(0.0, 1.0),
                (v + 0.3).clamp(0.0, 1.0),
                1.0,
              ],
            ),
          ),
        );
      },
    );
  }
}

/// Arena card skeleton — proportions match the real ArenaCard exactly.
class ArenaCardSkeleton extends StatelessWidget {
  const ArenaCardSkeleton({super.key});

  @override
  Widget build(BuildContext context) {
    return Column(
      mainAxisSize: MainAxisSize.min,
      children: [
        // Top gold divider line
        Container(height: 1, color: const Color(0xFF3A2E10)),
        // Status bar strip (matches ArenaCard header row, height 32)
        Container(
          height: 32,
          padding: const EdgeInsets.symmetric(horizontal: 12),
          color: const Color(0xFF14120A),
          child: Row(
            children: [
              const IKShimmer(width: 48, height: 14),
              const SizedBox(width: 10),
              const IKShimmer(width: 72, height: 10),
              const Spacer(),
              const IKShimmer(width: 90, height: 10),
            ],
          ),
        ),
        // Content row: fighter portraits + stats
        Padding(
          padding: const EdgeInsets.fromLTRB(10, 10, 10, 8),
          child: Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              // Two fighter portrait blocks side-by-side
              SizedBox(
                width: 140,
                height: 98,
                child: Row(
                  children: const [
                    Expanded(child: IKShimmer(width: double.infinity, height: 98)),
                    SizedBox(width: 2),
                    Expanded(child: IKShimmer(width: double.infinity, height: 98)),
                  ],
                ),
              ),
              const SizedBox(width: 10),
              // Right-side stat cells
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: const [
                    IKShimmer(width: 80, height: 11),
                    SizedBox(height: 4),
                    IKShimmer(width: 110, height: 16),
                    SizedBox(height: 12),
                    Row(
                      children: [
                        Expanded(
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              IKShimmer(width: 60, height: 11),
                              SizedBox(height: 4),
                              IKShimmer(width: 40, height: 14),
                            ],
                          ),
                        ),
                        SizedBox(width: 10),
                        Expanded(
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              IKShimmer(width: 50, height: 11),
                              SizedBox(height: 4),
                              IKShimmer(width: 30, height: 14),
                            ],
                          ),
                        ),
                      ],
                    ),
                  ],
                ),
              ),
            ],
          ),
        ),
        // Bottom divider
        Container(height: 1, color: const Color(0xFF3A2E10)),
        const SizedBox(height: 10),
      ],
    );
  }
}
