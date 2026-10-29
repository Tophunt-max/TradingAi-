// Minimal Reanimated stand-in covering the APIs this app uses. The package's own
// mock initialises native CSS handlers that are unavailable under Jest.
jest.mock('react-native-reanimated', () => {
  const { View } = require('react-native');
  const animation = { duration: () => animation, delay: () => animation };
  return {
    __esModule: true,
    default: { View },
    FadeIn: animation,
    SlideInDown: animation,
    useSharedValue: (v) => ({ value: v }),
    useAnimatedStyle: (fn) => fn(),
    withTiming: (v) => v,
    withRepeat: (v) => v,
    useReducedMotion: () => false,
  };
});

// Icon fonts are loaded through the asset registry, which Jest does not provide.
jest.mock('@expo/vector-icons', () => {
  const { Text } = require('react-native');
  const Icon = ({ name }) => require('react').createElement(Text, { accessibilityElementsHidden: true }, name);
  return { Ionicons: Icon };
});
