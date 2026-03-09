import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import '../../core/palette.dart';
import '../../core/typography.dart';
import '../../models/bet.dart';

class BetConfirmation extends StatefulWidget {
  const BetConfirmation({super.key, required this.bet});

  final Bet bet;

  @override
  State<BetConfirmation> createState() => _BetConfirmationState();
}

class _BetConfirmationState extends State<BetConfirmation>
    with SingleTickerProviderStateMixin {
  late final AnimationController _ctrl;
  late final Animation<double> _checkScale;
  late final Animation<double> _contentFade;

  @override
  void initState() {
    super.initState();
    _ctrl = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 800),
    );
    _checkScale = Tween<double>(begin: 0.0, end: 1.0).animate(
      CurvedAnimation(
          parent: _ctrl,
          curve: const Interval(0, 0.5, curve: Curves.elasticOut)),
    );
    _contentFade = CurvedAnimation(
        parent: _ctrl,
        curve: const Interval(0.3, 0.8, curve: Curves.easeOut));
    HapticFeedback.mediumImpact();
    _ctrl.forward();
  }

  @override
  void dispose() {
    _ctrl.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final bet = widget.bet;
    final bottom = MediaQuery.of(context).padding.bottom;

    return Container(
      padding: EdgeInsets.only(
          left: 24, right: 24, top: 32, bottom: bottom + 24),
      decoration: const BoxDecoration(
        color: Palette.sheetBg,
        borderRadius: BorderRadius.vertical(top: Radius.circular(16)),
      ),
      child: AnimatedBuilder(
        animation: _ctrl,
        builder: (context, _) => Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            // Animated check icon — bounces in
            Transform.scale(
              scale: _checkScale.value,
              child: const Icon(Icons.check_circle,
                  color: Palette.green, size: 56),
            ),
            const SizedBox(height: 16),
            // Content fades in
            Opacity(
              opacity: _contentFade.value,
              child: Column(
                children: [
                  Text('Bet Placed!', style: displayStyle(size: 28)),
                  const SizedBox(height: 16),
                  Text(
                    '${bet.amount.toStringAsFixed(1)} ${bet.currency} on ${bet.fighterName}',
                    style: bodyStyle(size: 18, color: Palette.gold),
                  ),
                  const SizedBox(height: 8),
                  Text(
                    'vs ${bet.opponentName}',
                    style: bodyStyle(size: 14, color: Palette.muted),
                  ),
                  const SizedBox(height: 16),
                  Row(
                    mainAxisAlignment: MainAxisAlignment.spaceBetween,
                    children: [
                      Text('Potential Payout',
                          style:
                              bodyStyle(size: 14, color: Palette.muted)),
                      Text(
                          '${(bet.amount * bet.oddsAtPlacement).toStringAsFixed(2)} ${bet.currency}',
                          style: bodyStyle(
                              size: 14, color: Palette.green)),
                    ],
                  ),
                  const SizedBox(height: 8),
                  if (bet.txSignature != null)
                    Row(
                      mainAxisAlignment:
                          MainAxisAlignment.spaceBetween,
                      children: [
                        Text('Transaction',
                            style: bodyStyle(
                                size: 14, color: Palette.muted)),
                        Text(
                          '${bet.txSignature!.substring(0, bet.txSignature!.length.clamp(0, 8))}...',
                          style: bodyStyle(
                              size: 14, color: Palette.secondary),
                        ),
                      ],
                    ),
                  const SizedBox(height: 24),
                  SizedBox(
                    width: double.infinity,
                    height: 50,
                    child: ElevatedButton(
                      onPressed: () => Navigator.of(context).pop(),
                      style: ElevatedButton.styleFrom(
                        backgroundColor: Palette.darkGold,
                        foregroundColor: Palette.white,
                        shape: RoundedRectangleBorder(
                            borderRadius: BorderRadius.circular(4)),
                      ),
                      child: Text('Back to Match',
                          style: bodyStyle(
                              size: 16, weight: FontWeight.w600)),
                    ),
                  ),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }
}
