import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../core/palette.dart';
import '../../core/typography.dart';
import '../../models/match.dart';
import '../../models/bet.dart';
import '../../providers/auth_provider.dart';
import '../../providers/bet_provider.dart';
import '../shared/pressable.dart';
import 'bet_confirmation.dart';

class BetBottomSheet extends ConsumerStatefulWidget {
  const BetBottomSheet({super.key, required this.match});

  final Match match;

  @override
  ConsumerState<BetBottomSheet> createState() => _BetBottomSheetState();
}

class _BetBottomSheetState extends ConsumerState<BetBottomSheet> {
  int _selectedFighter = 0; // 0 = fighter1 (side A), 1 = fighter2 (side B)
  final _amountController = TextEditingController(text: '1.0');
  bool _loading = false;
  bool _confirmed = false;
  Bet? _placedBet;
  String? _error;

  @override
  void dispose() {
    _amountController.dispose();
    super.dispose();
  }

  double get _amount => double.tryParse(_amountController.text) ?? 0;

  double get _selectedOdds =>
      _selectedFighter == 0
          ? widget.match.odds.fighter1Odds
          : widget.match.odds.fighter2Odds;

  String get _selectedName =>
      _selectedFighter == 0
          ? widget.match.fighter1.name
          : widget.match.fighter2.name;

  String get _selectedId =>
      _selectedFighter == 0
          ? widget.match.fighter1.id
          : widget.match.fighter2.id;

  /// "A" for fighter1, "B" for fighter2
  String get _selectedSide => _selectedFighter == 0 ? 'A' : 'B';

  Future<void> _placeBet() async {
    // Fetch a fresh Privy access token for server-side signing
    final privyJwt = await ref.read(privyServiceProvider).getAccessToken();
    if (privyJwt == null || privyJwt.isEmpty) {
      setState(() => _error = 'Not authenticated — please log in again.');
      return;
    }

    setState(() {
      _loading = true;
      _error = null;
    });

    try {
      final bet = await ref.read(betProvider.notifier).placeBet(
        matchId: widget.match.id,
        fighterId: _selectedId,
        amount: _amount,
        side: _selectedSide,
        privyJwt: privyJwt,
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
    final bottom = MediaQuery.of(context).padding.bottom;

    return AnimatedSwitcher(
      duration: const Duration(milliseconds: 400),
      switchInCurve: Curves.easeOut,
      switchOutCurve: Curves.easeIn,
      transitionBuilder: (child, animation) => FadeTransition(
        opacity: animation,
        child: SlideTransition(
          position: Tween<Offset>(
            begin: const Offset(0, 0.05),
            end: Offset.zero,
          ).animate(animation),
          child: child,
        ),
      ),
      child: _confirmed && _placedBet != null
          ? BetConfirmation(
              key: const ValueKey('confirm'),
              bet: _placedBet!,
            )
          : _buildForm(wallet, bottom),
    );
  }

  Widget _buildForm(dynamic wallet, double bottom) {
    return Container(
      key: const ValueKey('form'),
      padding: EdgeInsets.only(
          left: 24, right: 24, top: 24, bottom: bottom + 24),
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
          const SizedBox(height: 20),
          Text('Place Your Bet', style: displayStyle(size: 28)),
          const SizedBox(height: 4),
          Text('Bets are placed on-chain with SKR',
              style: bodyStyle(size: 12, color: Palette.muted)),
          const SizedBox(height: 20),
          // Fighter selection — A = fighter1, B = fighter2
          Row(
            children: [
              Expanded(
                child: _FighterCard(
                  name: widget.match.fighter1.name,
                  odds: widget.match.odds.fighter1Odds,
                  side: 'A',
                  selected: _selectedFighter == 0,
                  onTap: () => setState(() => _selectedFighter = 0),
                ),
              ),
              const SizedBox(width: 12),
              Expanded(
                child: _FighterCard(
                  name: widget.match.fighter2.name,
                  odds: widget.match.odds.fighter2Odds,
                  side: 'B',
                  selected: _selectedFighter == 1,
                  onTap: () => setState(() => _selectedFighter = 1),
                ),
              ),
            ],
          ),
          const SizedBox(height: 20),
          // Amount input
          TextField(
            controller: _amountController,
            keyboardType: const TextInputType.numberWithOptions(decimal: true),
            style: bodyStyle(size: 18),
            decoration: InputDecoration(
              labelText: 'Amount (SKR)',
              labelStyle: bodyStyle(size: 14, color: Palette.muted),
              enabledBorder: const OutlineInputBorder(
                borderSide: BorderSide(color: Palette.border),
              ),
              focusedBorder: const OutlineInputBorder(
                borderSide: BorderSide(color: Palette.gold),
              ),
            ),
            onChanged: (_) => setState(() {}),
          ),
          const SizedBox(height: 12),
          // Payout preview
          Row(
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            children: [
              Text('Potential Payout',
                  style: bodyStyle(size: 14, color: Palette.muted)),
              Text('${(_amount * _selectedOdds).toStringAsFixed(2)} SKR',
                  style: bodyStyle(size: 14, color: Palette.green)),
            ],
          ),
          const SizedBox(height: 4),
          Row(
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            children: [
              Text('Platform Fee', style: bodyStyle(size: 14, color: Palette.muted)),
              Text('5%', style: bodyStyle(size: 14, color: Palette.muted)),
            ],
          ),
          if (_error != null) ...[
            const SizedBox(height: 8),
            Text(_error!, style: bodyStyle(size: 12, color: Palette.red)),
          ],
          const SizedBox(height: 20),
          // Confirm button
          SizedBox(
            width: double.infinity,
            height: 50,
            child: ElevatedButton(
              onPressed: !_loading && _amount > 0 ? _placeBet : null,
              style: ElevatedButton.styleFrom(
                backgroundColor: Palette.gold,
                foregroundColor: Palette.black,
                disabledBackgroundColor: Palette.border,
                shape: RoundedRectangleBorder(
                    borderRadius: BorderRadius.circular(4)),
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
                  : Text('Confirm Bet on $_selectedName (Side $_selectedSide)',
                      style: bodyStyle(
                          size: 15,
                          color: Palette.black,
                          weight: FontWeight.w600)),
            ),
          ),
        ],
      ),
    );
  }
}

class _FighterCard extends StatelessWidget {
  const _FighterCard({
    required this.name,
    required this.odds,
    required this.side,
    required this.selected,
    required this.onTap,
  });

  final String name;
  final double odds;
  final String side;
  final bool selected;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return Pressable(
      onTap: onTap,
      haptic: true,
      child: AnimatedContainer(
        duration: const Duration(milliseconds: 200),
        curve: Curves.easeOut,
        padding: const EdgeInsets.symmetric(vertical: 16, horizontal: 12),
        decoration: BoxDecoration(
          border: Border.all(
            color: selected ? Palette.gold : Palette.border,
            width: selected ? 2 : 1,
          ),
          borderRadius: BorderRadius.circular(4),
          color: selected ? Palette.darkGold : Colors.transparent,
        ),
        child: Column(
          children: [
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
              style: displayStyle(
                  size: 15,
                  color: selected ? Palette.gold : Palette.white),
              child: Text(name, textAlign: TextAlign.center),
            ),
            const SizedBox(height: 4),
            Text('${odds.toStringAsFixed(1)}×',
                style: bodyStyle(size: 14, color: Palette.secondary)),
          ],
        ),
      ),
    );
  }
}
