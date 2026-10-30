import { useState } from 'react';
import { ChipRow, NoticeBanner, QueryState, Screen } from '../../components/ui';
import { useModelCompare } from '../../features/ai/api';
import { ModelCompareTable } from '../../features/ai/ModelCompare';

const SYMBOLS = ['NIFTY', 'BANKNIFTY', 'RELIANCE', 'TCS', 'INFY', 'HDFCBANK'];

export default function ModelCompareScreen() {
  const [symbol, setSymbol] = useState('NIFTY');
  const [tf, setTf] = useState('15m');
  const q = useModelCompare(symbol, tf);
  return (
    <Screen edges={['bottom']} refreshing={q.isRefetching} onRefresh={q.refetch}>
      <ChipRow value={symbol} onChange={setSymbol} options={SYMBOLS.map((s) => ({ value: s, label: s }))} testIDPrefix="cmp-symbol" />
      <ChipRow value={tf} onChange={setTf} options={['5m', '15m', '1h', '1d'].map((v) => ({ value: v, label: v.toUpperCase() }))} testIDPrefix="cmp-tf" />
      <QueryState query={q}>{(d) => <ModelCompareTable data={d} />}</QueryState>
      <NoticeBanner tone="info">
        Logistic regression is the baseline. Random forest and histogram gradient boosting must beat it out of sample with a bootstrap confidence interval before they can pass the gate. Models trained on synthetic DEMO data are never promotable.
      </NoticeBanner>
    </Screen>
  );
}
