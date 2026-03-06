const kMaxWidth = 420.0;
const kUseMockData = false;

// Runtime chain, program, mint and Privy values are fetched from
// GET /api/client-config. These are fallback-only values.
const kFallbackUseDevnet = false;

// Backend API — override with --dart-define for local device testing:
//   flutter run --dart-define=API_HOST=10.0.2.2:8000
const _apiHost = String.fromEnvironment('API_HOST', defaultValue: 'immortalkombat.mercle.ai');
final _isLocal = _apiHost.contains(':'); // true when a port is specified (local dev)
final kApiOrigin = '${_isLocal ? 'http' : 'https'}://$_apiHost';
final kApiBaseUrl = '${_isLocal ? 'http' : 'https'}://$_apiHost/api';
final kWsBaseUrl = '${_isLocal ? 'ws' : 'wss'}://$_apiHost';
// Base for non-API routes (e.g. /stream/audio/...)
final kStreamBaseUrl = '${_isLocal ? 'http' : 'https'}://$_apiHost';


// Privy credentials — App ID from dashboard.privy.io
const kFallbackPrivyAppId = 'cmm5ifxpw00p50cl5bkx86zcd';
const kFallbackPrivyClientId = 'client-WY6WiWvWLFu17WpAsHYy8EuUWhdVFdGhA3vKCeeAFnZ3s';
const kFallbackSkrMint = 'BGUuLGTZJ7nyhReCFWpC4nQf2APE4N6dY6hizj1DXivJ';
const kFallbackProgramId = 'CoTfhg7a9vjZMCCuvpxmnhSj9CzTAahxUvDutzZjRrth';
const kFallbackTokenSymbol = 'SKR';
const kFallbackTokenDecimals = 6;
const kFallbackExplorerBaseUrl = 'https://solscan.io';

class Assets {
  static const skullIcon = 'assets/icon/skullicon.svg';
  static const startBg = 'assets/figma/getstartedimage.png';
  static const startHero = 'assets/figma/startHeroGray.png';
  static const logoVector = 'assets/figma/logoVector.png';
  static const ctaTop = 'assets/figma/ctaTop.png';
  static const ctaBottom = 'assets/figma/ctaBottom.png';
  static const signInBg = 'assets/figma/signInBg.png';
  static const onboardingOne = 'assets/figma/onboardingOne.png';
  static const onboardingTwo = 'assets/figma/onboardingTwo.png';
  static const onboardingThree = 'assets/figma/onboardingThree.png';
  static const onboardingGlowOne = 'assets/figma/onboardingGlowOne.png';
  static const onboardingGlowTwo = 'assets/figma/onboardingGlowTwo.png';
  static const onboardingGlowThree = 'assets/figma/onboardingGlowThree.png';
  static const arenaTile = 'assets/figma/arenaTile.png';
  static const arenaTileAlt = 'assets/figma/arenaTileAlt.png';
  static const arenaSingle = 'assets/figma/arenaSingle.png';
  static const battleLeft = 'assets/figma/battleLeft.png';
  static const battleRight = 'assets/figma/battleRight.png';
  static const fighterLeft = 'assets/figma/fighterLeft.png';
  static const fighterCenter = 'assets/figma/fighterCenter.png';
  static const fighterRight = 'assets/figma/fighterRight.png';
  static const photoUnavailable = 'assets/figma/photounavailable.png';
  static const profileAvatar = 'assets/figma/profileAvatar.png';
  static const detailsHero = 'assets/figma/detailsHero.png';

  static const navActiveArena = 'assets/figma/ui/nav_active_arena.png';
  static const navInactiveArena = 'assets/figma/ui/nav_inactive_arena.png';
  static const navActiveFighters = 'assets/figma/ui/nav_active_fighters.png';
  static const navInactiveFighters =
      'assets/figma/ui/nav_inactive_fighters.png';
  static const navInactiveProfile = 'assets/figma/ui/nav_inactive_profile.png';
  static const balanceIcon = 'assets/figma/ui/balance_icon.png';
  static const stepperLeft = 'assets/figma/ui/stepper_left.png';
  static const stepperRight = 'assets/figma/ui/stepper_right.png';
  static const stepperFrame = 'assets/figma/ui/stepper_frame.png';
}
