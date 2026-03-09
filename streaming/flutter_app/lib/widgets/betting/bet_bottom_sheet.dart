import 'dart:math' as math;

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../core/palette.dart';
import '../../core/runtime_client_config.dart';
import '../../core/typography.dart';
import '../../models/bet.dart';
import '../../models/match.dart';
import '../../providers/auth_provider.dart';
import '../../providers/bet_provider.dart';
import '../../providers/match_provider.dart';
import '../../providers/wallet_provider.dart';
import '../shared/pressable.dart';
import 'bet_confirmation.dart';

class BetBottomSheet extends ConsumerStatefulWidget {
  const BetBottomSheet({
    super.key,
    required this.match,
    this.initialSelectedFighter = 0,
  });

  final Match match;
  final int initialSelectedFighter;

  @override
  ConsumerState<BetBottomSheet> createState() => _BetBottomSheetState();
}

class _BetBottomSheetState extends ConsumerState<BetBottomSheet> {
  static const double _eps = 1e-9;
  static const List<double> _quickBetCandidates = [100, 200, 500, 1000];

  late int _selectedFighter; // 0 = fighter1 (A), 1 = fighter2 (B)
  double? _selectedAmount;
  bool _loading = false;
  bool _confirmed = false;
  Bet? _placedBet;
  String? _error;

  @override
  void initState() {
    super.initState();
    _selectedFighter = widget.initialSelectedFighter.clamp(0, 1);
  }

  double get _minBetUi => RuntimeClientConfig.instance.minBetUi;
  double get _maxBetUi => RuntimeClientConfig.instance.maxBetUi;
  int get _feeBps => RuntimeClientConfig.instance.feeBps;
  int get _tokenDecimals => RuntimeClientConfig.instance.tokenDecimals;
  String get _tokenSymbol => RuntimeClientConfig.instance.tokenSymbol;

  String get _selectedName => _selectedFighter == 0
      ? widget.match.fighter1?.name ?? 'Fighter 1'
      : widget.match.fighter2?.name ?? 'Fighter 2';

  String get _selectedId => _selectedFighter == 0
      ? widget.match.fighter1?.id ?? ''
      : widget.match.fighter2?.id ?? '';

  String get _selectedSide => _selectedFighter == 0 ? 'A' : 'B';

  double _selectedPoolBefore() {
    if (_selectedFighter == 0) {
      if (widget.match.odds.fighter1Pool > 0) {
        return widget.match.odds.fighter1Pool;
      }
      return widget.match.totalPool * widget.match.odds.fighter1PoolPct;
    }
    if (widget.match.odds.fighter2Pool > 0) {
      return widget.match.odds.fighter2Pool;
    }
    return widget.match.totalPool * widget.match.odds.fighter2PoolPct;
  }

  double _oppositePoolBefore() {
    if (_selectedFighter == 0) {
      if (widget.match.odds.fighter2Pool > 0) {
        return widget.match.odds.fighter2Pool;
      }
      return widget.match.totalPool * widget.match.odds.fighter2PoolPct;
    }
    if (widget.match.odds.fighter1Pool > 0) {
      return widget.match.odds.fighter1Pool;
    }
    return widget.match.totalPool * widget.match.odds.fighter1PoolPct;
  }

  int _toBaseUnits(double uiAmount) {
    final scale = math.pow(10, _tokenDecimals).toDouble();
    return (uiAmount * scale).floor();
  }

  double _fromBaseUnits(int amountBase) {
    if (_tokenDecimals <= 0) return amountBase.toDouble();
    return amountBase / math.pow(10, _tokenDecimals);
  }

  String _fmt(double value, {int decimals = 2}) {
    final s = value.toStringAsFixed(decimals);
    return s.replaceFirst(RegExp(r'\.?0+$'), '');
  }

  List<double> _quickBetsFor(double maxSelectable) {
    return _quickBetCandidates
        .where((v) => v + _eps >= _minBetUi && v - _eps <= maxSelectable)
        .toList();
  }

  bool _isAmountValid(double amount, double maxSelectable) {
    if (amount <= 0) return false;
    if (amount + _eps < _minBetUi) return false;
    if (amount - _eps > maxSelectable) return false;
    return true;
  }

  bool _isSelected(double value) {
    final picked = _selectedAmount;
    if (picked == null) return false;
    return (picked - value).abs() < _eps;
  }

