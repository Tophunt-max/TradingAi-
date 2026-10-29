import { useEffect } from 'react';
import { View } from 'react-native';
import Animated, { useAnimatedStyle, useSharedValue, withRepeat, withTiming } from 'react-native-reanimated';
import { useTheme } from '../theme/ThemeProvider';
import { useShouldReduceMotion } from '../../hooks/useMotion';

export function Skeleton({ width = '100%', height = 14, radius, style }) {
  const theme = useTheme();
  const reduce = useShouldReduceMotion();
  const opacity = useSharedValue(0.5);

  useEffect(() => {
    if (!reduce) opacity.value = withRepeat(withTiming(1, { duration: 800 }), -1, true);
  }, [reduce, opacity]);

  const animated = useAnimatedStyle(() => ({ opacity: opacity.value }));
  return (
    <Animated.View
      accessibilityElementsHidden
      importantForAccessibility="no"
      style={[{ width, height, borderRadius: radius ?? theme.radii.sm, backgroundColor: theme.colors.surfaceRaised }, animated, style]}
    />
  );
}

export function SkeletonList({ rows = 4 }) {
  return (
    <View accessibilityLabel="Loading" accessibilityRole="progressbar" style={{ gap: 12 }}>
      {Array.from({ length: rows }).map((_, i) => (
        <View key={i} style={{ flexDirection: 'row', justifyContent: 'space-between' }}>
          <Skeleton width="45%" />
          <Skeleton width="25%" />
        </View>
      ))}
    </View>
  );
}
