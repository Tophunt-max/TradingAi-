import { Pressable, View } from 'react-native';
import { useTheme } from '../theme/ThemeProvider';
import { AppText } from './AppText';

export function Card({ children, style, onPress, padded = true, accessibilityLabel, testID }) {
  const theme = useTheme();
  const base = {
    backgroundColor: theme.colors.surface,
    borderColor: theme.colors.borderSubtle,
    borderWidth: 1,
    borderRadius: theme.radii.lg,
    padding: padded ? theme.spacing.lg : 0,
    ...theme.elevation.card,
  };
  if (onPress) {
    return (
      <Pressable
        testID={testID}
        accessibilityRole="button"
        accessibilityLabel={accessibilityLabel}
        onPress={onPress}
        style={({ pressed }) => [base, { opacity: pressed ? 0.9 : 1 }, style]}
      >
        {children}
      </Pressable>
    );
  }
  return <View testID={testID} style={[base, style]}>{children}</View>;
}

export function CardHeader({ title, subtitle, right }) {
  return (
    <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12, gap: 8 }}>
      <View style={{ flex: 1 }}>
        <AppText variant="heading">{title}</AppText>
        {subtitle ? <AppText variant="caption" tone="muted">{subtitle}</AppText> : null}
      </View>
      {right}
    </View>
  );
}
