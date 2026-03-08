import 'package:flutter/material.dart';
import '../../core/palette.dart';
import '../../core/typography.dart';
import '../shared/pressable.dart';

enum MatchHistoryResultFilter { all, won, loss }

extension MatchHistoryResultFilterLabel on MatchHistoryResultFilter {
  String get label {
    switch (this) {
      case MatchHistoryResultFilter.all:
        return 'All';
      case MatchHistoryResultFilter.won:
        return 'Won';
      case MatchHistoryResultFilter.loss:
        return 'Loss';
    }
  }
}

Map<String, String> buildMatchHistoryOpponentOptions(
  List<Map<String, dynamic>> matches,
) {
  final options = <String, String>{};
  for (final match in matches) {
    final opponentId = match['opponent_id'] as String?;
    final opponentName = (match['opponent_name'] as String?)?.trim() ?? '';
    if (opponentId != null && opponentName.isNotEmpty) {
      options[opponentId] = opponentName;
    }
  }
  return options;
}

List<Map<String, dynamic>> filterMatchHistory(
  List<Map<String, dynamic>> matches, {
  MatchHistoryResultFilter resultFilter = MatchHistoryResultFilter.all,
  String? opponentId,
  bool nonZeroPoolOnly = false,
}) {
  return matches.where((match) {
    if (nonZeroPoolOnly && _asDouble(match['total_bet_amount']) <= 0) {
      return false;
    }
    if (resultFilter == MatchHistoryResultFilter.won &&
        match['result'] != 'WIN') {
      return false;
    }
    if (resultFilter == MatchHistoryResultFilter.loss &&
        match['result'] != 'LOSS') {
      return false;
    }
    if (opponentId != null && match['opponent_id'] != opponentId) {
      return false;
    }
    return true;
  }).toList();
}

class MatchHistoryFilters extends StatelessWidget {
  const MatchHistoryFilters({
    super.key,
    required this.resultFilter,
    required this.opponentId,
    required this.opponentOptions,
    required this.onResultChanged,
    required this.onOpponentChanged,
    this.nonZeroPoolOnly = false,
    this.onNonZeroPoolOnlyChanged,
  });

  final MatchHistoryResultFilter resultFilter;
  final String? opponentId;
  final Map<String, String> opponentOptions;
  final ValueChanged<MatchHistoryResultFilter> onResultChanged;
  final ValueChanged<String?> onOpponentChanged;
  final bool nonZeroPoolOnly;
  final ValueChanged<bool>? onNonZeroPoolOnlyChanged;

  @override
  Widget build(BuildContext context) {
    final opponentLabel = opponentId == null
        ? 'VS Anyone'
        : 'VS ${opponentOptions[opponentId] ?? 'Unknown'}';

    return Wrap(
      spacing: 10,
      runSpacing: 10,
      children: [
        ...MatchHistoryResultFilter.values.map(
          (filter) => _HistoryFilterChip(
            label: filter.label,
            selected: resultFilter == filter,
            onTap: () => onResultChanged(filter),
          ),
        ),
        if (opponentOptions.isNotEmpty)
          PopupMenuButton<String?>(
            color: Palette.cardBg,
            onSelected: onOpponentChanged,
            itemBuilder: (context) => [
              const PopupMenuItem<String?>(
                value: null,
                child: Text('VS Anyone'),
              ),
              ...opponentOptions.entries.map(
                (entry) => PopupMenuItem<String?>(
                  value: entry.key,
                  child: Text('VS ${entry.value}'),
                ),
              ),
            ],
            child: _HistoryFilterChip(
              label: opponentLabel,
              selected: opponentId != null,
              trailing: const Icon(
                Icons.keyboard_arrow_down,
                size: 16,
                color: Palette.statLabel,
              ),
            ),
          ),
        if (onNonZeroPoolOnlyChanged != null)
          _HistoryFilterChip(
            label: 'Non-zero Pool',
            selected: nonZeroPoolOnly,
            onTap: () => onNonZeroPoolOnlyChanged!.call(!nonZeroPoolOnly),
          ),
      ],
    );
  }
}

class MatchHistoryCard extends StatelessWidget {
  const MatchHistoryCard({
    super.key,
    required this.match,
    required this.fighterName,
    required this.tokenSymbol,
  });

  final Map<String, dynamic> match;
  final String fighterName;
  final String tokenSymbol;

