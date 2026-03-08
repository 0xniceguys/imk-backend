import '../core/constants.dart';
import '../core/runtime_client_config.dart';
import '../models/wallet_state.dart';

double resolveSkrUsdPrice(WalletState wallet, {RuntimeClientConfig? config}) {
  if (wallet.seekerUsdPrice > 0 && wallet.seekerUsdPrice.isFinite) {
    return wallet.seekerUsdPrice;
  }

  if (wallet.seekerBalance > 0 && wallet.seekerUsdValue > 0) {
    final inferred = wallet.seekerUsdValue / wallet.seekerBalance;
    if (inferred.isFinite && inferred > 0) {
      return inferred;
    }
  }

  final cfg = config ?? RuntimeClientConfig.instance;
  if (cfg.isDevnet) {
    return kDevnetSkrPriceUsd;
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
