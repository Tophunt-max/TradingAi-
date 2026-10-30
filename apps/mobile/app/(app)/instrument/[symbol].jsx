import { useState } from 'react';
import { View } from 'react-native';
import { Stack, router, useLocalSearchParams } from 'expo-router';
import { AppText, Badge, Button, Card, Chip, ChipRow, DataSourceBadge, NoticeBanner, PnLText, QueryState, Screen, Skeleton } from '../../../components/ui';
import { CandleChart, IndicatorPanel } from '../../../components/charts/CandleChart';
import { useTheme } from '../../../components/theme/ThemeProvider';
import { BIAS_TONE } from '../../../features/ai/Sections';
import { patternContextLine, patternStatus } from '../../../features/ai/evidence';
import { FundamentalsCard } from '../../../features/learning/Sections';
import { useCandles, useQuotes } from '../../../features/market/api';
import { QualityBanner } from '../../../features/market/QualityBanner';
import { mapAnnotations } from '../../../features/market/annotations';
import { isStale } from '../../../features/system/api';
import { fixed, formatIST, formatPrice } from '../../../services/format';

const TIMEFRAMES = ['1m', '3m', '5m', '15m', '1h', '1d'].map((v) => ({ value: v, label: v.toUpperCase() }));
const PANELS = { rsi: 'RSI', macd: 'MACD', adx: 'ADX', stoch: 'Stoch' };

