import { useState } from 'react';
import { router } from 'expo-router';
import { Button, ChipRow, QueryState, Screen } from '../../components/ui';
import { useIntelligenceDashboard } from '../../features/intelligence/api';
import { DashboardView } from '../../features/intelligence/Dashboard';

export default function Intelligence() {
  const [underlying, setUnderlying] = useState('NIFTY');
  const [tf, setTf] = useState('15m');
  const q = useIntelligenceDashboard(underlying, tf);
  return (
    <Screen edges={['bottom']} refreshing={q.isRefetching} onRefresh={q.refetch}>
      <ChipRow value={underlying} onChange={setUnderlying} options={['NIFTY', 'BANKNIFTY'].map((v) => ({ value: v, label: v }))} testIDPrefix="intel-underlying" />
      <ChipRow value={tf} onChange={setTf} options={['5m', '15m', '1h'].map((v) => ({ value: v, label: v.toUpperCase() }))} testIDPrefix="intel-tf" />
      <Button title="Open Decision Report" icon="document-text-outline" fullWidth onPress={() => router.push(`/decision?underlying=${underlying}&timeframe=${tf}`)} testID="open-decision-report" />
      <QueryState query={q}>{(d) => <DashboardView data={d} />}</QueryState>
    </Screen>
  );
}
