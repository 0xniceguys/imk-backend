import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:cached_network_image/cached_network_image.dart';
import '../../core/constants.dart';
import '../../models/fighter.dart';
import '../../providers/fighter_image_cache_provider.dart';

class FighterImage extends ConsumerWidget {
  const FighterImage({
    super.key,
    required this.fighter,
    this.width,
    this.height,
    this.fit = BoxFit.contain,
    this.alignment = Alignment.center,
  });

  final Fighter fighter;
  final double? width;
  final double? height;
  final BoxFit fit;
  final Alignment alignment;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final url = fighter.resolvedImageUrl;
    if (url == null) {
      return Image.asset(
        Assets.photoUnavailable,
        width: width,
        height: height,
        fit: fit,
        alignment: alignment,
      );
    }
    return Image(
      image: CachedNetworkImageProvider(
        url,
        cacheManager: ref.watch(fighterImageCacheManagerProvider),
      ),
      width: width,
      height: height,
      fit: fit,
      alignment: alignment,
      errorBuilder: (_, error, stackTrace) => Image.asset(
        Assets.photoUnavailable,
        width: width,
        height: height,
        fit: fit,
        alignment: alignment,
      ),
    );
  }
}
