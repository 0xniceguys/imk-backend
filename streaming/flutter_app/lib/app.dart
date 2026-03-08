import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'core/palette.dart';
import 'core/constants.dart';
import 'core/typography.dart';
import 'router.dart';
import 'providers/auth_provider.dart';
import 'providers/match_stream_provider.dart';
import 'providers/wallet_provider.dart';
import 'screens/splash_screen.dart';
import 'screens/get_started_screen.dart';
import 'screens/onboarding_screen.dart';
import 'screens/arena_list_screen.dart';
import 'screens/battle_detail_screen.dart';
import 'screens/fighter_overview_screen.dart';
import 'screens/fighter_details_screen.dart';
import 'screens/fighter_match_history_screen.dart';
import 'screens/profile_screen.dart';
import 'screens/live_match_screen.dart';
import 'screens/post_match_screen.dart';

// Auth-flow routes that should replace the nav stack (no back button)
const _authRoutes = {'/get-started', '/onboarding'};

// Tab-level routes: replace instead of push so back doesn't cycle tabs
const _tabRoutes = {'/arena-list', '/fighter-overview', '/profile'};

// Routes that use right-slide (iOS detail) transition
const _slideRightRoutes = {'/fighter-details', '/fighter-match-history'};

// Routes that get a zoom-scale entrance (feels cinematic)
const _zoomRoutes = {'/live-match'};

class ImmortalKombatApp extends ConsumerStatefulWidget {
  const ImmortalKombatApp({super.key, required this.isDeepLinkStart});
  final bool isDeepLinkStart;

  @override
  ConsumerState<ImmortalKombatApp> createState() => _ImmortalKombatAppState();
}

class _ImmortalKombatAppState extends ConsumerState<ImmortalKombatApp> {
  final _navKey = GlobalKey<NavigatorState>();
  late final String _postSplashRoute;

  @override
  void initState() {
    super.initState();
    // Compute once — never re-evaluate on rebuild to avoid GET_STARTED
    // flashing mid-auth when build() re-runs due to ref.watch(authProvider).
    _postSplashRoute = '/get-started';
    debugPrint('[App] initState: _postSplashRoute=$_postSplashRoute');
  }

  @override
  Widget build(BuildContext context) {
    // Watch auth to rebuild on changes; navigate reactively via listener
    ref.watch(authProvider);
    // Keep the global HLS preloader alive at all times so the video controller
    // starts initialising the moment backend signals streaming_state=ready.
    ref.watch(globalHlsPreloaderProvider);

    ref.listen<AuthState>(authProvider, (prev, next) {
      debugPrint('[App] AUTH: ${prev?.status} → ${next.status}');
      final nav = _navKey.currentState;
      if (nav == null) {
        debugPrint('[App] AUTH: nav is null, skipping navigation');
        return;
      }

      // Fresh login completed (any method) → onboarding
      if (next.status == AuthStatus.authenticated &&
          prev?.status == AuthStatus.authenticating) {
        ref.read(walletProvider.notifier).loadWallet();
        nav.pushNamedAndRemoveUntil('/onboarding', (_) => false);
        return;
      }

      // Initial auth resolution is now handled by _SplashPage itself,
      // so we only handle mid-session transitions here.
      debugPrint('[App] AUTH: no navigation triggered for this transition');
    });

    return MaterialApp(
      navigatorKey: _navKey,
      debugShowCheckedModeBanner: false,
      title: 'Immortal Kombat',
      theme: ThemeData(
        brightness: Brightness.dark,
        fontFamily: kAppFontFamily,
        scaffoldBackgroundColor: Palette.black,
        colorScheme: const ColorScheme.dark(
          primary: Palette.gold,
          secondary: Palette.gold,
          surface: Palette.sheetBg,
          error: Palette.red,
        ),
        splashColor: Palette.gold.withValues(alpha: 0.1),
        highlightColor: Palette.gold.withValues(alpha: 0.05),
        textSelectionTheme: const TextSelectionThemeData(
          cursorColor: Palette.gold,
          selectionColor: Palette.darkGold,
          selectionHandleColor: Palette.gold,
        ),
      ),
      // Always start at splash — it routes onward once animation completes
      initialRoute: '/splash',
      onGenerateRoute: (settings) {
        // ── Splash ────────────────────────────────────────────────────────
        if (settings.name == '/splash') {
          return _FadeRoute(
            page: SplashPage(postRoute: _postSplashRoute),
            settings: settings,
          );
        }

        final slug = slugFromRoute(settings.name);
        final id = idFromRoute(settings.name);
        final page = _ScreenPage(slug: slug, paramId: id);

        // Auth-flow screens: fade+slide, replace entire stack
        if (_authRoutes.contains(settings.name)) {
          return _FadeSlideRoute(page: page, settings: settings);
        }

        // Tab-level nav: pure crossfade
        if (_tabRoutes.contains(settings.name)) {
          return _FadeRoute(page: page, settings: settings);
        }

        // Fighter details: slide from right (iOS push feel)
        final basePath = settings.name?.split('/').take(2).join('/') ?? '';
        if (_slideRightRoutes.contains(basePath)) {
          return _SlideRightRoute(page: page, settings: settings);
        }

        // Live match: cinematic zoom-in entrance
        if (_zoomRoutes.contains(basePath)) {
          return _ZoomFadeRoute(page: page, settings: settings);
        }

        // Default: fade + slide up
        return _FadeSlideRoute(page: page, settings: settings);
      },
    );
  }
}

// ──────────────────────────────────────────────────────────────────────────────
// Page route builders
// ──────────────────────────────────────────────────────────────────────────────

