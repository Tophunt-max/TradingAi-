import { ActivityIndicator, Pressable, StyleSheet, View } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { useTheme } from '../theme/ThemeProvider';
import { useHaptics } from '../../hooks/useHaptics';
import { AppText } from './AppText';

/** variant: primary | secondary | ghost | danger. size: md | sm */
export function Button({
  title,
  onPress,
  variant = 'primary',
  size = 'md',
  icon,
  loading = false,
  disabled = false,
  fullWidth = false,
  accessibilityHint,
  testID,
}) {
  const theme = useTheme();
  const { tap } = useHaptics();
  const c = theme.colors;
  const styles = {
    primary: { bg: c.primary, fg: '#FFFFFF', border: c.primary },
    secondary: { bg: c.surfaceRaised, fg: c.text, border: c.border },
    ghost: { bg: 'transparent', fg: c.primary, border: 'transparent' },
    danger: { bg: c.negativeSoft, fg: c.negative, border: c.negative },
  }[variant];
  const isDisabled = disabled || loading;

  return (
    <Pressable
      testID={testID}
      accessibilityRole="button"
      accessibilityLabel={title}
      accessibilityHint={accessibilityHint}
      accessibilityState={{ disabled: isDisabled, busy: loading }}
      disabled={isDisabled}
      onPress={(e) => {
        tap();
        onPress?.(e);
      }}
      style={({ pressed }) => [
        s.base,
        {
          backgroundColor: styles.bg,
          borderColor: styles.border,
          borderRadius: theme.radii.md,
          paddingVertical: size === 'sm' ? 8 : 13,
          paddingHorizontal: size === 'sm' ? 12 : 18,
          opacity: isDisabled ? 0.5 : pressed ? 0.85 : 1,
          alignSelf: fullWidth ? 'stretch' : 'auto',
        },
        variant === 'primary' && !isDisabled ? { shadowColor: c.glow, shadowOpacity: 1, shadowRadius: 12, shadowOffset: { width: 0, height: 4 } } : null,
      ]}
    >
      <View style={s.row}>
        {loading ? (
          <ActivityIndicator color={variant === 'primary' ? '#fff' : styles.fg} />
        ) : (
          <>
            {icon ? <Ionicons name={icon} size={size === 'sm' ? 16 : 18} color={variant === 'primary' ? '#fff' : styles.fg} /> : null}
            <AppText variant={size === 'sm' ? 'caption' : 'bodyStrong'} style={{ color: variant === 'primary' ? '#fff' : styles.fg, fontWeight: '600' }}>
              {title}
            </AppText>
          </>
        )}
      </View>
    </Pressable>
  );
}

const s = StyleSheet.create({
  base: { borderWidth: 1, minHeight: 44, justifyContent: 'center' },
  row: { flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 8 },
});
