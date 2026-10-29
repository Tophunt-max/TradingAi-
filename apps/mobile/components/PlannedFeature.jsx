import { View } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { useTheme } from './theme/ThemeProvider';
import { AppText, Badge, Card } from './ui';
import { FEATURES } from '../features/roadmap';

/** Card stating plainly that a module is not implemented yet. */
export function PlannedFeature({ id, compact = false }) {
  const theme = useTheme();
  const f = FEATURES[id];
  if (!f) return null;
  return (
    <Card testID={`planned-${id}`}>
      <View style={{ flexDirection: 'row', gap: 12, alignItems: compact ? 'center' : 'flex-start' }}>
        <View style={{ width: 40, height: 40, borderRadius: 12, backgroundColor: theme.colors.primarySoft, alignItems: 'center', justifyContent: 'center' }}>
          <Ionicons name={f.icon} size={20} color={theme.colors.primary} />
        </View>
        <View style={{ flex: 1, gap: 4 }}>
          <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
            <AppText variant="bodyStrong">{f.title}</AppText>
            <Badge label={`PHASE ${f.phase}`} tone="neutral" />
          </View>
          {!compact ? <AppText variant="caption" tone="muted">{f.summary}</AppText> : null}
          <AppText variant="caption" tone="warning">Not built yet. No data is shown until it is implemented.</AppText>
        </View>
      </View>
    </Card>
  );
}
