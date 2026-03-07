import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../services/global_events_service.dart';

/// Singleton global events service that listens for ALL match status changes.
final globalEventsServiceProvider = Provider<GlobalEventsService>((ref) {
  final service = GlobalEventsService();
  service.connect(); // Auto-connect on creation
  ref.onDispose(service.dispose);
  return service;
});

/// Stream of match status change events from any match.
final matchStatusEventsProvider = StreamProvider<Map<String, dynamic>>((ref) {
  final service = ref.watch(globalEventsServiceProvider);
  return service.matchStatusStream;
});