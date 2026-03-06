import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../core/palette.dart';
import '../../core/typography.dart';
import '../../models/match.dart';
import '../../models/bet.dart';
import '../../providers/auth_provider.dart';
import '../../providers/bet_provider.dart';
import '../../providers/match_provider.dart';
import '../../core/api_exception.dart';
import '../shared/pressable.dart';
import 'bet_confirmation.dart';

// Betting limits — must match backend config defaults.
// These can be driven from a config endpoint in the future.
const double _kMinBet = 1.0;
const double _kMaxBet = 10000.0;
const double _kFeePct  = 0.05;
const List<double> _kQuickAmounts = [5, 10, 50, 100];

class BetBottomSheet extends ConsumerStatefulWidget {
  const BetBottomSheet({super.key, required this.match});
  final Match match;

  @override
  ConsumerState<BetBottomSheet> createState() => _BetBottomSheetState();
}

class _BetBottomSheetState extends ConsumerState<BetBottomSheet> {
  int _selectedFighter = 0; // 0 = Side A (fighter1), 1 = Side B (fighter2)
  final _amountController = TextEditingController(text: '10');
  bool _loading   = false;
  bool _confirmed = false;
  Bet?  _placedBet;
  String? _error;

  // Live match snapshot — refreshed when sheet opens so pool data is fresh.
  late Match _match;

  @override
  void initState() {
    super.initState();
    _match = widget.match;
    _refreshMatch();
  }

  /// Pull fresh match data (pool totals, odds) from the backend.
  Future<void> _refreshMatch() async {
    try {
      await ref.read(matchProvider.notifier).refresh();
      final updated = ref.read(matchProvider)
          .cast<Match?>()
          .firstWhere((m) => m?.id == widget.match.id, orElse: () => null);
      if (updated != null && mounted) setState(() => _match = updated);
    } catch (_) {
      // Silently fall back to widget.match — stale data beats crash.
    }
  }

  @override
  void dispose() {
    _amountController.dispose();
    super.dispose();
  }

  double get _amount => double.tryParse(_amountController.text) ?? 0;

  String get _selectedName =>
      _selectedFighter == 0 ? _match.fighter1.name : _match.fighter2.name;

  String get _selectedId =>
      _selectedFighter == 0 ? _match.fighter1.id : _match.fighter2.id;

  String get _selectedSide => _selectedFighter == 0 ? 'A' : 'B';

  /// Returns null if the amount is valid, otherwise an error string.
  String? get _amountError {
    final a = _amount;
    if (a <= 0) return null; // Don't show error until user has typed something
    if (a < _kMinBet) return 'Minimum bet is ${_kMinBet.toStringAsFixed(0)} SKR';
    if (a > _kMaxBet) return 'Maximum bet is ${_kMaxBet.toStringAsFixed(0)} SKR';
    return null;
  }

  bool get _canSubmit =>
      _amount >= _kMinBet &&
      _amount <= _kMaxBet &&
      _match.bettingOpen &&
      !_loading;

