import '../core/constants.dart';

class ClientConfig {
  const ClientConfig({
    required this.environment,
    required this.cluster,
    required this.rpcHttp,
    required this.rpcWs,
    required this.skrMint,
    required this.tokenSymbol,
    required this.tokenDecimals,
  });

  final String environment;
  final String cluster;
  final String rpcHttp;
  final String rpcWs;
  final String skrMint;
  final String tokenSymbol;
  final int tokenDecimals;

  bool get isDevnet =>
      environment.toLowerCase().contains('dev') ||
      cluster.toLowerCase().contains('devnet');

  bool get isUsdStable =>
      tokenSymbol.toUpperCase() == 'USDC' ||
      tokenSymbol.toUpperCase() == 'USDT';

  factory ClientConfig.fromJson(Map<String, dynamic> json) {
    final network = (json['network'] as Map<String, dynamic>?) ?? const {};
    final contract = (json['contract'] as Map<String, dynamic>?) ?? const {};
    final token = (json['token'] as Map<String, dynamic>?) ?? const {};

    return ClientConfig(
      environment: (json['environment'] as String?)?.trim().isNotEmpty == true
          ? json['environment'] as String
          : (kUseDevnet ? 'devnet' : 'mainnet'),
      cluster: (network['cluster'] as String?)?.trim().isNotEmpty == true
          ? network['cluster'] as String
          : (kUseDevnet ? 'devnet' : 'mainnet-beta'),
      rpcHttp: (network['rpc_http'] as String?)?.trim().isNotEmpty == true
          ? network['rpc_http'] as String
          : (kUseDevnet
                ? 'https://api.devnet.solana.com'
                : 'https://api.mainnet-beta.solana.com'),
      rpcWs: (network['rpc_ws'] as String?)?.trim().isNotEmpty == true
          ? network['rpc_ws'] as String
          : (kUseDevnet
                ? 'wss://api.devnet.solana.com'
                : 'wss://api.mainnet-beta.solana.com'),
      skrMint: (contract['skr_mint'] as String?)?.trim().isNotEmpty == true
          ? contract['skr_mint'] as String
          : (kUseDevnet
                ? '4zMMC9srt5Ri5X14GAgXhaHii3GnPAEERYPJgZJDncDU'
                : 'SKRbvo6Gf7GondiT3BbTfuRDPqLWei4j2Qy2NPGZhW3'),
      tokenSymbol: (token['symbol'] as String?)?.trim().isNotEmpty == true
          ? token['symbol'] as String
          : (kUseDevnet ? 'USDC' : 'SKR'),
      tokenDecimals: (token['decimals'] as num?)?.toInt() ?? 6,
    );
  }

  factory ClientConfig.fallback() {
    return ClientConfig(
      environment: kUseDevnet ? 'devnet' : 'mainnet',
      cluster: kUseDevnet ? 'devnet' : 'mainnet-beta',
      rpcHttp: kUseDevnet
          ? 'https://api.devnet.solana.com'
          : 'https://api.mainnet-beta.solana.com',
      rpcWs: kUseDevnet
          ? 'wss://api.devnet.solana.com'
          : 'wss://api.mainnet-beta.solana.com',
      skrMint: kUseDevnet
          ? '4zMMC9srt5Ri5X14GAgXhaHii3GnPAEERYPJgZJDncDU'
          : 'SKRbvo6Gf7GondiT3BbTfuRDPqLWei4j2Qy2NPGZhW3',
      tokenSymbol: kUseDevnet ? 'USDC' : 'SKR',
      tokenDecimals: 6,
    );
  }
}