/// Fade + gentle slide-up (default for detail screens & auth)
class _FadeSlideRoute extends PageRouteBuilder<void> {
  _FadeSlideRoute({required Widget page, required RouteSettings settings})
    : super(
        settings: settings,
        transitionDuration: const Duration(milliseconds: 380),
        reverseTransitionDuration: const Duration(milliseconds: 280),
        pageBuilder: (context, animation, secondaryAnimation) => page,
        transitionsBuilder: (context, animation, secondaryAnimation, child) {
          final fade = CurvedAnimation(
            parent: animation,
            curve: Curves.easeOutExpo,
          );
          final slide =
              Tween<Offset>(
                begin: const Offset(0, 0.05),
                end: Offset.zero,
              ).animate(
                CurvedAnimation(parent: animation, curve: Curves.easeOutExpo),
              );
          return FadeTransition(
            opacity: fade,
            child: SlideTransition(position: slide, child: child),
          );
        },
      );
}

/// Pure crossfade (tab switching)
class _FadeRoute extends PageRouteBuilder<void> {
  _FadeRoute({required Widget page, required RouteSettings settings})
    : super(
        settings: settings,
        transitionDuration: const Duration(milliseconds: 250),
        reverseTransitionDuration: const Duration(milliseconds: 180),
        pageBuilder: (context, animation, secondaryAnimation) => page,
        transitionsBuilder: (context, animation, secondaryAnimation, child) {
          return FadeTransition(
            opacity: CurvedAnimation(parent: animation, curve: Curves.easeOut),
            child: child,
          );
        },
      );
}

/// Slide from right — iOS push feel for fighter details
class _SlideRightRoute extends PageRouteBuilder<void> {
  _SlideRightRoute({required Widget page, required RouteSettings settings})
    : super(
        settings: settings,
        transitionDuration: const Duration(milliseconds: 360),
        reverseTransitionDuration: const Duration(milliseconds: 280),
        pageBuilder: (context, animation, secondaryAnimation) => page,
        transitionsBuilder: (context, animation, secondaryAnimation, child) {
          final slide =
              Tween<Offset>(
                begin: const Offset(1.0, 0),
                end: Offset.zero,
              ).animate(
                CurvedAnimation(parent: animation, curve: Curves.easeOutCubic),
              );
          final fade = CurvedAnimation(
            parent: animation,
            curve: const Interval(0, 0.4, curve: Curves.easeOut),
          );
          return FadeTransition(
            opacity: fade,
            child: SlideTransition(position: slide, child: child),
          );
        },
      );
}

/// Zoom + fade — cinematic entrance for live match screen
class _ZoomFadeRoute extends PageRouteBuilder<void> {
  _ZoomFadeRoute({required Widget page, required RouteSettings settings})
    : super(
        settings: settings,
        transitionDuration: const Duration(milliseconds: 450),
        reverseTransitionDuration: const Duration(milliseconds: 300),
        pageBuilder: (context, animation, secondaryAnimation) => page,
        transitionsBuilder: (context, animation, secondaryAnimation, child) {
          final fade = CurvedAnimation(
            parent: animation,
            curve: Curves.easeOut,
          );
          final scale = Tween<double>(begin: 0.93, end: 1.0).animate(
            CurvedAnimation(parent: animation, curve: Curves.easeOutCubic),
          );
          return FadeTransition(
            opacity: fade,
            child: ScaleTransition(scale: scale, child: child),
          );
        },
      );
}

// ──────────────────────────────────────────────────────────────────────────────
// Screen dispatcher
// ──────────────────────────────────────────────────────────────────────────────
class _ScreenPage extends StatelessWidget {
  const _ScreenPage({required this.slug, this.paramId});
  final ScreenSlug slug;
  final String? paramId;

  void _navigate(BuildContext context, String route) {
    if (_authRoutes.contains(route) || _tabRoutes.contains(route)) {
      // Auth/tab routes: clear entire stack
      Navigator.of(context).pushNamedAndRemoveUntil(route, (_) => false);
    } else if (route.startsWith('/post-match')) {
      // Post-match replaces live-match in the stack so back goes to arena-list,
      // not back into the dead live-match screen.
      Navigator.of(context).pushReplacementNamed(route);
    } else {
      Navigator.of(context).pushNamed(route);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: Palette.black,
      body: Center(
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: kMaxWidth),
          child: _screen(context),
        ),
      ),
    );
  }

  Widget _screen(BuildContext context) {
    onNav(String route) => _navigate(context, route);

    return switch (slug) {
      ScreenSlug.getStarted => GetStartedScreen(onNavigate: onNav),
      ScreenSlug.onboarding => OnboardingScreen(onNavigate: onNav),
      ScreenSlug.arenaList => ArenaListScreen(onNavigate: onNav),
      ScreenSlug.battleDetail => BattleDetailScreen(
        onNavigate: onNav,
        matchId: paramId,
      ),
      ScreenSlug.fighterOverview => FighterOverviewScreen(onNavigate: onNav),
      ScreenSlug.fighterDetails => FighterDetailsScreen(
        onNavigate: onNav,
        fighterId: paramId,
      ),
      ScreenSlug.fighterMatchHistory => FighterMatchHistoryScreen(
        onNavigate: onNav,
        fighterId: paramId,
      ),
      ScreenSlug.profile => ProfileScreen(onNavigate: onNav),
      ScreenSlug.liveMatch => LiveMatchScreen(
        onNavigate: onNav,
        matchId: paramId,
      ),
      ScreenSlug.postMatch => PostMatchScreen(
        onNavigate: onNav,
        matchId: paramId,
      ),
    };
  }
}
