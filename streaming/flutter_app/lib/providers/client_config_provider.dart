import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../models/client_config.dart';
import 'match_provider.dart';

final clientConfigProvider = FutureProvider<ClientConfig>((ref) async {
  final api = ref.read(apiServiceProvider);
  final json = await api.fetchClientConfig();
  if (json == null) return ClientConfig.fallback();
  return ClientConfig.fromJson(json);
});

final clientConfigValueProvider = Provider<ClientConfig>((ref) {
  return ref.watch(clientConfigProvider).valueOrNull ?? ClientConfig.fallback();
});
