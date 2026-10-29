import { View } from 'react-native';
import { useTheme } from '../theme/ThemeProvider';
import { AppText } from './AppText';

/** tone: neutral | primary | positive | negative | warning | info */
export function Badge({ label, tone = 'neutral', testID }) {
  const theme = useTheme();
  const c = theme.colors;
  const map = {
    neutral: [c.surfaceRaised, c.textMuted],
    primary: [c.primarySoft, c.primary],
    positive: [c.positiveSoft, c.positive],
    negative: [c.negativeSoft, c.negative],
    warning: [c.warningSoft, c.warning],
    info: ['rgba(59,130,246,0.14)', c.info],
  };
  const [bg, fg] = map[tone] ?? map.neutral;
  return (
    <View
      testID={testID}
      accessibilityRole="text"
      style={{ backgroundColor: bg, borderRadius: theme.radii.pill, paddingHorizontal: 8, paddingVertical: 3, alignSelf: 'flex-start' }}
    >
      <AppText variant="label" style={{ color: fg }}>{label}</AppText>
    </View>
  );
}

/**
 * Data provenance badge. Every market-data view must render one so users can
 * always tell LIVE, DELAYED, DEMO and STALE data apart.
 */
export function DataSourceBadge({ source, stale = false }) {
  if (stale) return <Badge label="STALE" tone="warning" testID="badge-stale" />;
  const tone = { LIVE: 'positive', DELAYED: 'warning', DEMO: 'info', PAPER: 'primary' }[source] ?? 'neutral';
  // Synthetic prices are "SIMULATED" so they are not confused with the DEMO account mode.
  const label = source === 'DEMO' ? 'SIMULATED' : source ?? 'UNKNOWN';
  return <Badge label={label} tone={tone} testID={`badge-${(source ?? 'unknown').toLowerCase()}`} />;
}

/** Account mode of a bot/order: PAPER executes in the DEMO account, LIVE in the REAL one. */
export function ModeBadge({ live }) {
  return live ? <Badge label="REAL" tone="negative" /> : <Badge label="DEMO · VIRTUAL" tone="primary" />;
}
