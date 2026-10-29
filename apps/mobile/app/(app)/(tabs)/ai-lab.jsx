import { useState } from 'react';
import { View } from 'react-native';
import { router, useLocalSearchParams } from 'expo-router';
import { Ionicons } from '@expo/vector-icons';
import { AppText, Badge, Button, Card, CardHeader, ChipRow, DataSourceBadge, KeyValue, ListRow, NoticeBanner, QueryState, Screen, Select } from '../../../components/ui';
import { useTheme } from '../../../components/theme/ThemeProvider';
import { useAnalysis } from '../../../features/market/api';
import { FEATURES, featureHref } from '../../../features/roadmap';
import { formatIST } from '../../../services/format';

const STOCKS = ['RELIANCE', 'TCS', 'INFY', 'HDFCBANK', 'ICICIBANK', 'SBIN', 'ITC', 'LT', 'BHARTIARTL', 'TATAMOTORS'];
const TONE = { BULLISH: 'positive', BEARISH: 'negative', SIDEWAYS: 'warning', NEUTRAL: 'muted' };
const ICON = { BULLISH: 'trending-up', BEARISH: 'trending-down', SIDEWAYS: 'swap-horizontal' };

export default function AiLab() {
  const theme = useTheme();
  const params = useLocalSearchParams();
  const [group, setGroup] = useState('NIFTY');
  const [stock, setStock] = useState('RELIANCE');
  const [tf, setTf] = useState('15m');
  const [seenParam, setSeenParam] = useState(null);

  // Follow ?symbol= when arriving from a chart (state adjusted during render).
  const param = params.symbol ? String(params.symbol).toUpperCase() : null;
  if (param && param !== seenParam) {
    setSeenParam(param);
    if (param === 'NIFTY' || param === 'BANKNIFTY') setGroup(param);
    else if (STOCKS.includes(param)) {
      setGroup('STOCKS');
      setStock(param);
    }
  }

  const symbol = group === 'STOCKS' ? stock : group;
  const a = useAnalysis(symbol, tf);

  return (
    <Screen title="AI Market Analysis" subtitle="Transparent, rule-based until a calibrated model ships">
      <ChipRow value={group} onChange={setGroup} options={[{ value: 'NIFTY', label: 'NIFTY' }, { value: 'BANKNIFTY', label: 'BANKNIFTY' }, { value: 'STOCKS', label: 'STOCKS' }]} testIDPrefix="ai-group" />
      {group === 'STOCKS' ? <Select label="Stock" value={stock} onChange={setStock} options={STOCKS.map((s) => ({ value: s, label: s }))} /> : null}
      <ChipRow value={tf} onChange={setTf} options={['5m', '15m', '1h', '1d'].map((v) => ({ value: v, label: v.toUpperCase() }))} />
      <QueryState query={a}>
        {(d) => (
          <>
            <Card testID="analysis-trend" style={{ borderColor: theme.colors[TONE[d.trend]] ?? theme.colors.border }}>
              <View style={{ flexDirection: 'row', gap: 12, alignItems: 'center' }}>
                <View style={{ width: 52, height: 52, borderRadius: 16, alignItems: 'center', justifyContent: 'center', backgroundColor: theme.colors.surfaceRaised }}>
                  <Ionicons name={ICON[d.trend]} size={28} color={theme.colors[TONE[d.trend]]} />
                </View>
                <View style={{ flex: 1 }}>
                  <AppText variant="caption" tone="muted">Technical trend · {symbol} · {tf}</AppText>
                  <AppText variant="title" tone={TONE[d.trend]}>{d.trend}</AppText>
                  <AppText variant="caption" tone="faint">{d.trend_method}</AppText>
                </View>
                <DataSourceBadge source={d.source} />
              </View>
            </Card>

            <Card>
              <CardHeader title="AI probability" right={<Badge label="NOT AVAILABLE" tone="warning" />} />
              <AppText tone="muted">
                UP / DOWN / SIDEWAYS probabilities appear only after a model passes out-of-sample calibration checks (Phase 4). A high probability
                alone will never authorise a trade.
              </AppText>
            </Card>

            <Card>
              <CardHeader title="Key levels" subtitle="Confirmed swing points only" />
              <KeyValue label="Last price" value={d.last_price.toLocaleString('en-IN')} />
              {d.resistance.map((r, i) => <KeyValue key={`r${r}`} label={`Resistance ${i + 1}`} value={r.toLocaleString('en-IN')} tone="negative" />)}
              {d.support.map((s, i) => <KeyValue key={`s${s}`} label={`Support ${i + 1}`} value={s.toLocaleString('en-IN')} tone="positive" />)}
              {!d.resistance.length && !d.support.length ? <AppText tone="muted">No confirmed levels in range.</AppText> : null}
              <KeyValue label="ATR (14)" value={d.atr ?? '—'} />
            </Card>

            <Card>
              <CardHeader title="Signals" subtitle="Correlated; not independent confirmations" />
              {d.signals.map((s) => (
                <ListRow key={s.name} title={s.name} subtitle={s.reading} right={<Badge label={s.bias} tone={TONE[s.bias] === 'muted' ? 'neutral' : TONE[s.bias]} />} />
              ))}
            </Card>

            <Card testID="analysis-decision" style={{ borderColor: theme.colors.warning }}>
              <CardHeader title="Decision" right={<Badge label="NO TRADE" tone="warning" />} />
              {d.abstain_reasons.map((r) => <AppText key={r} variant="caption" tone="muted">• {r}</AppText>)}
              <AppText variant="caption" tone="faint" style={{ marginTop: 8 }}>Generated {formatIST(d.as_of)} IST</AppText>
            </Card>
          </>
        )}
      </QueryState>
      <Button title="Open chart" icon="bar-chart-outline" variant="secondary" fullWidth onPress={() => router.push(`/instrument/${symbol}`)} />
      <Card>
        <CardHeader title="Research tools" />
        {['model-lab', 'backtesting'].map((id) => (
          <ListRow key={id} title={FEATURES[id].title} subtitle={`Phase ${FEATURES[id].phase} · not built yet`} icon={FEATURES[id].icon} onPress={() => router.push(featureHref(id))} />
        ))}
      </Card>
      <NoticeBanner tone="info">Analysis uses synthetic DEMO candles until an authorised data provider is connected.</NoticeBanner>
    </Screen>
  );
}
