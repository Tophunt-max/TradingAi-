import { View } from 'react-native';
import { AppText, Card, CardHeader, DataSourceBadge, DataTable, PnLText, QueryState } from '../../components/ui';
import { isStale, useIndices } from '../system/api';
import { formatIST } from '../../services/format';

export function IndexQuotesCard({ title = 'Indices' }) {
  const query = useIndices();
  const stale = query.data ? isStale(query.data.as_of) : false;
  return (
    <Card testID="index-quotes">
      <CardHeader
        title={title}
        subtitle={query.data ? `As of ${formatIST(query.data.as_of)} IST` : undefined}
        right={query.data ? <DataSourceBadge source={query.data.source} stale={stale} /> : null}
      />
      <QueryState query={query} isEmpty={(d) => !d?.items?.length} emptyMessage="No index data.">
        {(data) => (
          <View style={{ gap: 10 }}>
            {data.is_demo ? (
              <AppText variant="caption" tone="warning" testID="demo-disclaimer">{data.disclaimer}</AppText>
            ) : null}
            <DataTable
              keyExtractor={(r) => r.symbol}
              rows={data.items}
              columns={[
                {
                  key: 'name',
                  title: 'Index',
                  flex: 1.6,
                  render: (r) => (
                    <View>
                      <AppText variant="bodyStrong">{r.symbol}</AppText>
                      <AppText variant="caption" tone="faint">{r.exchange}</AppText>
                    </View>
                  ),
                },
                { key: 'last', title: 'Last', align: 'right', render: (r) => <AppText variant="mono">{Number(r.last).toLocaleString('en-IN', { minimumFractionDigits: 2 })}</AppText> },
                { key: 'change_pct', title: 'Chg %', align: 'right', render: (r) => <PnLText value={r.change_pct} suffix="%" /> },
              ]}
            />
          </View>
        )}
      </QueryState>
    </Card>
  );
}
