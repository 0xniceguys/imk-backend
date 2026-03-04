import 'dart:convert';
import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:intl/intl.dart';
import '../../core/constants.dart';
import '../../core/palette.dart';
import '../../core/typography.dart';
import '../../providers/match_provider.dart';
import '../../providers/wallet_provider.dart';
import '../../services/api_service.dart';
import '../shared/pressable.dart';
import '../shared/ik_loader.dart';

enum _WithdrawToken { sol, seeker }

class WalletManageSheet extends ConsumerStatefulWidget {
  const WalletManageSheet({super.key});

  @override
  ConsumerState<WalletManageSheet> createState() => _WalletManageSheetState();
}

class _WalletManageSheetState extends ConsumerState<WalletManageSheet> {
  _WithdrawToken _token = _WithdrawToken.sol;
  final _addrCtrl = TextEditingController();
  final _amountCtrl = TextEditingController();
  bool _isSending = false;

  // Solana base fee for a simple transfer: 5000 lamports = 0.000005 SOL.
  // This is the network protocol constant — no backend call needed.
  // Priority fees are typically 0 on non-congested mainnet.
  static const double _networkFeeSol = 0.000005;

  @override
  void initState() {
    super.initState();
    // Refresh balance every time the sheet opens so deposits are visible.
    WidgetsBinding.instance.addPostFrameCallback((_) {
      ref.read(walletProvider.notifier).refreshBalance();
    });
    // Rebuild fee estimate whenever amount changes.
    _amountCtrl.addListener(() => setState(() {}));
  }

  @override
  void dispose() {
    _addrCtrl.dispose();
    _amountCtrl.dispose();
    super.dispose();
  }

