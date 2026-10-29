import { useState } from 'react';
import { View } from 'react-native';
import { router } from 'expo-router';
import { AppText, Card, CardHeader, DataSourceBadge, ListRow, QueryState, Screen, SegmentedControl, TextField } from '../../../components/ui';
import { useMovers, useQuotes, useSearch } from '../../../features/market/api';
import { QuoteRow } from '../../../features/market/QuoteRow';
import { isStale } from '../../../features/system/api';
import { formatIST } from '../../../services/format';
import { QualityBanner } from '../../../features/market/QualityBanner';

function QuoteList({ segment }) {
  const q = useQuotes(segment);
  return (
    <QueryState query={q} isEmpty={(d) => !d.items.length}>
      {(d) => (
        <Card>
          <CardHeader title={segment === 'INDEX' ? 'Indices' : 'Stocks'} subtitle={`${d.provider} · as of ${formatIST(d.as_of)} IST`} right={<DataSourceBadge source={d.source} stale={isStale(d.as_of) || d.quality?.status === 'FAIL'} />} />
          <QualityBanner payload={d} />
          {d.items.map((it) => <QuoteRow key={it.symbol} q={it} />)}
        </Card>
      )}
    </QueryState>
  );
}

function Movers() {
  const m = useMovers(5);
  return (
    <QueryState query={m}>
      {(d) => (
        <>
          {[['Top gainers', d.gainers], ['Top losers', d.losers], ['Relative volume leaders', d.volume_leaders]].map(([title, rows]) => (
            <Card key={title}>
              <CardHeader title={title} right={<DataSourceBadge source={d.source} />} />
              {rows.length ? rows.map((it) => <QuoteRow key={it.symbol} q={it} />) : <AppText tone="muted">None right now.</AppText>}
            </Card>
          ))}
        </>
      )}
    </QueryState>
  );
}

function SearchResults({ q }) {
  const s = useSearch(q);
  return (
    <Card>
      <QueryState query={s} isEmpty={(d) => !d.length} emptyMessage={`No supported instrument matches "${q}".`}>
        {(hits) => hits.map((h) => (
          <ListRow key={h.symbol} title={h.symbol} subtitle={`${h.name} · ${h.exchange}`} icon={h.segment === 'INDEX' ? 'stats-chart-outline' : 'business-outline'} onPress={() => router.push(`/instrument/${h.symbol}`)} />
        ))}
      </QueryState>
    </Card>
  );
}

export default function Markets() {
  const [tab, setTab] = useState('INDEX');
  const [query, setQuery] = useState('');
  return (
    <Screen title="Markets" subtitle="NSE / BSE overview">
      <TextField value={query} onChangeText={setQuery} placeholder="Search stocks, indices…" leftIcon="search-outline" autoCapitalize="characters" testID="market-search" />
      {query.trim() ? (
        <SearchResults q={query} />
      ) : (
        <View style={{ gap: 16 }}>
          <SegmentedControl
            value={tab}
            onChange={setTab}
            options={[
              { value: 'INDEX', label: 'Indices' },
              { value: 'EQ', label: 'Stocks' },
              { value: 'MOVERS', label: 'Movers' },
            ]}
          />
          {tab === 'MOVERS' ? <Movers /> : <QuoteList segment={tab} />}
          <Card>
            <ListRow title="Option Chain" subtitle="NIFTY / BANKNIFTY" icon="grid-outline" onPress={() => router.push('/option-chain')} />
            <ListRow title="Market Screener" subtitle="Filters on relative volume, trend and options" icon="funnel-outline" onPress={() => router.push('/screener')} />
          </Card>
        </View>
      )}
    </Screen>
  );
}
