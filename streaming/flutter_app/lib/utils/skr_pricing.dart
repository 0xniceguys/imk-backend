import '../core/runtime_client_config.dart';
import '../models/wallet_state.dart';

const double kDevnetSkrFallbackPriceUsd = 0.25;

double resolveSkrUsdPrice(WalletState wallet, {RuntimeClientConfig? config}) {
  if (wallet.seekerBalance > 0 && wallet.seekerUsdValue > 0) {
    final inferred = wallet.seekerUsdValue / wallet.seekerBalance;
    if (inferred.isFinite && inferred > 0) {
      return inferred;
    }
  }

  final cfg = config ?? RuntimeClientConfig.instance;
  if (cfg.isDevnet) {
    return kDevnetSkrFallbackPriceUsd;
  }
  return 0;
}

double skrToUsd(
  double amountSkr,
  WalletState wallet, {
  RuntimeClientConfig? config,
}) {
  if (!amountSkr.isFinite) return 0;
  return amountSkr * resolveSkrUsdPrice(wallet, config: config);
}
