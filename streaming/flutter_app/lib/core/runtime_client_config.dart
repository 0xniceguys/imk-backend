import 'dart:convert';
import 'dart:math' as math;

import 'package:flutter/foundation.dart';
import 'package:http/http.dart' as http;

import '../models/client_config.dart';
import 'constants.dart';

void _log(String msg) {
  // ignore: avoid_print
  if (kDebugMode) print('[ClientConfig] $msg');
}

class RuntimeClientConfig {
  RuntimeClientConfig._();
  static final RuntimeClientConfig instance = RuntimeClientConfig._();

  ClientConfig? _config;
  bool _attemptedBootstrap = false;

  bool get isLoaded => _config != null;
  bool get attemptedBootstrap => _attemptedBootstrap;

  String get cluster {
    final c = _config?.network.cluster;
    if (c == 'devnet' || c == 'testnet' || c == 'mainnet') return c!;
    return kFallbackUseDevnet ? 'devnet' : 'mainnet';
  }

  bool get isDevnet => cluster == 'devnet';

  String get rpcHttp => _config?.network.rpcHttp.isNotEmpty == true
      ? _config!.network.rpcHttp
      : (kFallbackUseDevnet
            ? 'https://api.devnet.solana.com'
            : 'https://api.mainnet-beta.solana.com');

  String get privyAppId => _config?.privy.appId.isNotEmpty == true
      ? _config!.privy.appId
      : kFallbackPrivyAppId;

  String get privyClientId => _config?.privy.clientId.isNotEmpty == true
      ? _config!.privy.clientId
      : kFallbackPrivyClientId;

  String get programId => _config?.contract.programId.isNotEmpty == true
      ? _config!.contract.programId
      : kFallbackProgramId;

  String get skrMint => _config?.contract.skrMint.isNotEmpty == true
      ? _config!.contract.skrMint
      : kFallbackSkrMint;

  int get minBetBaseUnits => _config?.contract.minBetBaseUnits ?? 100;
  int get maxBetBaseUnits => _config?.contract.maxBetBaseUnits ?? 400;
  int get feeBps => _config?.contract.feeBps ?? 500;
  double get minBetUi => _baseUnitsToUi(minBetBaseUnits, tokenDecimals);
  double get maxBetUi => _baseUnitsToUi(maxBetBaseUnits, tokenDecimals);

  String get tokenSymbol => _config?.token.symbol.isNotEmpty == true
      ? _config!.token.symbol
      : kFallbackTokenSymbol;
  int get tokenDecimals => _config?.token.decimals ?? kFallbackTokenDecimals;

  String get explorerBaseUrl => _config?.explorer.baseUrl.isNotEmpty == true
      ? _config!.explorer.baseUrl
      : kFallbackExplorerBaseUrl;

  String get rawEnvironment =>
      _config?.environment.isNotEmpty == true ? _config!.environment : cluster;

  ClientConfig? get rawConfig => _config;

  double _baseUnitsToUi(int baseUnits, int decimals) {
    if (decimals <= 0) return baseUnits.toDouble();
    return baseUnits / math.pow(10, decimals);
  }

  Future<void> bootstrap({http.Client? client}) async {
    if (_attemptedBootstrap) return;
    _attemptedBootstrap = true;
    final httpClient = client ?? http.Client();
    final uri = Uri.parse('$kApiBaseUrl/client-config');
    _log('Bootstrapping from $uri');

    try {
      final resp = await httpClient
          .get(uri)
          .timeout(const Duration(seconds: 8));
      if (resp.statusCode != 200) {
        _log('client-config fetch failed: ${resp.statusCode}');
        return;
      }
      final body = jsonDecode(resp.body);
      if (body is! Map<String, dynamic>) {
        _log('client-config invalid JSON payload shape');
        return;
      }
      _config = ClientConfig.fromJson(body);
      _log(
        'Loaded config env=${_config!.environment} cluster=${_config!.network.cluster} '
        'program=${_config!.contract.programId} mint=${_config!.contract.skrMint}',
      );
    } catch (e) {
      _log('client-config bootstrap error: $e');
    } finally {
      if (client == null) {
        httpClient.close();
      }
    }
  }
}
