import { Pressable, View } from 'react-native';
import { router } from 'expo-router';
import { useTheme } from '../../components/theme/ThemeProvider';
import { AppText, PnLText } from '../../components/ui';

export function QuoteRow({ q, right, testID }) {
  const theme = useTheme();
  return (
    <Pressable
      testID={testID ?? `quote-${q.symbol}`}
      accessibilityRole="button"
      accessibilityLabel={`${q.symbol}, ${q.last}, ${q.change_pct} percent`}
      onPress={() => router.push(`/instrument/${q.symbol}`)}
      style={({ pressed }) => ({ flexDirection: 'row', alignItems: 'center', paddingVertical: 12, borderBottomWidth: 1, borderColor: theme.colors.borderSubtle, opacity: pressed ? 0.7 : 1 })}
    >
      <View style={{ flex: 1 }}>
        <AppText variant="bodyStrong">{q.symbol}</AppText>
        <AppText variant="caption" tone="faint" numberOfLines={1}>{q.exchange}{q.sector ? ` · ${q.sector}` : ''}</AppText>
      </View>
      {right}
      <View style={{ alignItems: 'flex-end', minWidth: 110 }}>
        <AppText variant="mono">{Number(q.last).toLocaleString('en-IN', { minimumFractionDigits: 2 })}</AppText>
        <View style={{ flexDirection: 'row', gap: 4 }}>
          <PnLText value={q.change} variant="caption" />
          <PnLText value={q.change_pct} prefix="(" suffix="%)" variant="caption" />
        </View>
      </View>
    </Pressable>
  );
}