  Future<void> _placeBet() async {
    // 1. Validate amount
    final amountErr = _amountError;
    if (amountErr != null) {
      setState(() => _error = amountErr);
      return;
    }
    if (!_match.bettingOpen) {
      setState(() => _error = 'Betting is currently closed for this match.');
      return;
    }

    // 2. Get fresh Privy JWT — retry once if null (token might need refresh)
    String? privyJwt = await ref.read(privyServiceProvider).getAccessToken();
    if ((privyJwt == null || privyJwt.isEmpty) && mounted) {
      // Give Privy one more second and retry
      await Future.delayed(const Duration(seconds: 1));
      privyJwt = await ref.read(privyServiceProvider).getAccessToken();
    }
    if (privyJwt == null || privyJwt.isEmpty) {
      if (mounted) setState(() => _error = 'Session expired — please log out and log in again.');
      return;
    }

    setState(() {
      _loading = true;
      _error   = null;
    });

    try {
      final bet = await ref.read(betProvider.notifier).placeBet(
        matchId:   _match.id,
        fighterId: _selectedId,
        amount:    _amount,
        side:      _selectedSide,
        privyJwt:  privyJwt!,
      );
      if (bet != null && mounted) {
        HapticFeedback.heavyImpact();
        setState(() {
          _confirmed = true;
          _placedBet = bet;
        });
      }
    } on ApiException catch (e) {
      if (mounted) setState(() => _error = e.message);
    } catch (e) {
      if (mounted) {
        final msg = e.toString();
        // Surface human-readable backend messages (e.g. "Betting is closed")
        final clean = msg.replaceFirst('Exception: ', '');
        setState(() => _error = clean);
      }
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final bottom = MediaQuery.of(context).padding.bottom;

    return AnimatedSwitcher(
      duration: const Duration(milliseconds: 350),
      switchInCurve:  Curves.easeOut,
      switchOutCurve: Curves.easeIn,
      transitionBuilder: (child, animation) => FadeTransition(
        opacity: animation,
        child: SlideTransition(
          position: Tween<Offset>(
            begin: const Offset(0, 0.04),
            end: Offset.zero,
          ).animate(animation),
          child: child,
        ),
      ),
      child: _confirmed && _placedBet != null
          ? BetConfirmation(key: const ValueKey('confirm'), bet: _placedBet!)
          : _buildForm(bottom),
    );
  }

  Widget _buildForm(double bottom) {
    final bettingClosed = !_match.bettingOpen;

    return Container(
      key: const ValueKey('form'),
      padding: EdgeInsets.only(left: 24, right: 24, top: 20, bottom: bottom + 24),
      decoration: const BoxDecoration(
        color: Palette.sheetBg,
        borderRadius: BorderRadius.vertical(top: Radius.circular(16)),
      ),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          // Drag handle
          Container(
            width: 40, height: 4,
            decoration: BoxDecoration(
              color: Palette.muted,
              borderRadius: BorderRadius.circular(2),
            ),
          ),
          const SizedBox(height: 18),

          // Title row
          Row(
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            children: [
              Text('Place Your Bet', style: displayStyle(size: 24)),
              // Live / Closed badge
              Container(
                padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
                decoration: BoxDecoration(
                  color: bettingClosed
                      ? const Color(0xFF3B1111)
                      : const Color(0xFF0D2B1F),
                  borderRadius: BorderRadius.circular(20),
                  border: Border.all(
                    color: bettingClosed
                        ? const Color(0xFFFF5B5B)
                        : const Color(0xFF39D98A),
                    width: 0.8,
                  ),
                ),
                child: Row(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    Container(
                      width: 6, height: 6,
                      decoration: BoxDecoration(
                        shape: BoxShape.circle,
                        color: bettingClosed
                            ? const Color(0xFFFF5B5B)
                            : const Color(0xFF39D98A),
                      ),
                    ),
                    const SizedBox(width: 5),
                    Text(
                      bettingClosed ? 'Closed' : 'Betting Open',
                      style: TextStyle(
                        fontSize: 10,
                        fontWeight: FontWeight.w700,
                        color: bettingClosed
                            ? const Color(0xFFFF5B5B)
                            : const Color(0xFF39D98A),
                      ),
                    ),
                  ],
                ),
              ),
            ],
          ),
          const SizedBox(height: 4),
          Align(
            alignment: Alignment.centerLeft,
            child: Text(
              'Bets are placed on-chain with SKR · gas is free',
              style: bodyStyle(size: 11, color: Palette.muted),
            ),
          ),
          const SizedBox(height: 18),

          // ── Fighter selector ──────────────────────────────────────────
          Row(
            children: [
              Expanded(
                child: _FighterCard(
                  name: _match.fighter1.name,
                  odds: _match.odds.fighter1Odds,
                  poolPct: _match.odds.fighter1PoolPct,
                  side: 'A',
                  selected: _selectedFighter == 0,
                  onTap: bettingClosed ? null : () => setState(() => _selectedFighter = 0),
                ),
              ),
              const SizedBox(width: 10),
              Expanded(
                child: _FighterCard(
                  name: _match.fighter2.name,
                  odds: _match.odds.fighter2Odds,
                  poolPct: _match.odds.fighter2PoolPct,
                  side: 'B',
                  selected: _selectedFighter == 1,
                  onTap: bettingClosed ? null : () => setState(() => _selectedFighter = 1),
                ),
              ),
            ],
          ),
          const SizedBox(height: 16),

          // ── Quick-amount chips ────────────────────────────────────────
          Row(
            children: _kQuickAmounts.map((q) {
              return Expanded(
                child: Padding(
                  padding: const EdgeInsets.symmetric(horizontal: 3),
                  child: Pressable(
                    onTap: bettingClosed
                        ? null
                        : () {
                            HapticFeedback.selectionClick();
                            _amountController.text = q.toStringAsFixed(0);
                            setState(() {});
                          },
                    child: Container(
                      height: 32,
                      decoration: BoxDecoration(
                        border: Border.all(color: Palette.border),
                        borderRadius: BorderRadius.circular(4),
                        color: _amount == q
                            ? Palette.darkGold
                            : Colors.transparent,
                      ),
                      alignment: Alignment.center,
                      child: Text(
                        '${q.toStringAsFixed(0)}',
                        style: bodyStyle(
                          size: 12,
                          color: _amount == q ? Palette.gold : Palette.secondary,
                          weight: FontWeight.w600,
                        ),
                      ),
                    ),
                  ),
                ),
              );
            }).toList(),
          ),
          const SizedBox(height: 10),

          // ── Amount input ──────────────────────────────────────────────
          TextField(
            controller: _amountController,
            enabled: !bettingClosed,
            keyboardType: const TextInputType.numberWithOptions(decimal: true),
            style: bodyStyle(size: 20, weight: FontWeight.w600),
            decoration: InputDecoration(
              labelText: 'Amount (SKR)',
              labelStyle: bodyStyle(size: 13, color: Palette.muted),
              suffixText: 'SKR',
              suffixStyle: bodyStyle(size: 14, color: Palette.muted),
              helperText: 'Min $_kMinBet · Max ${_kMaxBet.toStringAsFixed(0)}',
              helperStyle: bodyStyle(size: 10, color: Palette.muted),
              errorText: _amountError,
              errorStyle: bodyStyle(size: 10, color: Palette.red),
              enabledBorder: const OutlineInputBorder(
                borderSide: BorderSide(color: Palette.border),
              ),
              focusedBorder: const OutlineInputBorder(
                borderSide: BorderSide(color: Palette.gold),
              ),
              disabledBorder: const OutlineInputBorder(
                borderSide: BorderSide(color: Palette.border),
              ),
              errorBorder: const OutlineInputBorder(
                borderSide: BorderSide(color: Palette.red),
              ),
              focusedErrorBorder: const OutlineInputBorder(
                borderSide: BorderSide(color: Palette.red),
              ),
            ),
            onChanged: (_) => setState(() {}),
          ),
          const SizedBox(height: 12),

          // ── Cost breakdown ────────────────────────────────────────────
          if (_amount >= _kMinBet)
            _CostBreakdown(match: _match, amount: _amount, selectedFighter: _selectedFighter),

          // ── API / auth error ──────────────────────────────────────────
          if (_error != null) ...[
            const SizedBox(height: 8),
            Container(
              width: double.infinity,
              padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
              decoration: BoxDecoration(
                color: const Color(0xFF3B1111),
                borderRadius: BorderRadius.circular(6),
                border: Border.all(color: Palette.red.withOpacity(0.5)),
              ),
              child: Row(
                children: [
                  const Icon(Icons.error_outline, color: Palette.red, size: 14),
                  const SizedBox(width: 8),
                  Expanded(
                    child: Text(_error!,
                        style: bodyStyle(size: 12, color: Palette.red)),
                  ),
                ],
              ),
            ),
          ],
          const SizedBox(height: 16),

          // ── Confirm button ───────────────────────────────────────────
          SizedBox(
            width: double.infinity,
            height: 52,
            child: ElevatedButton(
              onPressed: _canSubmit ? _placeBet : null,
              style: ElevatedButton.styleFrom(
                backgroundColor: Palette.gold,
                foregroundColor: Palette.black,
                disabledBackgroundColor: Palette.border,
                shape: RoundedRectangleBorder(
                    borderRadius: BorderRadius.circular(6)),
              ),
              child: _loading
                  ? const SizedBox(
                      width: 22, height: 22,
                      child: CircularProgressIndicator(
                        strokeWidth: 2.5,
                        color: Colors.black,
                      ),
                    )
                  : bettingClosed
                      ? Text('Betting Closed',
                          style: bodyStyle(
                              size: 15,
                              color: Palette.muted,
                              weight: FontWeight.w600))
                      : Text(
                          'Bet ${_amount.toStringAsFixed(2)} SKR on $_selectedName',
                          style: bodyStyle(
                              size: 15,
                              color: Palette.black,
                              weight: FontWeight.w600),
                        ),
            ),
          ),
        ],
      ),
    );
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Cost Breakdown
// ─────────────────────────────────────────────────────────────────────────────

