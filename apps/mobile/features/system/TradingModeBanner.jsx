import { useState } from 'react';
import { Pressable, View } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { useTheme } from '../../components/theme/ThemeProvider';
import { AppText, Badge } from '../../components/ui';
import { useSystemStatus } from './api';

/** Always-visible reminder of trading mode and why live trading is locked. */
export function TradingModeBanner() {
  const theme = useTheme();
  const { data } = useSystemStatus();
  const [open, setOpen] = useState(false);
  if (!data) return null;
  return (
    <Pressable
      testID="trading-mode-banner"
      accessibilityRole="button"
      accessibilityLabel="Trading mode details"
      onPress={() => setOpen((o) => !o)}
      style={{ borderRadius: theme.radii.md, borderWidth: 1, borderColor: theme.colors.border, backgroundColor: theme.colors.surfaceRaised, padding: 12, gap: 8 }}
    >
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
        <Badge label="PAPER MODE" tone="primary" />
        {data.is_demo_data ? <Badge label="DEMO DATA" tone="info" /> : null}
        <Badge label="LIVE LOCKED" tone="warning" />
        <View style={{ flex: 1 }} />
        <Ionicons name={open ? 'chevron-up' : 'chevron-down'} size={16} color={theme.colors.textMuted} />
      </View>
      {open ? (
        <View style={{ gap: 4 }}>
          <AppText variant="caption" tone="muted">Live trading stays disabled until all of these are resolved:</AppText>
          {data.live_trading_blockers.map((b) => (
            <AppText key={b} variant="caption" tone="muted">• {b}</AppText>
          ))}
          <AppText variant="caption" tone="faint" style={{ marginTop: 4 }}>{data.disclaimer}</AppText>
        </View>
      ) : null}
    </Pressable>
  );
}