export default function Instrument() {
  const { symbol: raw } = useLocalSearchParams();
  const symbol = String(raw).toUpperCase();
  const theme = useTheme();
  const [tf, setTf] = useState('5m');
  const [overlay, setOverlay] = useState({ ema: true, ema100: false, bb: false, vwap: false, avwap: false, st: false, smc: true });
  const [panel, setPanel] = useState('rsi');
  const indicators = [
    'ema9', 'ema21', 'rsi14', 'macd', 'adx14', 'stoch',
    overlay.bb ? 'bb20' : null, overlay.vwap ? 'vwap' : null, overlay.st ? 'supertrend' : null,
    overlay.ema100 ? 'ema100' : null, overlay.avwap ? 'avwap' : null,
  ].filter(Boolean).join(',');
  const quote = useQuotes('ALL', symbol);
  const candles = useCandles(symbol, tf, indicators, 80, overlay.smc);
  const q = quote.data?.items?.[0];
  const s = theme.chart.series;
  const toggle = (k) => setOverlay((o) => ({ ...o, [k]: !o[k] }));

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
          {(d) => {
            const { markers, zones } = overlay.smc ? mapAnnotations(d.candles, d.annotations) : { markers: [], zones: [] };
            const recent = overlay.smc ? [...(d.annotations?.structure ?? []), ...(d.annotations?.patterns ?? [])].sort((a, b) => (a.confirmed_ts < b.confirmed_ts ? 1 : -1)).slice(0, 5) : [];
            const ind = d.indicators;
            return (
              <View style={{ gap: 8 }} testID="chart-card">
                <View style={{ flexDirection: 'row', gap: 12, flexWrap: 'wrap' }}>
                  {overlay.ema ? (
                    <>
                      <AppText variant="caption" style={{ color: s[0] }}>EMA 9</AppText>
                      <AppText variant="caption" style={{ color: s[1] }}>EMA 21</AppText>
                    </>
                  ) : null}
                  {overlay.bb ? <AppText variant="caption" style={{ color: s[2] }}>BB 20, 2</AppText> : null}
                  {overlay.vwap ? <AppText variant="caption" style={{ color: s[3] }}>VWAP</AppText> : null}
                  {overlay.st ? <AppText variant="caption" style={{ color: s[4] }}>Supertrend</AppText> : null}
                  {overlay.ema100 ? <AppText variant="caption" style={{ color: s[2] }}>EMA 100</AppText> : null}
                  {overlay.avwap ? <AppText variant="caption" style={{ color: s[3] }}>AVWAP (last swing)</AppText> : null}
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
                  markers={markers}
                  zones={zones}
                  overlays={[
                    ...(overlay.ema ? [{ key: 'ema9', values: ind.ema9, color: s[0] }, { key: 'ema21', values: ind.ema21, color: s[1] }] : []),
                    ...(overlay.bb && ind.bb_upper ? [{ key: 'bbu', values: ind.bb_upper, color: s[2] }, { key: 'bbl', values: ind.bb_lower, color: s[2] }] : []),
                    ...(overlay.vwap && ind.vwap ? [{ key: 'vwap', values: ind.vwap, color: s[3] }] : []),
                    ...(overlay.st && ind.supertrend ? [{ key: 'st', values: ind.supertrend, color: s[4] }] : []),
                    ...(overlay.ema100 && ind.ema100 ? [{ key: 'ema100', values: ind.ema100, color: s[2] }] : []),
                    ...(overlay.avwap && ind.avwap ? [{ key: 'avwap', values: ind.avwap, color: s[3] }] : []),
                  ]}
                />
                <AppText variant="caption" tone="muted">
                  {panel === 'rsi'
                    ? `RSI (14) ${fixed(ind.rsi14.at(-1))}`
                    : panel === 'macd'
                      ? `MACD (12, 26, 9) ${fixed(ind.macd.at(-1))}`
                      : panel === 'adx'
                        ? `ADX (14) ${fixed(ind.adx14.at(-1), 1)} · +DI ${fixed(ind.pdi14.at(-1), 1)} · −DI ${fixed(ind.mdi14.at(-1), 1)}`
                        : `Stochastic %K ${fixed(ind.stoch_k.at(-1), 1)} · %D ${fixed(ind.stoch_d.at(-1), 1)}`}
                </AppText>
                {panel === 'rsi' ? (
                  <IndicatorPanel lines={[{ key: 'rsi', values: ind.rsi14, color: s[0] }]} range={[0, 100]} guides={[30, 70]} />
                ) : panel === 'macd' ? (
                  <IndicatorPanel bars={ind.macd_hist} lines={[{ key: 'macd', values: ind.macd, color: s[1] }, { key: 'sig', values: ind.macd_signal, color: s[2] }]} />
                ) : panel === 'adx' ? (
                  <IndicatorPanel range={[0, 60]} guides={[25]} lines={[{ key: 'adx', values: ind.adx14, color: s[0] }, { key: 'pdi', values: ind.pdi14, color: theme.chart.candleUp }, { key: 'mdi', values: ind.mdi14, color: theme.chart.candleDown }]} />
                ) : (
                  <IndicatorPanel range={[0, 100]} guides={[20, 80]} lines={[{ key: 'k', values: ind.stoch_k, color: s[0] }, { key: 'd', values: ind.stoch_d, color: s[2] }]} />
                )}
                {recent.length ? (
                  <View style={{ gap: 4 }} testID="chart-events">
                    <AppText variant="label" tone="muted">RECENT CONFIRMED EVENTS</AppText>
                    {recent.map((e, i) => {
                      const status = patternStatus(e);
                      const ctx = patternContextLine(e);
                      return (
                        <View key={`${e.name}-${e.confirmed_ts}-${i}`} style={{ gap: 2 }}>
                          <View style={{ flexDirection: 'row', gap: 6, alignItems: 'center' }}>
                            <Badge label={e.direction} tone={BIAS_TONE[e.direction]} />
                            <AppText variant="caption" style={{ flex: 1 }} numberOfLines={1}>{e.name} · {formatIST(e.confirmed_ts)}</AppText>
                            {status ? <Badge label={status.label} tone={status.tone} /> : null}
                          </View>
                          {ctx ? <AppText variant="caption" tone="faint" numberOfLines={1}>{ctx}</AppText> : null}
                        </View>
                      );
                    })}
                  </View>
                ) : null}
              </View>
            );
          }}
        </QueryState>
      </Card>

      <View style={{ flexDirection: 'row', gap: 8, flexWrap: 'wrap' }} testID="indicator-toggles">
        <Chip label="EMA 9/21" selected={overlay.ema} onPress={() => toggle('ema')} />
        <Chip label="Bollinger" selected={overlay.bb} onPress={() => toggle('bb')} />
        <Chip label="VWAP" selected={overlay.vwap} onPress={() => toggle('vwap')} />
        <Chip label="Supertrend" selected={overlay.st} onPress={() => toggle('st')} />
        <Chip label="EMA 100" selected={overlay.ema100} onPress={() => toggle('ema100')} />
        <Chip label="AVWAP" selected={overlay.avwap} onPress={() => toggle('avwap')} />
        <Chip label="Patterns & SMC" selected={overlay.smc} onPress={() => toggle('smc')} testID="toggle-smc" />
      </View>
      <View style={{ flexDirection: 'row', gap: 8, flexWrap: 'wrap' }}>
        {Object.entries(PANELS).map(([k, label]) => <Chip key={k} label={label} selected={panel === k} onPress={() => setPanel(k)} />)}
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
      <FundamentalsCard symbol={symbol} />
      <AppText variant="caption" tone="faint">
        Triangles mark patterns at the candle that completed them; UNCONFIRMED patterns still need a close beyond their high or low. AVWAP is anchored at the latest confirmed swing and starts only once that swing was confirmed. Shaded boxes are fair value gaps and order blocks until price trades back into them.
      </AppText>
    </Screen>
  );
}