class _CostBreakdown extends StatelessWidget {
  const _CostBreakdown({
    required this.match,
    required this.amount,
    required this.selectedFighter,
  });

  final Match match;
  final double amount;
  final int selectedFighter;

  // Use the pool % from backend (already accurate from on-chain totals).
  double get _poolPctMySide =>
      selectedFighter == 0 ? match.odds.fighter1PoolPct : match.odds.fighter2PoolPct;

  double get _poolPctOther =>
      selectedFighter == 0 ? match.odds.fighter2PoolPct : match.odds.fighter1PoolPct;

  double get _mySidePool => match.totalPool * _poolPctMySide;
  double get _otherPool  => match.totalPool * _poolPctOther;

  double get _totalPoolAfter => match.totalPool + amount;
  double get _mySideAfter    => _mySidePool + amount;

  /// Parimutuel payout: (totalPool_after × 0.95) × (myBet / mySideAfter)
  double get _estimatedPayout {
    if (_mySideAfter <= 0) return 0;
    return (_totalPoolAfter * (1 - _kFeePct)) * (amount / _mySideAfter);
  }

  double get _fee    => amount * _kFeePct;
  double get _profit => _estimatedPayout - amount;

  @override
  Widget build(BuildContext context) {
    final profitColor = _profit >= 0
        ? const Color(0xFF39D98A)
        : const Color(0xFFFF5B5B);

    final poolA = selectedFighter == 0 ? _mySidePool : _otherPool;
    final poolB = selectedFighter == 0 ? _otherPool  : _mySidePool;
    final totalDisplay = poolA + poolB;
    final ratioA = totalDisplay > 0 ? poolA / totalDisplay : 0.5;

    return Container(
      margin: const EdgeInsets.only(bottom: 4),
      padding: const EdgeInsets.all(14),
      decoration: BoxDecoration(
        color: const Color(0xFF12121F),
        borderRadius: BorderRadius.circular(8),
        border: Border.all(color: const Color(0xFF2D2D4E)),
      ),
      child: Column(
        children: [
          // ── Three-cell summary ───────────────────────────────────────
          Row(
            children: [
              _Cell(label: 'Your Bet', value: '${amount.toStringAsFixed(2)} SKR'),
              const _Sep(),
              _Cell(label: '5% Fee (est.)', value: '${_fee.toStringAsFixed(2)} SKR', muted: true),
              const _Sep(),
              _Cell(label: 'Est. Payout', value: '${_estimatedPayout.toStringAsFixed(2)} SKR', green: true),
            ],
          ),
          const SizedBox(height: 8),

          // ── Profit line ──────────────────────────────────────────────
          Text(
            _profit >= 0
                ? '+${_profit.toStringAsFixed(2)} SKR profit if you win'
                : '${_profit.toStringAsFixed(2)} SKR net if you win',
            style: TextStyle(fontSize: 11, color: profitColor, fontWeight: FontWeight.w600),
          ),
          const SizedBox(height: 10),

          // ── Pool bar — uses ACCURATE pool percentages from backend ────
          Tooltip(
            message: 'Current pool split before your bet',
            child: ClipRRect(
              borderRadius: BorderRadius.circular(4),
              child: Row(
                children: [
                  Flexible(
                    flex: (ratioA * 100).round().clamp(1, 99),
                    child: Container(height: 6, color: const Color(0xFFFFD700)),
                  ),
                  Flexible(
                    flex: ((1 - ratioA) * 100).round().clamp(1, 99),
                    child: Container(height: 6, color: const Color(0xFF8B8BFF)),
                  ),
                ],
              ),
            ),
          ),
          const SizedBox(height: 4),
          Row(
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            children: [
              Text('Side A  ${poolA.toStringAsFixed(1)} SKR',
                  style: const TextStyle(fontSize: 10, color: Color(0xFFFFD700))),
              Text('Total  ${match.totalPool.toStringAsFixed(1)} SKR',
                  style: TextStyle(fontSize: 10, color: Colors.white.withOpacity(0.4))),
              Text('Side B  ${poolB.toStringAsFixed(1)} SKR',
                  style: const TextStyle(fontSize: 10, color: Color(0xFF8B8BFF))),
            ],
          ),
          const SizedBox(height: 10),

          // ── Gas badge ─────────────────────────────────────────────────
          Container(
            padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
            decoration: BoxDecoration(
              color: const Color(0xFF0D2B1F),
              borderRadius: BorderRadius.circular(20),
              border: Border.all(color: const Color(0xFF39D98A), width: 0.8),
            ),
            child: const Row(
              mainAxisSize: MainAxisSize.min,
              children: [
                Icon(Icons.bolt, size: 12, color: Color(0xFF39D98A)),
                SizedBox(width: 4),
                Text('Gas: FREE — sponsored by platform',
                    style: TextStyle(
                        fontSize: 10,
                        color: Color(0xFF39D98A),
                        fontWeight: FontWeight.w600)),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

class _Cell extends StatelessWidget {
  const _Cell({required this.label, required this.value, this.muted = false, this.green = false});
  final String label;
  final String value;
  final bool muted;
  final bool green;

  @override
  Widget build(BuildContext context) {
    return Expanded(
      child: Column(
        children: [
          Text(label,
              style: TextStyle(fontSize: 10, color: Colors.white.withOpacity(0.45))),
          const SizedBox(height: 2),
          Text(
            value,
            textAlign: TextAlign.center,
            style: TextStyle(
              fontSize: 12,
              fontWeight: FontWeight.w700,
              color: green
                  ? const Color(0xFF39D98A)
                  : muted
                      ? Colors.white.withOpacity(0.35)
                      : Colors.white,
            ),
          ),
        ],
      ),
    );
  }
}

class _Sep extends StatelessWidget {
  const _Sep();
  @override
  Widget build(BuildContext context) =>
      Container(width: 1, height: 28, color: Colors.white.withOpacity(0.08),
          margin: const EdgeInsets.symmetric(horizontal: 4));
}

// ─────────────────────────────────────────────────────────────────────────────
// Fighter card
// ─────────────────────────────────────────────────────────────────────────────

class _FighterCard extends StatelessWidget {
  const _FighterCard({
    required this.name,
    required this.odds,
    required this.poolPct,
    required this.side,
    required this.selected,
    required this.onTap,
  });

  final String name;
  final double odds;
  final double poolPct;
  final String side;
  final bool selected;
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) {
    return Pressable(
      onTap: onTap,
      haptic: true,
      child: AnimatedContainer(
        duration: const Duration(milliseconds: 200),
        curve: Curves.easeOut,
        padding: const EdgeInsets.symmetric(vertical: 14, horizontal: 10),
        decoration: BoxDecoration(
          border: Border.all(
            color: selected ? Palette.gold : Palette.border,
            width: selected ? 2 : 1,
          ),
          borderRadius: BorderRadius.circular(6),
          color: selected ? Palette.darkGold : Colors.transparent,
        ),
        child: Column(
          children: [
            // Side badge
            Container(
              padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
              decoration: BoxDecoration(
                color: selected ? Palette.gold : Palette.border,
                borderRadius: BorderRadius.circular(4),
              ),
              child: Text('Side $side',
                  style: bodyStyle(
                      size: 10,
                      color: selected ? Palette.black : Palette.muted,
                      weight: FontWeight.w700)),
            ),
            const SizedBox(height: 6),
            AnimatedDefaultTextStyle(
              duration: const Duration(milliseconds: 200),
              style: displayStyle(size: 13, color: selected ? Palette.gold : Palette.white),
              child: Text(name, textAlign: TextAlign.center, maxLines: 2,
                  overflow: TextOverflow.ellipsis),
            ),
            const SizedBox(height: 4),
            // Odds
            Text('${odds.toStringAsFixed(2)}×',
                style: bodyStyle(size: 13, color: Palette.secondary,
                    weight: FontWeight.w700)),
            const SizedBox(height: 2),
            // Pool share
            Text('${(poolPct * 100).toStringAsFixed(0)}% of pool',
                style: bodyStyle(size: 10, color: Palette.muted)),
          ],
        ),
      ),
    );
  }
}
