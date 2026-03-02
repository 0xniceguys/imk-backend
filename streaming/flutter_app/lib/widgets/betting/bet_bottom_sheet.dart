import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../core/palette.dart';
import '../../core/typography.dart';
import '../../models/match.dart';
import '../../models/bet.dart';
import '../../providers/wallet_provider.dart';
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
  int _selectedFighter = 0; // 0 = fighter1, 1 = fighter2
  final _amountController = TextEditingController(text: '1.0');
  bool _confirmed = false;
  Bet? _placedBet;

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

  Future<void> _placeBet() async {
    final bet = await ref.read(betProvider.notifier).placeBet(
      matchId: widget.match.id,
      fighterId: _selectedId,
      amount: _amount,
    );
    if (bet != null) {
      setState(() {
        _confirmed = true;
        _placedBet = bet;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final wallet = ref.watch(walletProvider);
    final bottom = MediaQuery.of(context).padding.bottom;

    // Animated swap between bet form and confirmation
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
          ? BetConfirmation(key: const ValueKey('confirm'), bet: _placedBet!)
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
          const SizedBox(height: 20),
          // Fighter selection
          Row(
            children: [
              Expanded(
                child: _FighterCard(
                  name: widget.match.fighter1.name,
                  odds: widget.match.odds.fighter1Odds,
                  selected: _selectedFighter == 0,
                  onTap: () => setState(() => _selectedFighter = 0),
                ),
              ),
              const SizedBox(width: 12),
              Expanded(
                child: _FighterCard(
                  name: widget.match.fighter2.name,
                  odds: widget.match.odds.fighter2Odds,
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
              labelText: 'Amount (SOL)',
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
              Text('${(_amount * _selectedOdds).toStringAsFixed(2)} SOL',
                  style: bodyStyle(size: 14, color: Palette.green)),
            ],
          ),
          const SizedBox(height: 4),
          Row(
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            children: [
              Text('Wallet Balance',
                  style: bodyStyle(size: 14, color: Palette.muted)),
              Text('${wallet.solBalance.toStringAsFixed(1)} SOL',
                  style: bodyStyle(size: 14)),
            ],
          ),
          const SizedBox(height: 20),
          // Confirm button
          SizedBox(
            width: double.infinity,
            height: 50,
            child: ElevatedButton(
              onPressed: _amount > 0 && _amount <= wallet.solBalance
                  ? _placeBet
                  : null,
              style: ElevatedButton.styleFrom(
                backgroundColor: Palette.gold,
                foregroundColor: Palette.black,
                disabledBackgroundColor: Palette.border,
                shape: RoundedRectangleBorder(
                    borderRadius: BorderRadius.circular(4)),
              ),
              child: Text('Confirm Bet on $_selectedName',
                  style: bodyStyle(
                      size: 16,
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
    required this.selected,
    required this.onTap,
  });

  final String name;
  final double odds;
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
            AnimatedDefaultTextStyle(
              duration: const Duration(milliseconds: 200),
              style: displayStyle(
                  size: 16,
                  color: selected ? Palette.gold : Palette.white),
              child: Text(name),
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
