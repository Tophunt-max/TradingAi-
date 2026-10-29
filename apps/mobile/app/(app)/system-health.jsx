import { AppText, Badge, Card, CardHeader, ListRow, QueryState, Screen } from '../../components/ui';
import { useHealth, useSystemStatus } from '../../features/system/api';
import { formatIST } from '../../services/format';

const TONE = { ok: 'positive', degraded: 'warning', down: 'negative', not_configured: 'neutral' };
const NAMES = {
  database: 'Database',
  redis: 'Cache / rate limiter',
  market_data_worker: 'Market-data worker',
  ai_worker: 'AI model worker',
  execution_worker: 'Execution worker',
  broker: 'Broker connection',
};

export default function SystemHealth() {
  const health = useHealth();
  const status = useSystemStatus();
  return (
    <Screen edges={['bottom']} refreshing={health.isRefetching} onRefresh={health.refetch}>
      <QueryState query={health}>
        {(h) => (
          <Card>
            <CardHeader title="Components" subtitle={`Checked ${formatIST(h.checked_at)} IST`} right={<Badge label={h.status.toUpperCase()} tone={TONE[h.status]} />} />
            {h.components.map((c) => (
              <ListRow
                key={c.name}
                title={NAMES[c.name] ?? c.name}
                subtitle={[c.latency_ms != null ? `${c.latency_ms} ms` : null, c.detail].filter(Boolean).join(' · ') || undefined}
                right={<Badge label={c.status.replace('_', ' ').toUpperCase()} tone={TONE[c.status]} />}
              />
            ))}
          </Card>
        )}
      </QueryState>
      <QueryState query={status}>
        {(s) => (
          <Card>
            <CardHeader title="Environment" />
            <ListRow title="Environment" right={<Badge label={s.environment.toUpperCase()} />} />
            <ListRow title="Market data" right={<Badge label={s.market_data_mode} tone={s.is_demo_data ? 'info' : 'neutral'} />} />
            <ListRow title="Paper trading" right={<Badge label={s.paper_trading_enabled ? 'ENABLED' : 'DISABLED'} tone="primary" />} />
            <ListRow title="Live trading" right={<Badge label="LOCKED" tone="warning" />} />
            <AppText variant="caption" tone="faint">API {s.api_version}</AppText>
          </Card>
        )}
      </QueryState>
    </Screen>
  );
}
