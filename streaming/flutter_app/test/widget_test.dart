import 'package:flutter_test/flutter_test.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'package:immortal_kombat/app.dart';

void main() {
  testWidgets('renders splash screen on cold start', (WidgetTester tester) async {
    await tester.pumpWidget(
      const ProviderScope(
        child: ImmortalKombatApp(
          hasSeenIntro: false,
          isDeepLinkStart: false,
        ),
      ),
    );

    await tester.pump(); // First frame

    // Splash screen shows the logo asset or tagline
    expect(find.text('LLMs fight. Humans bet.'), findsNothing); // not yet
    expect(find.text('IMMORTAL'), findsNothing);
  });
}
