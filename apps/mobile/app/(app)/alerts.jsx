import { useState } from 'react';
import { Ionicons } from '@expo/vector-icons';
import { Card, ChipRow, ListRow, NoticeBanner, QueryState, Screen, StateView } from '../../components/ui';
import { useTheme } from '../../components/theme/ThemeProvider';
import { useAlerts } from '../../features/trading/api';
import { formatIST } from '../../services/format';

const ICON = { TRADES: 'swap-vertical', BOTS: 'hardware-chip-outline', RISK: 'shield-half-outline', SECURITY: 'lock-closed-outline', SYSTEM: 'cog-outline' };
const SEVERITY = { info: 'textMuted', warning: 'warning', critical: 'negative' };

export default function Alerts() {
  const theme = useTheme();
  const [cat, setCat] = useState('');
  const q = useAlerts(cat || undefined);
  return (
    <Screen edges={['bottom']} refreshing={q.isRefetching} onRefresh={q.refetch}>
      <ChipRow
        value={cat}
        onChange={setCat}
        options={[
          { value: '', label: 'All' },
          { value: 'TRADES', label: 'Trades' },
          { value: 'BOTS', label: 'Bots' },
          { value: 'RISK', label: 'Risk' },
          { value: 'SECURITY', label: 'Security' },
        ]}
      />
      <QueryState query={q}>
        {(items) =>
          items.length ? (
            <Card>
              {items.map((a) => (
                <ListRow
                  key={a.id}
                  title={a.title}
                  subtitle={`${a.body ? `${a.body} · ` : ''}${formatIST(a.created_at, { withDate: true })} IST`}
                  right={<Ionicons name={ICON[a.category]} size={18} color={theme.colors[SEVERITY[a.severity]]} />}
                />
              ))}
            </Card>
          ) : (
            <StateView kind="empty" title="No notifications" message={cat === 'TRADES' ? 'Trade alerts start with paper execution (Phase 6).' : undefined} />
          )
        }
      </QueryState>
      <NoticeBanner tone="info">Push and Telegram delivery, signal alerts and daily reports are added with the alert engine (Phase 5).</NoticeBanner>
    </Screen>
  );
}
