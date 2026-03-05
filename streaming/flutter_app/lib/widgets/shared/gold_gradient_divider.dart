import 'package:flutter/material.dart';

class GoldGradientDivider extends StatelessWidget {
  const GoldGradientDivider({
    super.key,
    this.height = 1,
    this.width = double.infinity,
    this.margin = EdgeInsets.zero,
    this.peakColor = const Color(0xFFFFC500),
  });

  final double height;
  final double width;
  final EdgeInsetsGeometry margin;
  final Color peakColor;

  @override
  Widget build(BuildContext context) {
    return Container(
      height: height,
      width: width,
      margin: margin,
      decoration: BoxDecoration(
        gradient: LinearGradient(
          colors: [Colors.transparent, peakColor, Colors.transparent],
        ),
      ),
    );
  }
}
