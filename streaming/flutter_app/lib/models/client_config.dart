class ClientNetworkConfig {
  final String cluster;
  final String rpcHttp;
  final String rpcWs;

  const ClientNetworkConfig({
    required this.cluster,
    required this.rpcHttp,
    required this.rpcWs,
  });

  factory ClientNetworkConfig.fromJson(Map<String, dynamic> json) {
    return ClientNetworkConfig(
      cluster: (json['cluster'] as String? ?? '').toLowerCase(),
      rpcHttp: json['rpc_http'] as String? ?? '',
      rpcWs: json['rpc_ws'] as String? ?? '',
    );
  }
}

class ClientPrivyConfig {
  final String appId;
  final String clientId;

  const ClientPrivyConfig({
    required this.appId,
    required this.clientId,
  });

  factory ClientPrivyConfig.fromJson(Map<String, dynamic> json) {
    return ClientPrivyConfig(
      appId: json['app_id'] as String? ?? '',
      clientId: json['client_id'] as String? ?? '',
    );
  }
}

class ClientContractConfig {
  final String programId;
  final String skrMint;
  final int feeBps;
  final int minBetBaseUnits;
  final int maxBetBaseUnits;
  final bool paused;
  final String source;

  const ClientContractConfig({
    required this.programId,
    required this.skrMint,
    required this.feeBps,
    required this.minBetBaseUnits,
    required this.maxBetBaseUnits,
    required this.paused,
    required this.source,
  });

  factory ClientContractConfig.fromJson(Map<String, dynamic> json) {
    int toInt(dynamic v, int fallback) {
      if (v is int) return v;
      if (v is num) return v.toInt();
      if (v is String) return int.tryParse(v) ?? fallback;
      return fallback;
    }

    return ClientContractConfig(
      programId: json['program_id'] as String? ?? '',
      skrMint: json['skr_mint'] as String? ?? '',
      feeBps: toInt(json['fee_bps'], 500),
      minBetBaseUnits: toInt(json['min_bet_base_units'], 100),
      maxBetBaseUnits: toInt(json['max_bet_base_units'], 400),
      paused: json['paused'] as bool? ?? false,
      source: json['source'] as String? ?? 'unknown',
    );
  }
}

class ClientTokenConfig {
  final String symbol;
  final int decimals;

  const ClientTokenConfig({
    required this.symbol,
    required this.decimals,
  });

  factory ClientTokenConfig.fromJson(Map<String, dynamic> json) {
    int toInt(dynamic v, int fallback) {
      if (v is int) return v;
      if (v is num) return v.toInt();
      if (v is String) return int.tryParse(v) ?? fallback;
      return fallback;
    }

    return ClientTokenConfig(
      symbol: json['symbol'] as String? ?? 'SKR',
      decimals: toInt(json['decimals'], 6),
    );
  }
}

class ClientExplorerConfig {
  final String baseUrl;

  const ClientExplorerConfig({required this.baseUrl});

  factory ClientExplorerConfig.fromJson(Map<String, dynamic> json) {
    return ClientExplorerConfig(baseUrl: json['base_url'] as String? ?? '');
  }
}

class ClientConfig {
  final int version;
  final String environment;
  final String generatedAt;
  final ClientNetworkConfig network;
  final ClientPrivyConfig privy;
  final ClientContractConfig contract;
  final ClientTokenConfig token;
  final ClientExplorerConfig explorer;

  const ClientConfig({
    required this.version,
    required this.environment,
    required this.generatedAt,
    required this.network,
    required this.privy,
    required this.contract,
    required this.token,
    required this.explorer,
  });

  factory ClientConfig.fromJson(Map<String, dynamic> json) {
    int toInt(dynamic v, int fallback) {
      if (v is int) return v;
      if (v is num) return v.toInt();
      if (v is String) return int.tryParse(v) ?? fallback;
      return fallback;
    }

    return ClientConfig(
      version: toInt(json['version'], 1),
      environment: (json['environment'] as String? ?? '').toLowerCase(),
      generatedAt: json['generated_at'] as String? ?? '',
      network: ClientNetworkConfig.fromJson(
        (json['network'] as Map?)?.cast<String, dynamic>() ?? const {},
      ),
      privy: ClientPrivyConfig.fromJson(
        (json['privy'] as Map?)?.cast<String, dynamic>() ?? const {},
      ),
      contract: ClientContractConfig.fromJson(
        (json['contract'] as Map?)?.cast<String, dynamic>() ?? const {},
      ),
      token: ClientTokenConfig.fromJson(
        (json['token'] as Map?)?.cast<String, dynamic>() ?? const {},
      ),
      explorer: ClientExplorerConfig.fromJson(
        (json['explorer'] as Map?)?.cast<String, dynamic>() ?? const {},
      ),
    );
  }
}
