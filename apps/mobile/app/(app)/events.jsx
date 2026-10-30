import { useState } from 'react';
import { View } from 'react-native';
import { AppText, Badge, Card, CardHeader, NoticeBanner, QueryState, Screen, SegmentedControl, StateView } from '../../components/ui';
import { useTheme } from '../../components/theme/ThemeProvider';
import { useEventRisk, useEvents } from '../../features/learning/api';
import { RISK_TONE, calendarItems } from '../../features/learning/format';
import { formatIST } from '../../services/format';

const IMPORTANCE_TONE = { HIGH: 'negative', MEDIUM: 'warning', LOW: 'neutral' };

function RiskCard({ symbol }) {
  const q = useEventRisk(symbol);
  return (
    <QueryState query={q}>
      {(r) => (
        <Card testID="event-risk">
          <CardHeader title={`Event risk · ${symbol}`} subtitle={`Window ${r.window.before_minutes} min before, ${r.window.after_minutes} min after`} right={<Badge label={r.level} tone={RISK_TONE[r.level]} testID="event-risk-level" />} />
          {r.action !== 'NONE' ? <AppText tone={r.action === 'NO_TRADE' ? 'negative' : 'warning'}>Action: {r.action.replace('_', ' ')}</AppText> : null}
          {r.reasons.map((t) => <AppText key={t} variant="caption" tone="muted">• {t}</AppText>)}
        </Card>
      )}
    </QueryState>
  );
}

function Calendar({ symbol }) {
  const theme = useTheme();
  const q = useEvents(symbol);
  return (
    <QueryState query={q}>
      {(d) => {
        const items = calendarItems(d.events, d.calendar);
        return (
          <Card testID="event-calendar">
            <CardHeader title="Calendar" subtitle={`As of ${formatIST(d.as_of, { withDate: true })} IST`} />
            {items.length ? (
              items.map((it) => (
                <View key={it.key} style={{ paddingVertical: 8, borderBottomWidth: 1, borderColor: theme.colors.borderSubtle, gap: 4 }}>
                  <View style={{ flexDirection: 'row', gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
                    <AppText variant="bodyStrong">{it.date}</AppText>
                    <Badge label={it.category.replace('_', ' ')} />
                    {it.label ? <Badge label={it.label} tone={it.label === 'SIMULATED' ? 'info' : 'neutral'} /> : null}
                    {it.importance ? <Badge label={it.importance} tone={IMPORTANCE_TONE[it.importance]} /> : null}
                  </View>
                  <AppText variant="caption">{it.title}</AppText>
                  <AppText variant="caption" tone="faint">{it.imported ? `Imported · ${it.source}` : `Derived · ${it.source ?? '—'}`}{it.ts ? ` · ${formatIST(it.ts, { withDate: true })} IST` : ''}</AppText>
                </View>
              ))
            ) : (
              <StateView kind="empty" compact title="Nothing scheduled" message="No events are known for this window." />
            )}
            <AppText variant="caption" tone="faint">{d.note}</AppText>
          </Card>
        );
      }}
    </QueryState>
  );
}

export default function Events() {
  const [symbol, setSymbol] = useState('NIFTY');
  return (
    <Screen edges={['bottom']}>
      <SegmentedControl value={symbol} onChange={setSymbol} options={[{ value: 'NIFTY', label: 'NIFTY' }, { value: 'BANKNIFTY', label: 'BANKNIFTY' }]} />
      <RiskCard symbol={symbol} />
      <Calendar symbol={symbol} />
      <NoticeBanner tone="info">
        Expiries and configured holidays are derived. Results, policy and macro dates appear only when imported from a licensed source; none is connected. Absence of an event here is not absence of risk.
      </NoticeBanner>
    </Screen>
  );
}
