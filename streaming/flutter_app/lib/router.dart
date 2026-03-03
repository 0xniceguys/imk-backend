enum ScreenSlug {
  getStarted,
  onboarding,
  arenaList,
  battleDetail,
  fighterOverview,
  profile,
  fighterDetails,
  liveMatch,
  postMatch,
}

enum NavTab { arena, fighters, profile }

const _routes = <String, ScreenSlug>{
  '/get-started': ScreenSlug.getStarted,
  '/onboarding': ScreenSlug.onboarding,
  '/onboarding-1': ScreenSlug.onboarding, // legacy alias
  '/arena-list': ScreenSlug.arenaList,
  '/battle-detail': ScreenSlug.battleDetail,
  '/fighter-overview': ScreenSlug.fighterOverview,
  '/profile': ScreenSlug.profile,
  '/fighter-details': ScreenSlug.fighterDetails,
  '/live-match': ScreenSlug.liveMatch,
  '/post-match': ScreenSlug.postMatch,
};

String routeFor(ScreenSlug slug) =>
    _routes.entries.firstWhere((e) => e.value == slug).key;

/// Parse route name, stripping any query-like suffix (e.g. '/battle-detail/1')
ScreenSlug slugFromRoute(String? name) {
  if (name == null) return ScreenSlug.getStarted;
  // Direct match first
  if (_routes.containsKey(name)) return _routes[name]!;
  // Try stripping last segment as an ID param
  final lastSlash = name.lastIndexOf('/');
  if (lastSlash > 0) {
    final base = name.substring(0, lastSlash);
    if (_routes.containsKey(base)) return _routes[base]!;
  }
  return ScreenSlug.getStarted;
}

/// Extract ID parameter from route path (e.g. '/battle-detail/mk4-1' → 'mk4-1')
String? idFromRoute(String? name) {
  if (name == null) return null;
  if (_routes.containsKey(name)) return null; // no param
  final lastSlash = name.lastIndexOf('/');
  if (lastSlash > 0) return name.substring(lastSlash + 1);
  return null;
}