  Future<void> _onSend() async {
    final address = _addrCtrl.text.trim();
    final amountText = _amountCtrl.text.trim();
    if (address.isEmpty || amountText.isEmpty) {
      _showError('Please enter both an address and amount');
      return;
    }

    final amount = double.tryParse(amountText);
    if (amount == null || amount <= 0) {
      _showError('Enter a valid amount');
      return;
    }

    // Validate address length (Solana addresses are 32-44 chars base58)
    if (address.length < 32 || address.length > 44) {
      _showError('Invalid Solana address');
      return;
    }

    // Check sufficient balance
    final wallet = ref.read(walletProvider);
    final maxAmount = _token == _WithdrawToken.sol
        ? wallet.solBalance
        : wallet.seekerBalance;
    if (amount > maxAmount) {
      _showError('Insufficient balance. You have ${maxAmount.toStringAsFixed(4)} '
          '${_token == _WithdrawToken.sol ? 'SOL' : 'SEEKER'}');
      return;
    }

    setState(() => _isSending = true);
    try {
      final api = ref.read(apiServiceProvider);
      final walletNotifier = ref.read(walletProvider.notifier);

      // Always refresh the Privy JWT before withdrawing — tokens expire
      // and a stale token causes a 401 / 403 from the backend.
      await walletNotifier.syncAuthToken(api);

      debugPrint('[Withdraw] Step 1: Preparing unsigned transaction...');
      // Step 1: Get unsigned transaction from backend
      final unsignedTxBase64 = await api.prepareWithdraw(
        token: _token == _WithdrawToken.sol ? 'sol' : 'seeker',
        toAddress: address,
        amount: amount,
      );
      debugPrint('[Withdraw] Got unsigned tx: ${unsignedTxBase64.substring(0, 50)}...');

      // Step 2: Sign with Privy embedded wallet
      debugPrint('[Withdraw] Step 2: Signing with Privy wallet...');
      final txBytes = base64Decode(unsignedTxBase64);
      final signedTxBase64 = await walletNotifier.signTransaction(txBytes);

      if (signedTxBase64 == null) {
        throw Exception('Failed to sign transaction with Privy wallet');
      }
      debugPrint('[Withdraw] Transaction signed: ${signedTxBase64.substring(0, 50)}...');

      // Step 3: Broadcast signed transaction
      debugPrint('[Withdraw] Step 3: Broadcasting transaction...');
      final sig = await api.broadcastWithdraw(
        signedTransactionBase64: signedTxBase64,
      );

      debugPrint('[Withdraw] Success! TX: $sig');
      final tokenName = _token == _WithdrawToken.sol ? 'SOL' : 'SEEKER';
      final short = '${sig.substring(0, 8)}...${sig.substring(sig.length - 6)}';
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(
        content: Text('Sent $tokenName! TX: $short'),
        backgroundColor: Palette.gold,
        duration: const Duration(seconds: 4),
      ));
      _addrCtrl.clear();
      _amountCtrl.clear();

      // Poll with exponential backoff until balance reflects the withdrawal
      debugPrint('[Withdraw] Starting retry-polling for balance update...');
      await walletNotifier.refreshWithRetry();
      debugPrint('[Withdraw] Balance refresh complete');
    } catch (e) {
      debugPrint('[Withdraw] Error: $e');
      if (!mounted) return;
      _showError(_friendlyError(e));
    } finally {
      if (mounted) setState(() => _isSending = false);
    }
  }

  /// Parses common Solana / backend errors into user-friendly messages.
  String _friendlyError(Object e) {
    final raw = e.toString();
    if (raw.contains('Insufficient') || raw.contains('insufficient')) {
      return 'Insufficient balance for this transaction.';
    }
    if (raw.contains('Blockhash not found') || raw.contains('blockhash')) {
      return 'Transaction expired. Please try again.';
    }
    if (raw.contains('Invalid') && raw.contains('address')) {
      return 'Invalid destination address.';
    }
    if (raw.contains('401') || raw.contains('403') || raw.contains('Unauthorized')) {
      return 'Session expired. Please close and reopen this page.';
    }
    if (raw.contains('No SEEKER token account')) {
      return 'Recipient has no SEEKER token account.';
    }
    if (raw.contains('sign')) {
      return 'Failed to sign the transaction. Please try again.';
    }
    if (raw.contains('timeout') || raw.contains('Timeout')) {
      return 'Network timeout. Check your connection and retry.';
    }
    // Fallback: strip the "Exception: " prefix
    return raw.replaceFirst('Exception: ', '');
  }

  void _showError(String msg) {
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(
      content: Text(msg),
      backgroundColor: Palette.red,
      duration: const Duration(seconds: 4),
    ));
  }

  @override
  Widget build(BuildContext context) {
    final wallet = ref.watch(walletProvider);
    final bottomInset = MediaQuery.of(context).viewInsets.bottom;
    final bottomSafe = MediaQuery.of(context).padding.bottom;
    final bottom = bottomInset > 0 ? bottomInset + 16 : bottomSafe + 24;

    final address = wallet.solanaAddress ?? '';
    final truncated = address.length > 12
        ? '${address.substring(0, 6)}...${address.substring(address.length - 4)}'
        : address;

    final seekerLabel = kUseDevnet ? 'USDC' : 'SEEKER';
    final seekerUnit = kUseDevnet ? 'USDC' : 'SKR';
    final maxAmount = _token == _WithdrawToken.sol
        ? wallet.solBalance
        : wallet.seekerBalance;

    return Container(
      padding: EdgeInsets.only(left: 24, right: 24, top: 24, bottom: bottom),
      decoration: const BoxDecoration(
        color: Palette.sheetBg,
        borderRadius: BorderRadius.vertical(top: Radius.circular(16)),
      ),
      child: SingleChildScrollView(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            // Handle bar
            Container(
              width: 40,
              height: 4,
              decoration: BoxDecoration(
                color: Palette.muted,
                borderRadius: BorderRadius.circular(2),
              ),
            ),
            const SizedBox(height: 20),
            Row(
              mainAxisAlignment: MainAxisAlignment.spaceBetween,
              children: [
                Text('Manage Wallet', style: displayStyle(size: 28)),
                GestureDetector(
                  onTap: () =>
                      ref.read(walletProvider.notifier).refreshBalance(),
                  child: const Icon(Icons.refresh, color: Palette.muted,
                      size: 20),
                ),
              ],
            ),
            const SizedBox(height: 24),

            // ── Balances ──────────────────────────────────────────────────
            // ── Error banner ─────────────────────────────────────────────
            if (wallet.errorMessage != null) ...[
              Container(
                width: double.infinity,
                padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
                decoration: BoxDecoration(
                  color: Palette.red.withValues(alpha: 0.1),
                  border: Border.all(color: Palette.red.withValues(alpha: 0.3)),
                  borderRadius: BorderRadius.circular(4),
                ),
                child: Row(
                  children: [
                    const Icon(Icons.error_outline, size: 16, color: Palette.red),
                    const SizedBox(width: 8),
                    Expanded(
                      child: Text(
                        wallet.errorMessage!,
                        style: bodyStyle(size: 12, color: Palette.red),
                      ),
                    ),
                    const SizedBox(width: 8),
                    GestureDetector(
                      onTap: () => ref.read(walletProvider.notifier).refreshBalance(),
                      child: Container(
                        padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
                        decoration: BoxDecoration(
                          border: Border.all(color: Palette.red.withValues(alpha: 0.3)),
                          borderRadius: BorderRadius.circular(4),
                        ),
                        child: Text('Retry', style: bodyStyle(size: 11, color: Palette.red)),
                      ),
                    ),
                  ],
                ),
              ),
              const SizedBox(height: 8),
            ],
            if (wallet.isLoading)
              const Padding(
                padding: EdgeInsets.symmetric(vertical: 8),
                child: IKLoader(size: 24),
              )
            else ...[
              _BalanceRow(
                label: 'SOL',
                usdValue: wallet.solUsdValue,
                subLabel: '${wallet.solBalance.toStringAsFixed(4)} SOL',
              ),
              const SizedBox(height: 8),
              _BalanceRow(
                label: kUseDevnet ? 'USDC (devnet)' : 'SEEKER',
                usdValue: wallet.seekerUsdValue,
                subLabel: '${wallet.seekerBalance.toStringAsFixed(2)} $seekerUnit',
              ),
            ],
            const SizedBox(height: 20),

            // ── Deposit — copy address ────────────────────────────────────
            Container(height: 1, color: Palette.border),
            const SizedBox(height: 16),
            Align(
              alignment: Alignment.centerLeft,
              child: Text('Deposit', style: bodyStyle(size: 14, color: Palette.muted)),
            ),
            const SizedBox(height: 12),
            // Address row — tap to copy
            Pressable(
              onTap: () {
                if (address.isEmpty) return;
                Clipboard.setData(ClipboardData(text: address));
                HapticFeedback.lightImpact();
                ScaffoldMessenger.of(context).showSnackBar(const SnackBar(
                  content: Text('Address copied'),
                  duration: Duration(seconds: 1),
                ));
              },
              child: Container(
                width: double.infinity,
                padding: const EdgeInsets.symmetric(vertical: 12, horizontal: 16),
                decoration: BoxDecoration(
                  border: Border.all(color: Palette.border),
                  borderRadius: BorderRadius.circular(4),
                ),
                child: Row(
                  mainAxisAlignment: MainAxisAlignment.spaceBetween,
                  children: [
                    Text(truncated,
                        style: bodyStyle(size: 14, color: Palette.secondary)),
                    const Icon(Icons.copy, size: 16, color: Palette.muted),
                  ],
                ),
              ),
            ),
            const SizedBox(height: 20),

            // ── Withdraw form ─────────────────────────────────────────────
            Container(height: 1, color: Palette.border),
            const SizedBox(height: 16),
            Align(
              alignment: Alignment.centerLeft,
              child: Text('Withdraw', style: bodyStyle(size: 14, color: Palette.muted)),
            ),
            const SizedBox(height: 12),

            // Token toggle
            Row(
              children: [
                _TokenToggle(
                  label: 'SOL',
                  selected: _token == _WithdrawToken.sol,
                  onTap: () => setState(() => _token = _WithdrawToken.sol),
                ),
                const SizedBox(width: 8),
                _TokenToggle(
                  label: seekerLabel,
                  selected: _token == _WithdrawToken.seeker,
                  onTap: () => setState(() => _token = _WithdrawToken.seeker),
                ),
              ],
            ),
            const SizedBox(height: 12),

            // To address
            Text('To address', style: bodyStyle(size: 12, color: Palette.muted)),
            const SizedBox(height: 6),
            _InputBox(
              controller: _addrCtrl,
              hint: 'Solana wallet address',
              keyboardType: TextInputType.text,
            ),
            const SizedBox(height: 10),

            // Amount + MAX
            Text('Amount', style: bodyStyle(size: 12, color: Palette.muted)),
            const SizedBox(height: 6),
            Row(
              children: [
                Expanded(
                  child: _InputBox(
                    controller: _amountCtrl,
                    hint: _token == _WithdrawToken.sol ? 'SOL' : seekerLabel,
                    keyboardType:
                        const TextInputType.numberWithOptions(decimal: true),
                  ),
                ),
                const SizedBox(width: 8),
                Pressable(
                  onTap: () => setState(
                      () => _amountCtrl.text = maxAmount.toString()),
                  child: Container(
                    padding: const EdgeInsets.symmetric(
                        horizontal: 12, vertical: 10),
                    decoration: BoxDecoration(
                      border: Border.all(color: Palette.border),
                      borderRadius: BorderRadius.circular(4),
                    ),
                    child: Text('MAX',
                        style: bodyStyle(size: 12, color: Palette.gold)),
                  ),
                ),
              ],
            ),
            const SizedBox(height: 12),

            // ── Fee estimate ─────────────────────────────────────────────
            Builder(builder: (context) {
              final wallet = ref.watch(walletProvider);
              // Derive SOL price from wallet state (avoids extra API call).
              final solPrice = wallet.solBalance > 0
                  ? wallet.solUsdValue / wallet.solBalance
                  : 0.0;
              final feeUsd = _networkFeeSol * solPrice;
              final typedAmount = double.tryParse(_amountCtrl.text.trim());
              final isSol = _token == _WithdrawToken.sol;

              return Container(
                padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
                decoration: BoxDecoration(
                  color: Palette.gold.withValues(alpha: 0.05),
                  border: Border.all(color: Palette.border),
                  borderRadius: BorderRadius.circular(4),
                ),
                child: Column(
                  children: [
                    Row(
                      mainAxisAlignment: MainAxisAlignment.spaceBetween,
                      children: [
                        Text('Network fee (est.)',
                            style: bodyStyle(size: 12, color: Palette.muted)),
                        Text(
                          '${_networkFeeSol.toStringAsFixed(6)} SOL'  
                          '${solPrice > 0 ? '  ≈ \$${feeUsd.toStringAsFixed(4)}' : ''}',
                          style: bodyStyle(size: 12, color: Palette.secondary),
                        ),
                      ],
                    ),
                    if (typedAmount != null && typedAmount > 0 && isSol) ...[
                      const SizedBox(height: 6),
                      Row(
                        mainAxisAlignment: MainAxisAlignment.spaceBetween,
                        children: [
                          Text('You send (incl. fee)',
                              style: bodyStyle(size: 12, color: Palette.muted)),
                          Text(
                            '${(typedAmount + _networkFeeSol).toStringAsFixed(6)} SOL',
                            style: bodyStyle(size: 12, color: Palette.gold),
                          ),
                        ],
                      ),
                    ],
                  ],
                ),
              );
            }),
            const SizedBox(height: 16),


            // Send / spinner
            if (_isSending)
              const Center(child: IKLoader(size: 28))
            else
              Pressable(
                onTap: _onSend,
                haptic: true,
                child: Container(
                  width: double.infinity,
                  padding: const EdgeInsets.symmetric(vertical: 14),
                  decoration: BoxDecoration(
                    border: Border.all(color: Palette.gold),
                    borderRadius: BorderRadius.circular(4),
                  ),
                  child: Row(
                    mainAxisAlignment: MainAxisAlignment.center,
                    children: [
                      const Icon(Icons.send, size: 18, color: Palette.gold),
                      const SizedBox(width: 8),
                      Text('Send',
                          style: bodyStyle(size: 14, color: Palette.gold)),
                    ],
                  ),
                ),
              ),
            const SizedBox(height: 8),
          ],
        ),
      ),
    );
  }
}

