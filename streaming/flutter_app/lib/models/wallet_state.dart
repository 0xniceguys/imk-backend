class WalletState {
  final String? solanaAddress;
  final double solBalance;
  final double usdcBalance;
  final double seekerBalance;
  final double solUsdValue;
  final double seekerUsdValue;
  final bool isLoading;
  final String? errorMessage;

  const WalletState({
    this.solanaAddress,
    this.solBalance = 0,
    this.usdcBalance = 0,
    this.seekerBalance = 0,
    this.solUsdValue = 0,
    this.seekerUsdValue = 0,
    this.isLoading = false,
    this.errorMessage,
  });

  double get totalUsdValue => solUsdValue + seekerUsdValue + usdcBalance;

  WalletState copyWith({
    String? solanaAddress,
    double? solBalance,
    double? usdcBalance,
    double? seekerBalance,
    double? solUsdValue,
    double? seekerUsdValue,
    bool? isLoading,
    String? errorMessage,
    bool clearError = false,
  }) =>
      WalletState(
        solanaAddress: solanaAddress ?? this.solanaAddress,
        solBalance: solBalance ?? this.solBalance,
        usdcBalance: usdcBalance ?? this.usdcBalance,
        seekerBalance: seekerBalance ?? this.seekerBalance,
        solUsdValue: solUsdValue ?? this.solUsdValue,
        seekerUsdValue: seekerUsdValue ?? this.seekerUsdValue,
        isLoading: isLoading ?? this.isLoading,
        errorMessage: clearError ? null : (errorMessage ?? this.errorMessage),
      );
}
