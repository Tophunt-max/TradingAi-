import { useState } from 'react';
import { View } from 'react-native';
import { router, useLocalSearchParams } from 'expo-router';
import { AppText, Button, Card, CardHeader, ChipRow, DataSourceBadge, ListRow, NoticeBanner, QueryState, Screen, Select } from '../../../components/ui';
import { ProbabilityCard } from '../../../features/ai/ProbabilityCard';
import { EvidenceCard, LevelsCard, MtfStrip, PatternsCard, PipelineCard, RegimeCard, SetupCard, SmcCard, StrategiesCard } from '../../../features/ai/Sections';
import { useAnalysis, useMtf } from '../../../features/market/api';
import { QualityBanner } from '../../../features/market/QualityBanner';
import { FEATURES, featureHref } from '../../../features/roadmap';
import { formatIST } from '../../../services/format';

const STOCKS = ['RELIANCE', 'TCS', 'INFY', 'HDFCBANK', 'ICICIBANK', 'SBIN', 'ITC', 'LT', 'BHARTIARTL', 'TATAMOTORS'];

export default function AiLab() {
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
  const mtf = useMtf(symbol);

  return (
    <Screen title="AI Market Analysis" subtitle="Regime, structure, strategies and calibrated probabilities" refreshing={a.isRefetching} onRefresh={a.refetch}>
      <ChipRow value={group} onChange={setGroup} options={[{ value: 'NIFTY', label: 'NIFTY' }, { value: 'BANKNIFTY', label: 'BANKNIFTY' }, { value: 'STOCKS', label: 'STOCKS' }]} testIDPrefix="ai-group" />
      {group === 'STOCKS' ? <Select label="Stock" value={stock} onChange={setStock} options={STOCKS.map((s) => ({ value: s, label: s }))} /> : null}
      <ChipRow value={tf} onChange={setTf} options={['5m', '15m', '1h', '1d'].map((v) => ({ value: v, label: v.toUpperCase() }))} testIDPrefix="ai-tf" />
      <QueryState query={a}>
        {(d) => (
          <>
            <View style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' }}>
              <AppText variant="caption" tone="faint">
                {symbol} · {tf} · {d.provider} · {formatIST(d.as_of)} IST
              </AppText>
              <DataSourceBadge source={d.source} stale={d.quality?.status === 'FAIL'} />
            </View>
            <QualityBanner payload={d} />
            <PipelineCard steps={d.pipeline} reasons={d.abstain_reasons} explanation={d.explanation} />
            <ProbabilityCard prediction={d.prediction} />
            <RegimeCard regime={d.regime} trend={d.trend} />
            <MtfStrip data={mtf.data} />
            <SetupCard setup={d.setup_candidate} options={d.options} />
            <StrategiesCard strategies={d.strategies} />
            <EvidenceCard technical={d.technical} />
            <PatternsCard patterns={d.patterns} structure={d.structure} />
            <SmcCard smc={d.smc} />
            <LevelsCard data={d} />
          </>
        )}
      </QueryState>
      <Button title="Open chart" icon="bar-chart-outline" variant="secondary" fullWidth onPress={() => router.push(`/instrument/${symbol}`)} />
      <Card>
        <CardHeader title="Research tools" />
        {['model-lab', 'backtesting'].map((id) => (
          <ListRow key={id} title={FEATURES[id].title} subtitle={FEATURES[id].route ? FEATURES[id].summary : `Phase ${FEATURES[id].phase} · not built yet`} icon={FEATURES[id].icon} onPress={() => router.push(featureHref(id))} />
        ))}
      </Card>
      {a.data?.is_demo ? <NoticeBanner tone="info">Analysis uses synthetic DEMO candles until an authorised data provider is connected.</NoticeBanner> : null}
    </Screen>
  );
}