  double _estimatedPayout(double amount) {
    if (amount <= 0) return 0;
    final selectedBeforeBase = _toBaseUnits(_selectedPoolBefore());
    final oppositeBeforeBase = _toBaseUnits(_oppositePoolBefore());
    final amountBase = _toBaseUnits(amount);
    final selectedAfterBase = selectedBeforeBase + amountBase;
    if (selectedAfterBase <= 0) return 0;

    // Platform fee is charged from the full pool (both sides).
    final totalAfterBase = selectedAfterBase + oppositeBeforeBase;
    final feeBase = (totalAfterBase * _feeBps) ~/ 10_000;
    final payoutPoolBase = totalAfterBase - feeBase;
    final estimatedBase = (payoutPoolBase * amountBase) ~/ selectedAfterBase;
    return _fromBaseUnits(estimatedBase);
  }

  Future<void> _placeBet({
    required double amount,
    required bool canBet,
    required double maxSelectable,
  }) async {
    if (!canBet) {
      setState(() {
        _error =
            'Insufficient balance. Minimum bet is ${_fmt(_minBetUi, decimals: 6)} $_tokenSymbol.';
      });
      return;
    }
    if (!_isAmountValid(amount, maxSelectable)) {
      setState(() {
        _error =
            'Bet amount must be between ${_fmt(_minBetUi, decimals: 6)} and ${_fmt(maxSelectable, decimals: 6)} $_tokenSymbol.';
      });
      return;
    }

    final api = ref.read(apiServiceProvider);
    final privy = ref.read(privyServiceProvider);
    final accessToken = await privy.getAccessToken();
    if (accessToken == null || accessToken.isEmpty) {
      setState(() => _error = 'Session expired. Please log in again.');
      return;
    }
    api.setAuthToken(accessToken);

    setState(() {
      _loading = true;
      _error = null;
    });

    try {
      final bet = await ref
          .read(betProvider.notifier)
          .placeBet(
            matchId: widget.match.id,
            fighterId: _selectedId,
            amount: amount,
            side: _selectedSide,
          );
      if (bet != null && mounted) {
        setState(() {
          _confirmed = true;
          _placedBet = bet;
        });
      }
    } catch (e) {
      if (mounted) {
        setState(() => _error = e.toString());
      }
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final wallet = ref.watch(walletProvider);
    final walletBalance = wallet.seekerBalance;
    final maxSelectable = math.min(walletBalance, _maxBetUi);
    final canBet = maxSelectable + _eps >= _minBetUi;
    final quickBets = _quickBetsFor(maxSelectable);
    final amount = _selectedAmount;
    final amountValid = amount != null && _isAmountValid(amount, maxSelectable);
    final amountUi = amount ?? 0.0;
    final estPayout = amountValid ? _estimatedPayout(amountUi) : 0.0;
    final estWinnings = amountValid ? (estPayout - amountUi) : 0.0;
    final estRoiPct = amountValid && amountUi > 0
        ? (estWinnings / amountUi) * 100
        : 0.0;
    final estReturnLabel =
        '${_fmt(estPayout, decimals: 6)} $_tokenSymbol (${_fmt(estRoiPct, decimals: 2)}%)';
    final bottom = MediaQuery.of(context).padding.bottom;

    return AnimatedSwitcher(
      duration: const Duration(milliseconds: 320),
      child: _confirmed && _placedBet != null
          ? BetConfirmation(key: const ValueKey('confirm'), bet: _placedBet!)
          : Container(
              key: const ValueKey('form'),
              padding: EdgeInsets.only(
                left: 24,
                right: 24,
                top: 24,
                bottom: bottom + 24,
              ),
              decoration: const BoxDecoration(
                color: Palette.sheetBg,
                borderRadius: BorderRadius.vertical(top: Radius.circular(16)),
              ),
              child: Column(
                mainAxisSize: MainAxisSize.min,
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Center(
                    child: Container(
                      width: 40,
                      height: 4,
                      decoration: BoxDecoration(
                        color: Palette.muted,
                        borderRadius: BorderRadius.circular(2),
                      ),
                    ),
                  ),
                  const SizedBox(height: 18),
                  // Center(child: Text('Place Your Bet', style: displayStyle(size: 28))),
                  // const SizedBox(height: 4),
                  Center(
                    child: Text(
                      'Bet on ${_selectedName.toUpperCase()}',
                      style: bodyStyle(
                        size: 28,
                        color: Palette.gold,
                        weight: FontWeight.w700,
                      ),
                    ),
                  ),
                  const SizedBox(height: 10),
                  Text(
                    'Wallet: ${_fmt(walletBalance, decimals: 6)} $_tokenSymbol',
                    style: bodyStyle(size: 12, color: Palette.muted),
                  ),
                  const SizedBox(height: 3),
                  Text(
                    'Allowed range: ${_fmt(_minBetUi, decimals: 6)} - ${_fmt(_maxBetUi, decimals: 6)} $_tokenSymbol',
                    style: bodyStyle(size: 12, color: Palette.muted),
                  ),
                  const SizedBox(height: 14),
                  Text(
                    'Quick Bet',
                    style: bodyStyle(size: 12, color: Palette.muted),
                  ),
                  const SizedBox(height: 6),
                  Wrap(
                    spacing: 8,
                    runSpacing: 8,
                    children: [
                      for (final quick in quickBets)
                        _QuickChip(
                          label: _fmt(quick),
                          selected: _isSelected(quick),
                          onTap: () => setState(() {
                            _selectedAmount = quick;
                            _error = null;
                          }),
                        ),
                      _QuickChip(
                        label: 'MAX',
                        selected:
                            canBet &&
                            amount != null &&
                            (amount - maxSelectable).abs() < _eps,
                        onTap: canBet
                            ? () => setState(() {
                                _selectedAmount = maxSelectable;
                                _error = null;
                              })
                            : () {},
                      ),
                    ],
                  ),
                  const SizedBox(height: 10),
                  if (amount != null)
                    Text(
                      'Selected Amount: ${_fmt(amount, decimals: 6)} $_tokenSymbol',
                      style: bodyStyle(size: 13, color: Palette.secondary),
                    )
                  else
                    Text(
                      'Pick an amount to preview return.',
                      style: bodyStyle(size: 13, color: Palette.muted),
                    ),
                  if (!canBet) ...[
                    const SizedBox(height: 8),
                    Text(
                      'Minimum bet is ${_fmt(_minBetUi, decimals: 6)} $_tokenSymbol.',
                      style: bodyStyle(size: 12, color: Palette.red),
                    ),
                  ],
                  if (amountValid) ...[
                    const SizedBox(height: 10),
                    Row(
                      mainAxisAlignment: MainAxisAlignment.spaceBetween,
                      children: [
                        Expanded(
                          child: Text(
                            'Return if $_selectedName wins',
                            style: bodyStyle(size: 12, color: Palette.muted),
                          ),
                        ),
                        const SizedBox(width: 10),
                        Flexible(
                          child: Text(
                            estReturnLabel,
                            textAlign: TextAlign.right,
                            maxLines: 1,
                            overflow: TextOverflow.ellipsis,
                            style: bodyStyle(
                              size: 12,
                              color:
                                  estWinnings >= 0 ? Palette.green : Palette.red,
                              weight: FontWeight.w700,
                            ),
                          ),
                        ),
                      ],
                    ),
                    const SizedBox(height: 4),
                    Text(
                      'returns might change based on new bets.',
                      style: bodyStyle(size: 11, color: Palette.muted),
                    ),
                  ],
                  if (_error != null) ...[
                    const SizedBox(height: 8),
                    Text(
                      _error!,
                      style: bodyStyle(size: 12, color: Palette.red),
                    ),
                  ],
                  const SizedBox(height: 16),
                  SizedBox(
                    width: double.infinity,
                    height: 50,
                    child: ElevatedButton(
                      onPressed: !_loading && amountValid
                          ? () => _placeBet(
                              amount: amount,
                              canBet: canBet,
                              maxSelectable: maxSelectable,
                            )
                          : null,
                      style: ElevatedButton.styleFrom(
                        backgroundColor: Palette.gold,
                        foregroundColor: Palette.black,
                        disabledBackgroundColor: Palette.border,
                        shape: RoundedRectangleBorder(
                          borderRadius: BorderRadius.circular(4),
                        ),
                      ),
                      child: _loading
                          ? const SizedBox(
                              width: 20,
                              height: 20,
                              child: CircularProgressIndicator(
                                strokeWidth: 2,
                                color: Colors.black,
                              ),
                            )
                          : Text(
                              'PLACE BET ON ${_selectedName.toUpperCase()}',
                              style: bodyStyle(
                                size: 13,
                                color: Palette.black,
                                weight: FontWeight.w700,
                              ),
                              textAlign: TextAlign.center,
                            ),
                    ),
                  ),
                ],
              ),
            ),
    );
  }
}

class _QuickChip extends StatelessWidget {
  const _QuickChip({
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
      haptic: true,
      child: AnimatedContainer(
        duration: const Duration(milliseconds: 160),
        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 7),
        decoration: BoxDecoration(
          borderRadius: BorderRadius.circular(3),
          border: Border.all(
            color: selected ? Palette.gold : Palette.border,
            width: selected ? 1.3 : 1,
          ),
          color: selected
              ? Palette.darkGold.withValues(alpha: 0.45)
              : Colors.transparent,
        ),
        child: Text(
          label,
          style: bodyStyle(
            size: 13,
            color: selected ? Palette.gold : Palette.secondary,
            weight: FontWeight.w600,
          ),
        ),
      ),
    );
  }
}
