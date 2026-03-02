import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:integration_test/integration_test.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:immortal_kombat/app.dart';
import 'package:immortal_kombat/providers/auth_provider.dart';
import 'package:immortal_kombat/services/privy_service.dart';

/// Integration tests for the Immortal Kombat app UI flow.
///
/// These tests bypass Privy auth (which requires real email/OAuth)
/// and test the post-auth UI flow: navigation, screens, wallet, betting.
///
/// Run on a connected device:
///   flutter test integration_test/app_flow_test.dart
void main() {
  IntegrationTestWidgetsFlutterBinding.ensureInitialized();

  group('Get Started & Onboarding Flow', () {
    testWidgets('Get Started screen renders and navigates to sign-in',
        (tester) async {
      await tester.pumpWidget(
        const ProviderScope(
          child: ImmortalKombatApp(
            hasSeenIntro: false,
            isDeepLinkStart: false,
          ),
        ),
      );
      await tester.pumpAndSettle();

      // Splash animates then routes to get-started (hasSeenIntro=false)
      expect(find.text('Get started'), findsOneWidget);

      // Tap Get Started
      await tester.tap(find.text('Get started'));
      await tester.pumpAndSettle();

      // Should navigate to sign-in modal
      expect(find.text('Log in or sign up'), findsOneWidget);
    });

    testWidgets('Sign-in screen shows all login options', (tester) async {
      await tester.pumpWidget(
        const ProviderScope(
          child: ImmortalKombatApp(
            hasSeenIntro: false,
            isDeepLinkStart: false,
          ),
        ),
      );
      await tester.pumpAndSettle();

      // Navigate to sign-in
      await tester.tap(find.text('Get started'));
      await tester.pumpAndSettle();

      // Verify all login options present
      expect(find.text('Google'), findsOneWidget);
      expect(find.text('Apple'), findsOneWidget);
      expect(find.text('or'), findsOneWidget);
      expect(find.text('Protected by privy'), findsOneWidget);

      // Verify email input exists
      expect(find.byType(TextField), findsOneWidget);
    });
  });

  group('Post-auth Navigation', () {
    testWidgets('Onboarding swipe and navigation works', (tester) async {
      await tester.pumpWidget(
        const ProviderScope(
          child: ImmortalKombatApp(
            hasSeenIntro: false,
            isDeepLinkStart: false,
          ),
        ),
      );
      await tester.pumpAndSettle();

      // Get to sign-in, then use "Continue with wallet" to skip to onboarding
      await tester.tap(find.text('Get started'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Continue with a wallet'));
      await tester.pumpAndSettle();

      // Should be on onboarding page 1
      expect(find.text('WHAT IS IMMORTAL KOMBAT?'), findsOneWidget);
      expect(find.text('Continue'), findsOneWidget);

      // Tap Continue to go to page 2
      await tester.tap(find.text('Continue'));
      await tester.pumpAndSettle();
      expect(find.text('ERA OF UNFIXABLE ESPORTS.'), findsOneWidget);

      // Tap Continue to go to page 3
      await tester.tap(find.text('Continue'));
      await tester.pumpAndSettle();
      expect(find.text('PURE FATALITY CHAOS.'), findsOneWidget);
      expect(find.text('Take my money'), findsOneWidget);
    });

    testWidgets('Skip button on onboarding works', (tester) async {
      await tester.pumpWidget(
        const ProviderScope(
          child: ImmortalKombatApp(
            hasSeenIntro: false,
            isDeepLinkStart: false,
          ),
        ),
      );
      await tester.pumpAndSettle();

      await tester.tap(find.text('Get started'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Continue with a wallet'));
      await tester.pumpAndSettle();

      // Tap Skip
      await tester.tap(find.text('Skip'));
      await tester.pumpAndSettle();

      // Should be on arena list (look for tab navigation)
      expect(find.text('LIVE'), findsWidgets);
    });
  });
}


      // Tap Get Started
      await tester.tap(find.text('Get started'));
      await tester.pumpAndSettle();

      // Should navigate to sign-in modal
      expect(find.text('Log in or sign up'), findsOneWidget);
    });

    testWidgets('Sign-in screen shows all login options', (tester) async {
      await tester.pumpWidget(
        const ProviderScope(child: ImmortalKombatApp()),
      );
      await tester.pumpAndSettle();

      // Navigate to sign-in
      await tester.tap(find.text('Get started'));
      await tester.pumpAndSettle();

      // Verify all login options present
      expect(find.text('Google'), findsOneWidget);
      expect(find.text('Apple'), findsOneWidget);
      expect(find.text('Continue with a wallet'), findsOneWidget);
      expect(find.text('or'), findsOneWidget);
      expect(find.text('Protected by privy'), findsOneWidget);

      // Verify email input exists
      expect(find.byType(TextField), findsOneWidget);
    });
  });

  group('Post-auth Navigation', () {
    testWidgets('Onboarding swipe and navigation works', (tester) async {
      await tester.pumpWidget(
        const ProviderScope(child: ImmortalKombatApp()),
      );
      await tester.pumpAndSettle();

      // Get to sign-in, then use "Continue with wallet" to skip to onboarding
      await tester.tap(find.text('Get started'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Continue with a wallet'));
      await tester.pumpAndSettle();

      // Should be on onboarding page 1
      expect(find.text('WHAT IS IMMORTAL KOMBAT?'), findsOneWidget);
      expect(find.text('Continue'), findsOneWidget);

      // Tap Continue to go to page 2
      await tester.tap(find.text('Continue'));
      await tester.pumpAndSettle();
      expect(find.text('ERA OF UNFIXABLE ESPORTS.'), findsOneWidget);

      // Tap Continue to go to page 3
      await tester.tap(find.text('Continue'));
      await tester.pumpAndSettle();
      expect(find.text('PURE FATALITY CHAOS.'), findsOneWidget);
      expect(find.text('Take my money'), findsOneWidget);
    });

    testWidgets('Skip button on onboarding works', (tester) async {
      await tester.pumpWidget(
        const ProviderScope(child: ImmortalKombatApp()),
      );
      await tester.pumpAndSettle();

      await tester.tap(find.text('Get started'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Continue with a wallet'));
      await tester.pumpAndSettle();

      // Tap Skip
      await tester.tap(find.text('Skip'));
      await tester.pumpAndSettle();

      // Should be on arena list (look for tab navigation)
      expect(find.text('LIVE'), findsWidgets);
    });
  });
}