  @override
  Widget build(BuildContext context) {
    final isWin = match['result'] == 'WIN';
    final opponent = match['opponent_name'] as String? ?? 'Unknown';
    final side = match['side'] as String? ?? '-';
    final roundsWon = _asInt(match['rounds_won']);
    final roundsLost = _asInt(match['rounds_lost']);
    final totalBetAmount = _asDouble(match['total_bet_amount']);
    final betForFighter = _asDouble(match['bet_amount_for_fighter']);
    final betForOpponent = _asDouble(match['bet_amount_for_opponent']);
    final resultColor = isWin ? Palette.green : Palette.red;

    return Container(
      width: double.infinity,
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 11),
      decoration: BoxDecoration(
        borderRadius: BorderRadius.circular(2),
        color: Palette.cardBg.withValues(alpha: 0.18),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      'vs $opponent',
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: displayStyle(size: 18, color: Palette.white),
                    ),
                    const SizedBox(height: 3),
                    Text(
                      _formatDate(match['completed_at'] as String?),
                      style: bodyStyle(size: 11, color: Palette.statLabel),
                    ),
                  ],
                ),
              ),
              const SizedBox(width: 10),
              Column(
                crossAxisAlignment: CrossAxisAlignment.end,
                children: [
                  Text(
                    isWin ? 'WON' : 'LOSS',
                    style: bodyStyle(
                      size: 11,
                      color: resultColor,
                      weight: FontWeight.w600,
                    ),
                  ),
                  const SizedBox(height: 3),
                  Text(
                    '${_formatAmount(totalBetAmount)} $tokenSymbol',
                    style: bodyStyle(size: 16, color: Palette.gold),
                  ),
                ],
              ),
            ],
          ),
          const SizedBox(height: 9),
          Wrap(
            spacing: 8,
            runSpacing: 8,
            children: [
              _MetaTag(label: 'Score', value: '$roundsWon-$roundsLost'),
              _MetaTag(label: 'Side', value: side),
              _MetaTag(
                label: fighterName,
                value: '${_formatAmount(betForFighter)} $tokenSymbol',
              ),
              _MetaTag(
                label: opponent,
                value: '${_formatAmount(betForOpponent)} $tokenSymbol',
              ),
            ],
          ),
        ],
      ),
    );
  }
}

class _HistoryFilterChip extends StatelessWidget {
  const _HistoryFilterChip({
    required this.label,
    required this.selected,
    this.onTap,
    this.trailing,
  });

  final String label;
  final bool selected;
  final VoidCallback? onTap;
  final Widget? trailing;

  @override
  Widget build(BuildContext context) {
    final child = Container(
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
      decoration: BoxDecoration(
        borderRadius: BorderRadius.circular(2),
        color: selected
            ? Palette.gold.withValues(alpha: 0.12)
            : Palette.cardBg.withValues(alpha: 0.28),
        border: Border.all(
          color: selected
              ? Palette.gold.withValues(alpha: 0.55)
              : Palette.border.withValues(alpha: 0.7),
        ),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Text(
            label,
            style: bodyStyle(
              size: 13,
              color: selected ? Palette.gold : Palette.white,
            ),
          ),
          if (trailing != null) ...[
            const SizedBox(width: 6),
            trailing!,
          ],
        ],
      ),
    );

    if (onTap == null) return child;
    return Pressable(onTap: onTap, child: child);
  }
}

class _MetaTag extends StatelessWidget {
  const _MetaTag({required this.label, required this.value});

  final String label;
  final String value;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 6),
      decoration: BoxDecoration(
        borderRadius: BorderRadius.circular(2),
        color: Palette.sheetBg.withValues(alpha: 0.52),
      ),
      child: RichText(
        text: TextSpan(
          style: bodyStyle(size: 11, color: Palette.statLabel),
          children: [
            TextSpan(text: '$label '),
            TextSpan(
              text: value,
              style: bodyStyle(size: 11, color: Palette.white),
            ),
          ],
        ),
      ),
    );
  }
}

int _asInt(dynamic value) {
  if (value is int) return value;
  if (value is num) return value.toInt();
  if (value is String) return int.tryParse(value) ?? 0;
  return 0;
}

double _asDouble(dynamic value) {
  if (value is double) return value;
  if (value is num) return value.toDouble();
  if (value is String) return double.tryParse(value) ?? 0.0;
  return 0.0;
}

String _formatAmount(double value) {
  final safe = value.isFinite ? value : 0.0;
  if (safe == safe.truncateToDouble()) {
    return safe.toStringAsFixed(0);
  }
  return safe.toStringAsFixed(2);
}

String _formatDate(String? iso) {
  if (iso == null) return '';
  try {
    final dt = DateTime.parse(iso).toLocal();
    final hour = dt.hour % 12 == 0 ? 12 : dt.hour % 12;
    final minute = dt.minute.toString().padLeft(2, '0');
    final suffix = dt.hour >= 12 ? 'PM' : 'AM';
    return '${dt.day}/${dt.month}/${dt.year} · $hour:$minute $suffix';
  } catch (_) {
    return '';
  }
}
