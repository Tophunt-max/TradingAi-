import { ActivityIndicator, View } from 'react-native';
import Animated, { FadeIn } from 'react-native-reanimated';
import { AppText } from '../../components/ui';
import { useTheme } from '../../components/theme/ThemeProvider';
import { useShouldReduceMotion } from '../../hooks/useMotion';
import { BrandMark } from './BrandMark';

export function SplashView() {
  const theme = useTheme();
  const reduce = useShouldReduceMotion();
  return (
    <View testID="splash" style={{ flex: 1, backgroundColor: theme.colors.backgroundDeep, alignItems: 'center', justifyContent: 'center', gap: 16 }}>
      <Animated.View entering={reduce ? undefined : FadeIn.duration(400)} style={{ alignItems: 'center', gap: 12 }}>
        <BrandMark size={72} />
        <AppText variant="display">{'QuantAI F&O'}</AppText>
        <AppText tone="muted">Smarter Analysis. Better Decisions. Higher Consistency.</AppText>
      </Animated.View>
      <ActivityIndicator color={theme.colors.primary} accessibilityLabel="Loading" />
    </View>
  );
}
