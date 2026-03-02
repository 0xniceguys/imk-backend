import 'package:app_links/app_links.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'app.dart';
import 'providers/auth_provider.dart';

void _log(String msg) {
  // ignore: avoid_print
  if (kDebugMode) print('[Main] $msg');
}

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();

  // Load hasSeenIntro BEFORE runApp so the first frame is correct
  final prefs = await SharedPreferences.getInstance();
  final hasSeenIntro = prefs.getBool('hasSeenIntro') ?? false;
  _log('=== APP COLD START ===');
  _log('hasSeenIntro=$hasSeenIntro');
  SystemChrome.setSystemUIOverlayStyle(const SystemUiOverlayStyle(
    statusBarColor: Colors.transparent,
    statusBarIconBrightness: Brightness.light,
    statusBarBrightness: Brightness.dark,
  ));

  final container = ProviderContainer();

  // Check for a cold-start deep link BEFORE runApp so we pick the right
  // initial route (sign-in instead of get-started when returning from Phantom).
  final appLinks = AppLinks();
  final initialUri = await appLinks.getInitialLink();
  final isDeepLinkStart = initialUri?.scheme == 'imk';
  _log('isDeepLinkStart=$isDeepLinkStart initialUri=$initialUri');

  // Listen for incoming deep links (wallet connect callbacks)
  appLinks.uriLinkStream.listen(
    (uri) {
      _log('Deep link received: scheme=${uri.scheme} host=${uri.host} '
          'path=${uri.path} query=${uri.queryParameters.keys.join(",")}');
      if (uri.scheme == 'imk') {
        _log('Routing to WalletDeepLinkService.handleDeepLink');
        container.read(walletDeepLinkProvider).handleDeepLink(uri);
      } else {
        _log('Ignoring deep link with scheme: ${uri.scheme}');
      }
    },
    onError: (e) => _log('Deep link stream error: $e'),
  );

  // Dispatch the cold-start deep link now that the stream listener is set up
  if (initialUri != null && isDeepLinkStart) {
    _log('Dispatching cold-start deep link: $initialUri');
    container.read(walletDeepLinkProvider).handleDeepLink(initialUri);
  }

  runApp(
    UncontrolledProviderScope(
      container: container,
      child: ImmortalKombatApp(
        hasSeenIntro: hasSeenIntro,
        isDeepLinkStart: isDeepLinkStart,
      ),
    ),
  );
}
