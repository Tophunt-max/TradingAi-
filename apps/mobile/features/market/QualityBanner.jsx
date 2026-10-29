import { useState } from 'react';
import { Pressable, View } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { useTheme } from '../../components/theme/ThemeProvider';
import { AppText } from '../../components/ui';

/**
 * Shows the data-quality report and provider notes attached to every
 * market-data payload. FAIL means the data must not drive trading decisions.
 */
export function QualityBanner({ payload, testID = 'quality-banner' }) {
  const theme = useTheme();
  const [open, setOpen] = useState(false);
  if (!payload?.quality) return null;
  const { status, issues } = payload.quality;
  const visible = issues.filter((i) => i.severity !== 'INFO');
  const info = issues.filter((i) => i.severity === 'INFO');
  const notes = payload.notes ?? [];
  if (!visible.length && !info.length && !notes.length) return null;
  const tone = status === 'FAIL' ? theme.colors.negative : status === 'WARN' ? theme.colors.warning : theme.colors.textMuted;
  const bg = status === 'FAIL' ? theme.colors.negativeSoft : status === 'WARN' ? theme.colors.warningSoft : theme.colors.surfaceRaised;
  const headline =
    status === 'FAIL'
      ? 'Data quality check failed: do not trade on this data'
      : status === 'WARN'
        ? `Data quality warnings (${visible.length})`
        : info[0]?.message ?? 'Data notes';
  return (
    <Pressable testID={testID} accessibilityRole="button" accessibilityLabel="Data quality details" onPress={() => setOpen((o) => !o)} style={{ padding: 10, borderRadius: theme.radii.md, backgroundColor: bg, gap: 6 }}>
      <View style={{ flexDirection: 'row', gap: 8, alignItems: 'center' }}>
        <Ionicons name={status === 'OK' ? 'information-circle-outline' : 'warning-outline'} size={16} color={tone} />
        <AppText variant="caption" style={{ color: tone, flex: 1, fontWeight: '600' }}>{headline}</AppText>
        <Ionicons name={open ? 'chevron-up' : 'chevron-down'} size={14} color={tone} />
      </View>
      {open ? (
        <View style={{ gap: 4 }}>
          {[...visible, ...info].map((i) => (
            <AppText key={i.code} variant="caption" tone="muted">• [{i.severity}] {i.message}</AppText>
          ))}
          {notes.map((n) => <AppText key={n} variant="caption" tone="faint">• {n}</AppText>)}
        </View>
      ) : null}
    </Pressable>
  );
}
