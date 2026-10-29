import { useMemo, useState } from 'react';
import { AppText, Badge, Card, CardHeader, ChipRow, DataSourceBadge, QueryState, Screen, StateView } from '../../components/ui';
import { useQuotes } from '../../features/market/api';
import { QuoteRow } from '../../features/market/QuoteRow';

const FILTERS = {
  gainers: { label: 'Top gainers', fn: (q) => Number(q.change_pct) > 0, sort: (a, b) => b.change_pct - a.change_pct },
  losers: { label: 'Top losers', fn: (q) => Number(q.change_pct) < 0, sort: (a, b) => a.change_pct - b.change_pct },
  volume: { label: 'Volume breakout', fn: (q) => q.relative_volume >= 1.5, sort: (a, b) => b.relative_volume - a.relative_volume },
  range: { label: 'Near day high', fn: (q) => (Number(q.day_high) - Number(q.last)) / Number(q.last) < 0.002, sort: (a, b) => b.change_pct - a.change_pct },
  options: { label: 'Has options', fn: (q) => q.has_options, sort: (a, b) => a.symbol.localeCompare(b.symbol) },
};

export default function Screener() {
  const [filter, setFilter] = useState('gainers');
  const q = useQuotes('ALL');
  const f = FILTERS[filter];
  const rows = useMemo(() => (q.data ? q.data.items.filter(f.fn).sort(f.sort) : []), [q.data, f]);
  return (
    <Screen edges={['bottom']} refreshing={q.isRefetching} onRefresh={q.refetch}>
      <ChipRow value={filter} onChange={setFilter} options={Object.entries(FILTERS).map(([value, x]) => ({ value, label: x.label }))} testIDPrefix="screen" />
      <QueryState query={q}>
        {(d) => (
          <Card>
            <CardHeader title={f.label} subtitle={`${rows.length} of ${d.items.length} instruments`} right={<DataSourceBadge source={d.source} />} />
            {rows.length ? (
              rows.map((it) => <QuoteRow key={it.symbol} q={it} right={filter === 'volume' ? <Badge label={`${it.relative_volume}x`} tone="info" /> : null} />)
            ) : (
              <StateView kind="empty" compact title="No matches" />
            )}
          </Card>
        )}
      </QueryState>
      <AppText variant="caption" tone="faint">OI-change and options-liquidity filters need the live option chain feed (Phase 3).</AppText>
    </Screen>
  );
}
