import { Text } from 'react-native';
import { useTheme } from '../theme/ThemeProvider';

/**
 * Typography primitive.
 * variant: display | title | heading | body | bodyStrong | caption | label | mono
 * tone: default | muted | faint | primary | positive | negative | warning | inverse
 */
export function AppText({ variant = 'body', tone = 'default', style, children, ...rest }) {
  const theme = useTheme();
  const tones = {
    default: theme.colors.text,
    muted: theme.colors.textMuted,
    faint: theme.colors.textFaint,
    primary: theme.colors.primary,
    secondary: theme.colors.secondary,
    positive: theme.colors.positive,
    negative: theme.colors.negative,
    warning: theme.colors.warning,
    inverse: theme.colors.textInverse,
  };
  return (
    <Text
      maxFontSizeMultiplier={1.6}
      style={[theme.typography[variant], { color: tones[tone] ?? tones.default }, style]}
      {...rest}
    >
      {children}
    </Text>
  );
}

// Formats a signed number with colour: green positive, red negative.
export function PnLText({ value, suffix = '', prefix = '', variant = 'mono', style }) {
  if (value === null || value === undefined || value === '') {
    return <AppText variant={variant} tone="faint" style={style}>—</AppText>;
  }
  const n = Number(value);
  const tone = n > 0 ? 'positive' : n < 0 ? 'negative' : 'muted';
  const sign = n > 0 ? '+' : '';
  return (
    <AppText variant={variant} tone={tone} style={style}>
      {prefix}
      {sign}
      {Number.isFinite(n) ? n.toLocaleString('en-IN', { maximumFractionDigits: 2, minimumFractionDigits: 2 }) : '—'}
      {suffix}
    </AppText>
  );
}
