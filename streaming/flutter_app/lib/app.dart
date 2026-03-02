import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'core/palette.dart';
import 'core/constants.dart';
import 'router.dart';
import 'providers/auth_provider.dart';
import 'providers/wallet_provider.dart';
import 'screens/get_started_screen.dart';
import 'screens/sign_in_screen.dart';
import 'screens/onboarding_screen.dart';
import 'screens/arena_list_screen.dart';
import 'screens/battle_detail_screen.dart';
import 'screens/fighter_overview_screen.dart';
import 'screens/fighter_details_screen.dart';
import 'screens/profile_screen.dart';
import 'screens/live_match_screen.dart';
import 'screens/post_match_screen.dart';

// Auth-flow routes that should replace the nav stack (no back button)
const _authRoutes = {'/get-started', '/sign-in-modal', '/onboarding'};

// Tab-level routes: replace instead of push so back doesn't cycle tabs
const _tabRoutes = {'/arena-list', '/fighter-overview', '/profile'};

// Routes that use right-slide (iOS detail) transition
const _slideRightRoutes = {'/fighter-details'};

// Routes that get a zoom-scale entrance (feels cinematic)
const _zoomRoutes = {'/live-match'};

class ImmortalKombatApp extends ConsumerStatefulWidget {
  const ImmortalKombatApp({
    super.key,
    required this.hasSeenIntro,
    required this.isDeepLinkStart,
  });
  final bool hasSeenIntro;
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
    _postSplashRoute = (widget.hasSeenIntro || widget.isDeepLinkStart)
        ? '/sign-in-modal'
        : '/get-started';
    debugPrint('[App] initState: _postSplashRoute=$_postSplashRoute');
  }

  @override
  Widget build(BuildContext context) {
    // Watch auth to rebuild on changes; navigate reactively via listener
    ref.watch(authProvider);

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

      // Returning session restored → arena
      if (next.status == AuthStatus.authenticated &&
          prev?.status == AuthStatus.unknown) {
        ref.read(walletProvider.notifier).loadWallet();
        nav.pushNamedAndRemoveUntil('/arena-list', (_) => false);
        return;
      }

      // Init completed, not logged in → sign-in (always, never get-started)
      if (prev?.status == AuthStatus.unknown &&
          next.status == AuthStatus.unauthenticated) {
        nav.pushNamedAndRemoveUntil('/sign-in-modal', (_) => false);
        return;
      }

      debugPrint('[App] AUTH: no navigation triggered for this transition');
    });

    return MaterialApp(
      navigatorKey: _navKey,
      debugShowCheckedModeBanner: false,
      title: 'Immortal Kombat',
      theme: ThemeData(
        brightness: Brightness.dark,
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
            page: _SplashPage(postRoute: _postSplashRoute),
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
// Splash page (wraps the animated splash content)
// ──────────────────────────────────────────────────────────────────────────────
class _SplashPage extends StatefulWidget {
  const _SplashPage({required this.postRoute});
  final String postRoute;

  @override
  State<_SplashPage> createState() => _SplashPageState();
}

class _SplashPageState extends State<_SplashPage>
    with TickerProviderStateMixin {
  late final AnimationController _logoCtrl;
  late final Animation<double> _logoFade;
  late final Animation<double> _logoScale;
  late final AnimationController _shimmerCtrl;
  late final Animation<double> _shimmerAnim;
  late final AnimationController _subtitleCtrl;
  late final Animation<double> _subtitleFade;
  late final Animation<double> _subtitleSlide;
  late final AnimationController _glowCtrl;
  late final Animation<double> _glowFade;

  @override
  void initState() {
    super.initState();
    _logoCtrl = AnimationController(
        vsync: this, duration: const Duration(milliseconds: 700));
    _logoFade = CurvedAnimation(parent: _logoCtrl, curve: Curves.easeOut);
    _logoScale = Tween<double>(begin: 0.72, end: 1.0).animate(
        CurvedAnimation(parent: _logoCtrl, curve: Curves.easeOutBack));

    _shimmerCtrl = AnimationController(
        vsync: this, duration: const Duration(milliseconds: 900));
    _shimmerAnim =
        CurvedAnimation(parent: _shimmerCtrl, curve: Curves.easeInOut);

    _subtitleCtrl = AnimationController(
        vsync: this, duration: const Duration(milliseconds: 600));
    _subtitleFade =
        CurvedAnimation(parent: _subtitleCtrl, curve: Curves.easeOut);
    _subtitleSlide = Tween<double>(begin: 20, end: 0).animate(
        CurvedAnimation(parent: _subtitleCtrl, curve: Curves.easeOutCubic));

    _glowCtrl = AnimationController(
        vsync: this, duration: const Duration(milliseconds: 500));
    _glowFade = CurvedAnimation(parent: _glowCtrl, curve: Curves.easeInOut);

    _run();
  }

  Future<void> _run() async {
    await _logoCtrl.forward();
    _shimmerCtrl.forward();
    await Future.delayed(const Duration(milliseconds: 200));
    _subtitleCtrl.forward();
    await Future.delayed(const Duration(milliseconds: 500));
    await _glowCtrl.forward();
    await Future.delayed(const Duration(milliseconds: 250));
    if (mounted) {
      Navigator.of(context)
          .pushNamedAndRemoveUntil(widget.postRoute, (_) => false);
    }
  }

  @override
  void dispose() {
    _logoCtrl.dispose();
    _shimmerCtrl.dispose();
    _subtitleCtrl.dispose();
    _glowCtrl.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: Palette.black,
      body: AnimatedBuilder(
        animation: Listenable.merge(
            [_logoCtrl, _shimmerCtrl, _subtitleCtrl, _glowCtrl]),
        builder: (ctx, child) => Container(
          color: Palette.black,
          child: Stack(
            fit: StackFit.expand,
            children: [
              // Radial ambient glow
              Positioned.fill(
                child: Opacity(
                  opacity: _logoFade.value * 0.35,
                  child: const DecoratedBox(
                    decoration: BoxDecoration(
                      gradient: RadialGradient(
                        center: Alignment.center,
                        radius: 0.6,
                        colors: [Color(0x2EFFC500), Colors.transparent],
                      ),
                    ),
                  ),
                ),
              ),
              // Exit glow burst
              if (_glowFade.value > 0)
                Positioned.fill(
                  child: Opacity(
                    opacity: _glowFade.value * 0.12,
                    child: DecoratedBox(
                      decoration: BoxDecoration(
                        gradient: RadialGradient(
                          center: Alignment.center,
                          radius: 0.9,
                          colors: [
                            Palette.gold
                                .withValues(alpha: _glowFade.value * 0.5),
                            Colors.transparent,
                          ],
                        ),
                      ),
                    ),
                  ),
                ),
              Center(
                child: Column(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    // Logo: scale bounce + fade
                    Opacity(
                      opacity: _logoFade.value,
                      child: Transform.scale(
                        scale: _logoScale.value,
                        child: _buildShimmerLogo(),
                      ),
                    ),
                    const SizedBox(height: 24),
                    // Tagline: slide up + fade
                    Transform.translate(
                      offset: Offset(0, _subtitleSlide.value),
                      child: Opacity(
                        opacity: _subtitleFade.value,
                        child: Column(
                          children: [
                            Container(
                              height: 1,
                              width: 120,
                              margin: const EdgeInsets.only(bottom: 12),
                              decoration: const BoxDecoration(
                                gradient: LinearGradient(colors: [
                                  Colors.transparent,
                                  Color(0x99FFC500),
                                  Colors.transparent,
                                ]),
                              ),
                            ),
                            const Text(
                              'LLMs fight. Humans bet.',
                              style: TextStyle(
                                color: Palette.muted,
                                fontSize: 14,
                                letterSpacing: 1.5,
                              ),
                            ),
                          ],
                        ),
                      ),
                    ),
                  ],
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }

  Widget _buildShimmerLogo() {
    final p = _shimmerAnim.value;
    return ShaderMask(
      blendMode: BlendMode.srcATop,
      shaderCallback: (bounds) => LinearGradient(
        colors: const [
          Colors.transparent,
          Color(0xFFFFE066),
          Colors.transparent,
        ],
        stops: [
          (p - 0.25).clamp(0.0, 1.0),
          p.clamp(0.0, 1.0),
          (p + 0.25).clamp(0.0, 1.0),
        ],
        begin: Alignment.centerLeft,
        end: Alignment.centerRight,
      ).createShader(bounds),
      child: Image.asset(Assets.logoVector, width: 200),
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
            final slide = Tween<Offset>(
              begin: const Offset(0, 0.05),
              end: Offset.zero,
            ).animate(CurvedAnimation(
              parent: animation,
              curve: Curves.easeOutExpo,
            ));
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
              opacity:
                  CurvedAnimation(parent: animation, curve: Curves.easeOut),
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
            final slide = Tween<Offset>(
              begin: const Offset(1.0, 0),
              end: Offset.zero,
            ).animate(CurvedAnimation(
              parent: animation,
              curve: Curves.easeOutCubic,
            ));
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
              CurvedAnimation(
                parent: animation,
                curve: Curves.easeOutCubic,
              ),
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
      Navigator.of(context).pushNamedAndRemoveUntil(route, (_) => false);
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
      ScreenSlug.signInModal => SignInScreen(onNavigate: onNav),
      ScreenSlug.onboarding => OnboardingScreen(onNavigate: onNav),
      ScreenSlug.arenaList => ArenaListScreen(onNavigate: onNav),
      ScreenSlug.battleDetail =>
        BattleDetailScreen(onNavigate: onNav, matchId: paramId),
      ScreenSlug.fighterOverview =>
        FighterOverviewScreen(onNavigate: onNav),
      ScreenSlug.fighterDetails =>
        FighterDetailsScreen(onNavigate: onNav, fighterId: paramId),
      ScreenSlug.profile => ProfileScreen(onNavigate: onNav),
      ScreenSlug.liveMatch =>
        LiveMatchScreen(onNavigate: onNav, matchId: paramId),
      ScreenSlug.postMatch =>
        PostMatchScreen(onNavigate: onNav, matchId: paramId),
    };
  }
}
