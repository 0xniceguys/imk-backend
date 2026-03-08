import 'package:app_links/app_links.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'app.dart';
import 'core/runtime_client_config.dart';
import 'providers/auth_provider.dart';

void _log(String msg) {
  // ignore: avoid_print
  if (kDebugMode) print('[Main] $msg');
}

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();

  _log('=== APP COLD START ===');

  SystemChrome.setSystemUIOverlayStyle(
    const SystemUiOverlayStyle(
      statusBarColor: Colors.transparent,
      statusBarIconBrightness: Brightness.light,
      statusBarBrightness: Brightness.dark,
    ),
  );

  // Load runtime chain/contract/Privy config from backend.
  await RuntimeClientConfig.instance.bootstrap();

  final container = ProviderContainer();

  final appLinks = AppLinks();
  final initialUri = await appLinks.getInitialLink();
  final isDeepLinkStart = initialUri?.scheme == 'imk';
  _log('isDeepLinkStart=$isDeepLinkStart initialUri=$initialUri');

  appLinks.uriLinkStream.listen((uri) {
    _log(
      'Deep link received: scheme=${uri.scheme} host=${uri.host} '
      'path=${uri.path} query=${uri.queryParameters.keys.join(",")}',
    );
    if (uri.scheme == 'imk') {
      _log('Routing to WalletDeepLinkService.handleDeepLink');
      container.read(walletDeepLinkProvider).handleDeepLink(uri);
    } else {
      _log('Ignoring deep link with scheme: ${uri.scheme}');
    }
  }, onError: (e) => _log('Deep link stream error: $e'));

  if (initialUri != null && isDeepLinkStart) {
    _log('Dispatching cold-start deep link: $initialUri');
    container.read(walletDeepLinkProvider).handleDeepLink(initialUri);
  }

  runApp(
    UncontrolledProviderScope(
      container: container,
      child: ImmortalKombatApp(isDeepLinkStart: isDeepLinkStart),
    ),
  );
}
