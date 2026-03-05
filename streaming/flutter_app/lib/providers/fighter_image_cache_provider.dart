import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_cache_manager/flutter_cache_manager.dart';
import '../models/fighter.dart';

class FighterImageCacheService {
  FighterImageCacheService(this.cacheManager);

  final CacheManager cacheManager;

  Future<void> prefetchFighters(Iterable<Fighter> fighters) async {
    final urls = fighters
        .map((f) => f.resolvedImageUrl)
        .whereType<String>()
        .toSet();
    for (final url in urls) {
      try {
        await cacheManager.downloadFile(url, key: url);
      } catch (_) {
        // Best-effort prefetch; ignore individual failures.
      }
    }
  }
}

final fighterImageCacheManagerProvider = Provider<CacheManager>((ref) {
  final manager = CacheManager(
    Config(
      'fighter_images_cache',
      stalePeriod: const Duration(days: 14),
      maxNrOfCacheObjects: 400,
    ),
  );
  ref.onDispose(manager.dispose);
  return manager;
});

final fighterImageCacheServiceProvider = Provider<FighterImageCacheService>((ref) {
  return FighterImageCacheService(ref.watch(fighterImageCacheManagerProvider));
});
