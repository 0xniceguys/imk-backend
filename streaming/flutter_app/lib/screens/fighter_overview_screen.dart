import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../router.dart';
import '../providers/fighter_provider.dart';
import '../widgets/shared/app_shell.dart';
import '../widgets/shared/ik_loader.dart';
import '../widgets/fighter/fighter_carousel.dart';

class FighterOverviewScreen extends ConsumerWidget {
  const FighterOverviewScreen({super.key, required this.onNavigate});
  final void Function(String) onNavigate;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final fighters = ref.watch(fighterProvider);
    return AppShell(
      activeTab: NavTab.fighters,
      scrollable: true,
      contentBottomPadding: 180,
      onNavigate: (slug) => onNavigate(routeFor(slug)),
      content: fighters.isEmpty
          ? const SizedBox(
              height: 560,
              child: Center(child: IKLoader(size: 40)),
            )
          : FighterCarousel(
              fighters: fighters,
              onMoreDetails: (id) => onNavigate('/fighter-details/$id'),
            ),
    );
  }
}
