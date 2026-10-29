import { Pressable, View } from 'react-native';
import { router } from 'expo-router';
import { useTheme } from '../../components/theme/ThemeProvider';
import { AppText, DataSourceBadge, PnLText, Skeleton, StateView } from '../../components/ui';
import { isStale } from '../system/api';
import { useQuotes } from './api';

export function IndexHeroCards({ symbols = 'NIFTY,BANKNIFTY' }) {
  const theme = useTheme();
  const q = useQuotes('INDEX', symbols);
  if (q.isError) return <StateView kind="error" compact message={q.error.message} actionLabel="Retry" onAction={q.refetch} />;
  const stale = q.data ? isStale(q.data.as_of) : false;
  return (
    <View style={{ gap: 8 }}>
    <View style={{ flexDirection: 'row', gap: 12 }}>
      {(q.data?.items ?? [null, null]).map((it, i) => {
        const up = it ? Number(it.change) >= 0 : true;
        return (
          <Pressable
            key={it?.symbol ?? i}
            testID={it ? `hero-${it.symbol}` : undefined}
            accessibilityRole="button"
            accessibilityLabel={it ? `${it.symbol} ${it.last}` : 'Loading'}
            onPress={() => it && router.push(`/instrument/${it.symbol}`)}
            style={{
              flex: 1,
              padding: 14,
              gap: 4,
              borderRadius: theme.radii.lg,
              borderWidth: 1,
              borderColor: up ? 'rgba(52,211,153,0.35)' : 'rgba(248,113,113,0.35)',
              backgroundColor: up ? 'rgba(16,185,129,0.08)' : 'rgba(239,68,68,0.08)',
            }}
          >
            {it ? (
              <>
                <View style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' }}>
                  <AppText variant="label" tone="muted">{it.symbol === 'NIFTY' ? 'NIFTY 50' : it.symbol}</AppText>
                  <DataSourceBadge source={q.data.source} stale={stale} />
                </View>
                <AppText variant="title" style={{ fontVariant: ['tabular-nums'] }}>
                  {Number(it.last).toLocaleString('en-IN', { minimumFractionDigits: 2 })}
                </AppText>
                <View style={{ flexDirection: 'row', gap: 6 }}>
                  <PnLText value={it.change} variant="caption" />
                  <PnLText value={it.change_pct} prefix="(" suffix="%)" variant="caption" />
                </View>
              </>
            ) : (
              <View style={{ gap: 8 }}>
                <Skeleton width="50%" />
                <Skeleton width="80%" height={22} />
                <Skeleton width="40%" />
              </View>
            )}
          </Pressable>
        );
      })}
    </View>
    {q.data?.is_demo ? (
      <AppText variant="caption" tone="warning" testID="demo-disclaimer">{q.data.disclaimer}</AppText>
    ) : null}
    </View>
  );
}
