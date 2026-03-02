import 'package:flutter/material.dart';
import 'palette.dart';

TextStyle displayStyle({
  double size = 24,
  Color color = Palette.white,
  FontWeight weight = FontWeight.w400,
  double? height,
  double? letterSpacing,
  TextDecoration decoration = TextDecoration.none,
}) =>
    TextStyle(
      fontFamily: 'serif',
      fontSize: size,
      fontWeight: weight,
      color: color,
      height: height,
      letterSpacing: letterSpacing,
      decoration: decoration,
    );

TextStyle bodyStyle({
  double size = 16,
  Color color = Palette.white,
  FontWeight weight = FontWeight.w400,
  double? height,
  double? letterSpacing,
  TextDecoration decoration = TextDecoration.none,
}) =>
    TextStyle(
      fontSize: size,
      fontWeight: weight,
      color: color,
      height: height,
      letterSpacing: letterSpacing,
      decoration: decoration,
    );

ButtonStyle plainBtn({EdgeInsetsGeometry padding = EdgeInsets.zero}) =>
    TextButton.styleFrom(
      padding: padding,
      minimumSize: Size.zero,
      tapTargetSize: MaterialTapTargetSize.shrinkWrap,
      splashFactory: NoSplash.splashFactory,
      overlayColor: Colors.transparent,
    );
