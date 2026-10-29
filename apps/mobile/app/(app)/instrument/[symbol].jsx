import { useState } from 'react';
import { View } from 'react-native';
import { Stack, router, useLocalSearchParams } from 'expo-router';
import { AppText, Button, Card, Chip, ChipRow, DataSourceBadge, NoticeBanner, PnLText, QueryState, Screen, Skeleton } from '../../../components/ui';
import { CandleChart, IndicatorPanel } from '../../../components/charts/CandleChart';
import { useTheme } from '../../../components/theme/ThemeProvider';
import { useCandles, useQuotes } from '../../../features/market/api';
import { isStale } from '../../../features/system/api';
import { formatIST, formatPrice } from '../../../services/format';
import { QualityBanner } from '../../../features/market/QualityBanner';

const TIMEFRAMES = ['1m', '3m', '5m', '15m', '1h', '1d'].map((v) => ({ value: v, label: v.toUpperCase() }));

export default function Instrument() {
  const { symbol: raw } = useLocalSearchParams();
  const symbol = String(raw).toUpperCase();
  const theme = useTheme();
  const [tf, setTf] = useState('5m');
  const [overlay, setOverlay] = useState({ ema: true, bb: false });
  const [panel, setPanel] = useState('rsi');
  const indicators = ['ema9', 'ema21', 'rsi14', 'macd', overlay.bb ? 'bb20' : null].filter(Boolean).join(',');
  const quote = useQuotes('ALL', symbol);
  const candles = useCandles(symbol, tf, indicators, 80);
  const q = quote.data?.items?.[0];
  const s = theme.chart.series;

  return (
    <Screen edges={['bottom']}>
      <Stack.Screen options={{ title: symbol }} />
      <View style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'flex-end' }}>
        <View>
          {q ? (
            <>
              <AppText variant="display" style={{ fontVariant: ['tabular-nums'] }}>{formatPrice(q.last)}</AppText>
              <View style={{ flexDirection: 'row', gap: 6 }}>
                <PnLText value={q.change} />
                <PnLText value={q.change_pct} prefix="(" suffix="%)" />
              </View>
              <AppText variant="caption" tone="faint">{q.name}</AppText>
            </>
          ) : (
            <Skeleton width={180} height={30} />
          )}
        </View>
        {quote.data ? <DataSourceBadge source={quote.data.source} stale={isStale(quote.data.as_of)} /> : null}
      </View>

      <ChipRow options={TIMEFRAMES} value={tf} onChange={setTf} testIDPrefix="tf" />

      <Card padded={false} style={{ padding: 10, gap: 8 }}>
        <QueryState query={candles}>
          {(d) => (
            <View style={{ gap: 8 }} testID="chart-card">
              <View style={{ flexDirection: 'row', gap: 12, flexWrap: 'wrap' }}>
                {overlay.ema ? (
                  <>
                    <AppText variant="caption" style={{ color: s[0] }}>EMA 9</AppText>
                    <AppText variant="caption" style={{ color: s[1] }}>EMA 21</AppText>
                  </>
                ) : null}
                {overlay.bb ? <AppText variant="caption" style={{ color: s[2] }}>BB 20, 2</AppText> : null}
                <AppText variant="caption" tone="faint">{d.provider} · closed candles · {formatIST(d.as_of)} IST</AppText>
              </View>
              {d.notes?.filter((n) => n.includes('proxy')).map((n) => (
                <NoticeBanner key={n} tone="info" testID="chart-proxy-note">{n}</NoticeBanner>
              ))}
              <QualityBanner payload={d} testID="chart-quality" />
              <CandleChart
                testID="candle-chart"
                candles={d.candles}
                height={250}
                overlays={[
                  ...(overlay.ema ? [{ key: 'ema9', values: d.indicators.ema9, color: s[0] }, { key: 'ema21', values: d.indicators.ema21, color: s[1] }] : []),
                  ...(overlay.bb && d.indicators.bb_upper
                    ? [
                        { key: 'bbu', values: d.indicators.bb_upper, color: s[2] },
                        { key: 'bbl', values: d.indicators.bb_lower, color: s[2] },
                      ]
                    : []),
                ]}
              />
              <AppText variant="caption" tone="muted">
                {panel === 'rsi'
                  ? `RSI (14) ${d.indicators.rsi14.at(-1)?.toFixed(2) ?? '—'}`
                  : `MACD (12, 26, 9) ${d.indicators.macd.at(-1)?.toFixed(2) ?? '—'}`}
              </AppText>
              {panel === 'rsi' ? (
                <IndicatorPanel lines={[{ key: 'rsi', values: d.indicators.rsi14, color: s[0] }]} range={[0, 100]} guides={[30, 70]} />
              ) : (
                <IndicatorPanel
                  bars={d.indicators.macd_hist}
                  lines={[
                    { key: 'macd', values: d.indicators.macd, color: s[1] },
                    { key: 'sig', values: d.indicators.macd_signal, color: s[2] },
                  ]}
                />
              )}
            </View>
          )}
        </QueryState>
      </Card>

      <View style={{ flexDirection: 'row', gap: 8, flexWrap: 'wrap' }} testID="indicator-toggles">
        <Chip label="EMA 9/21" selected={overlay.ema} onPress={() => setOverlay((o) => ({ ...o, ema: !o.ema }))} />
        <Chip label="Bollinger" selected={overlay.bb} onPress={() => setOverlay((o) => ({ ...o, bb: !o.bb }))} />
        <Chip label="RSI" selected={panel === 'rsi'} onPress={() => setPanel('rsi')} />
        <Chip label="MACD" selected={panel === 'macd'} onPress={() => setPanel('macd')} />
      </View>

      <View style={{ flexDirection: 'row', gap: 10 }}>
        <View style={{ flex: 1 }}>
          <Button title="AI Analysis" icon="sparkles-outline" fullWidth onPress={() => router.push({ pathname: '/ai-lab', params: { symbol } })} />
        </View>
        {q?.has_options ? (
          <View style={{ flex: 1 }}>
            <Button title="Option Chain" icon="grid-outline" variant="secondary" fullWidth onPress={() => router.push({ pathname: '/option-chain', params: { underlying: symbol } })} />
          </View>
        ) : null}
      </View>
      <NoticeBanner tone="info">
        Pattern recognition, Smart Money Concepts, multi-timeframe views and drawing tools are added with the analysis engine (Phase 4).
      </NoticeBanner>
    </Screen>
  );
}