// ── Sub-widgets ───────────────────────────────────────────────────────────────

class _BalanceRow extends StatelessWidget {
  const _BalanceRow({
    required this.label,
    required this.usdValue,
    this.subLabel,
  });

  final String label;
  final double usdValue;
  final String? subLabel;

  static final _usd = NumberFormat.currency(symbol: '\$', decimalDigits: 2);

  @override
  Widget build(BuildContext context) {
    return Row(
      mainAxisAlignment: MainAxisAlignment.spaceBetween,
      children: [
        Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(label, style: bodyStyle(size: 16, color: Palette.muted)),
            if (subLabel != null)
              Text(subLabel!,
                  style: bodyStyle(size: 11, color: Palette.muted)),
          ],
        ),
        Text(_usd.format(usdValue),
            style: displayStyle(size: 20, color: Palette.gold)),
      ],
    );
  }
}

class _TokenToggle extends StatelessWidget {
  const _TokenToggle({
    required this.label,
    required this.selected,
    required this.onTap,
  });

  final String label;
  final bool selected;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return Pressable(
      onTap: onTap,
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
        decoration: BoxDecoration(
          color: selected
              ? Palette.gold.withValues(alpha: 0.12)
              : Colors.transparent,
          border: Border.all(color: selected ? Palette.gold : Palette.border),
          borderRadius: BorderRadius.circular(4),
        ),
        child: Text(label,
            style: bodyStyle(
                size: 13, color: selected ? Palette.gold : Palette.muted)),
      ),
    );
  }
}

class _InputBox extends StatelessWidget {
  const _InputBox({
    required this.controller,
    required this.hint,
    required this.keyboardType,
  });

  final TextEditingController controller;
  final String hint;
  final TextInputType keyboardType;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 4),
      decoration: BoxDecoration(
        border: Border.all(color: Palette.border),
        borderRadius: BorderRadius.circular(4),
      ),
      child: TextField(
        controller: controller,
        style: bodyStyle(size: 14),
        decoration: InputDecoration(
          hintText: hint,
          hintStyle: bodyStyle(size: 14, color: Palette.hint),
          border: InputBorder.none,
          isDense: true,
          contentPadding: const EdgeInsets.symmetric(vertical: 8),
        ),
        keyboardType: keyboardType,
      ),
    );
  }
}
