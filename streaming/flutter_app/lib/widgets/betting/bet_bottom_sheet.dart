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
  bool _selectedMax = false;
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

  double _resolveCurrentAmount({
    required bool canBet,
    required double maxSelectable,
    required List<double> quickBets,
  }) {
    if (!canBet) return 0;
    if (_selectedMax) return maxSelectable;

    final manual = _selectedAmount;
    if (manual != null) {
      final clamped = manual.clamp(0, maxSelectable).toDouble();
      if (clamped + _eps >= _minBetUi) return clamped;
    }

    if (quickBets.isNotEmpty) return quickBets.first;
    return maxSelectable;
  }

  double _estimatedPayout(double amount) {
    if (amount <= 0) return 0;
    final totalBeforeBase = _toBaseUnits(widget.match.totalPool);
    final selectedBeforeBase = _toBaseUnits(_selectedPoolBefore());
    final amountBase = _toBaseUnits(amount);
    final totalAfterBase = totalBeforeBase + amountBase;
    final selectedAfterBase = selectedBeforeBase + amountBase;
    if (selectedAfterBase <= 0) return 0;

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
    final amount = _resolveCurrentAmount(
      canBet: canBet,
      maxSelectable: maxSelectable,
      quickBets: quickBets,
    );
    final amountValid = _isAmountValid(amount, maxSelectable);
    final totalPool = widget.match.totalPool;
    final sideAPool = widget.match.odds.fighter1Pool > 0
        ? widget.match.odds.fighter1Pool
        : totalPool * widget.match.odds.fighter1PoolPct;
    final sideBPool = widget.match.odds.fighter2Pool > 0
        ? widget.match.odds.fighter2Pool
        : totalPool * widget.match.odds.fighter2PoolPct;
    final selectedPoolBefore = _selectedPoolBefore();
    final selectedPoolAfter = selectedPoolBefore + amount;
    final totalAfter = totalPool + amount;
    final feeRate = _feeBps / 10000.0;
    final payoutPoolAfter = totalAfter * (1 - feeRate);
    final estPayout = _estimatedPayout(amount);
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
                children: [
                  Container(
                    width: 40,
                    height: 4,
                    decoration: BoxDecoration(
                      color: Palette.muted,
                      borderRadius: BorderRadius.circular(2),
                    ),
                  ),
                  const SizedBox(height: 18),
                  Text('Place Your Bet', style: displayStyle(size: 28)),
                  const SizedBox(height: 4),
                  Text(
                    'Wallet: ${_fmt(walletBalance, decimals: 6)} $_tokenSymbol',
                    style: bodyStyle(size: 12, color: Palette.muted),
                  ),
                  const SizedBox(height: 14),
                  Row(
                    children: [
                      Expanded(
                        child: _FighterCard(
                          name: widget.match.fighter1?.name ?? 'Fighter 1',
                          poolAmount: sideAPool,
                          tokenSymbol: _tokenSymbol,
                          side: 'A',
                          selected: _selectedFighter == 0,
                          onTap: () => setState(() => _selectedFighter = 0),
                        ),
                      ),
                      const SizedBox(width: 10),
                      Expanded(
                        child: _FighterCard(
                          name: widget.match.fighter2?.name ?? 'Fighter 2',
                          poolAmount: sideBPool,
                          tokenSymbol: _tokenSymbol,
                          side: 'B',
                          selected: _selectedFighter == 1,
                          onTap: () => setState(() => _selectedFighter = 1),
                        ),
                      ),
                    ],
                  ),
                  const SizedBox(height: 14),
                  Align(
                    alignment: Alignment.centerLeft,
                    child: Text(
                      'Quick Bet',
                      style: bodyStyle(size: 12, color: Palette.muted),
                    ),
                  ),
                  const SizedBox(height: 6),
                  Wrap(
                    spacing: 8,
                    runSpacing: 8,
                    children: [
                      for (final quick in quickBets)
                        _QuickChip(
                          label: _fmt(quick),
                          selected:
                              !_selectedMax && (amount - quick).abs() < _eps,
                          onTap: () => setState(() {
                            _selectedMax = false;
                            _selectedAmount = quick;
                            _error = null;
                          }),
                        ),
                      _QuickChip(
                        label: 'MAX',
                        selected: _selectedMax,
                        onTap: () => setState(() {
                          _selectedMax = true;
                          _selectedAmount = maxSelectable;
                          _error = null;
                        }),
                      ),
                    ],
                  ),
                  const SizedBox(height: 10),
                  Align(
                    alignment: Alignment.centerLeft,
                    child: Text(
                      'Selected Amount: ${_fmt(amount, decimals: 6)} $_tokenSymbol',
                      style: bodyStyle(size: 13, color: Palette.secondary),
                    ),
                  ),
                  if (!canBet) ...[
                    const SizedBox(height: 8),
                    Align(
                      alignment: Alignment.centerLeft,
                      child: Text(
                        'Minimum bet is ${_fmt(_minBetUi, decimals: 6)} $_tokenSymbol.',
                        style: bodyStyle(size: 12, color: Palette.red),
                      ),
                    ),
                  ],
                  const SizedBox(height: 14),
                  _InfoRow(
                    label: 'Total Pool',
                    value: '${_fmt(totalPool, decimals: 6)} $_tokenSymbol',
                  ),
                  _InfoRow(
                    label: 'Side A Pool',
                    value: '${_fmt(sideAPool, decimals: 6)} $_tokenSymbol',
                  ),
                  _InfoRow(
                    label: 'Side B Pool',
                    value: '${_fmt(sideBPool, decimals: 6)} $_tokenSymbol',
                  ),
                  _InfoRow(
                    label: 'Your Side Pool After Bet',
                    value:
                        '${_fmt(selectedPoolAfter, decimals: 6)} $_tokenSymbol',
                  ),
                  _InfoRow(
                    label: 'Platform Fee',
                    value: '${(_feeBps / 100).toStringAsFixed(2)}%',
                  ),
                  _InfoRow(
                    label: 'Payout Pool After Fee',
                    value:
                        '${_fmt(payoutPoolAfter, decimals: 6)} $_tokenSymbol',
                  ),
                  _InfoRow(
                    label: 'Estimated Payout if $_selectedName wins',
                    value: '${_fmt(estPayout, decimals: 6)} $_tokenSymbol',
                    valueColor: Palette.green,
                  ),
                  if (_error != null) ...[
                    const SizedBox(height: 8),
                    Align(
                      alignment: Alignment.centerLeft,
                      child: Text(
                        _error!,
                        style: bodyStyle(size: 12, color: Palette.red),
                      ),
                    ),
                  ],
                  const SizedBox(height: 16),
                  SizedBox(
                    width: double.infinity,
                    height: 50,
                    child: ElevatedButton(
                      onPressed: !_loading && canBet && amountValid
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
                              'Confirm ${_fmt(amount, decimals: 6)} $_tokenSymbol on $_selectedName',
                              style: bodyStyle(
                                size: 14,
                                color: Palette.black,
                                weight: FontWeight.w600,
                              ),
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

class _FighterCard extends StatelessWidget {
  const _FighterCard({
    required this.name,
    required this.poolAmount,
    required this.tokenSymbol,
    required this.side,
    required this.selected,
    required this.onTap,
  });

  final String name;
  final double poolAmount;
  final String tokenSymbol;
  final String side;
  final bool selected;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return Pressable(
      onTap: onTap,
      haptic: true,
      child: AnimatedContainer(
        duration: const Duration(milliseconds: 180),
        curve: Curves.easeOut,
        padding: const EdgeInsets.symmetric(vertical: 14, horizontal: 10),
        decoration: BoxDecoration(
          border: Border.all(
            color: selected ? Palette.gold : Palette.border,
            width: selected ? 1.4 : 1,
          ),
          borderRadius: BorderRadius.circular(4),
          color: selected
              ? Palette.darkGold.withValues(alpha: 0.45)
              : Colors.transparent,
        ),
        child: Column(
          children: [
            Text(
              'SIDE $side',
              style: bodyStyle(
                size: 10,
                color: selected ? Palette.gold : Palette.muted,
                weight: FontWeight.w600,
                letterSpacing: 0.8,
              ),
            ),
            const SizedBox(height: 5),
            Text(
              name,
              textAlign: TextAlign.center,
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: displayStyle(
                size: 14,
                color: selected ? Palette.gold : Palette.white,
              ),
            ),
            const SizedBox(height: 3),
            Text(
              '${poolAmount.toStringAsFixed(2)} $tokenSymbol',
              style: bodyStyle(size: 11, color: Palette.secondary),
            ),
          ],
        ),
      ),
    );
  }
}

class _InfoRow extends StatelessWidget {
  const _InfoRow({
    required this.label,
    required this.value,
    this.valueColor = Palette.secondary,
  });

  final String label;
  final String value;
  final Color valueColor;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 4),
      child: Row(
        mainAxisAlignment: MainAxisAlignment.spaceBetween,
        children: [
          Expanded(
            child: Text(
              label,
              style: bodyStyle(size: 12, color: Palette.muted),
            ),
          ),
          const SizedBox(width: 12),
          Text(value, style: bodyStyle(size: 12, color: valueColor)),
        ],
      ),
    );
  }
}
