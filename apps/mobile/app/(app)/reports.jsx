import { useState } from 'react';
import { View } from 'react-native';
import { AccountModeBadge, AppText, Button, Card, CardHeader, KeyValue, NoticeBanner, QueryState, Screen, StatTile } from '../../components/ui';
import { useTheme } from '../../components/theme/ThemeProvider';
import { useAccountMode } from '../../features/account/mode';
import { CAN_EXPORT, downloadCsv, useDailyReport, usePnl } from '../../features/reports/api';
import { CSV_EXPORTS, dayLabel, istToday, netBars, pct, shiftDay, signedINR } from '../../features/reports/format';
import { formatINR } from '../../services/format';

const toneOf = (v) => (Number(v) > 0 ? 'positive' : Number(v) < 0 ? 'negative' : 'default');

function Row({ children }) {
  return <View style={{ flexDirection: 'row', gap: 8, flexWrap: 'wrap' }}>{children}</View>;
}

function GroupList({ title, rows, testID }) {
  if (!rows?.length) return null;
  return (
    <Card testID={testID}>
      <CardHeader title={title} />
      {rows.map((g) => (
        <KeyValue key={g.key} label={`${g.key} · ${g.trades} trade${g.trades === 1 ? '' : 's'}`} value={signedINR(g.net)} tone={toneOf(g.net)} />
      ))}
    </Card>
  );
}

export function DailyReportView({ r }) {
  const t = r.totals;
  const b = r.charges_breakdown;
  return (
    <View style={{ gap: 12 }}>
      <Card testID="report-daily">
        <CardHeader title="Daily report" subtitle={`${r.mode_label} · ${r.pnl_basis}`} />
        <Row>
          <StatTile label="Trades" value={String(t.trades)} sub={`${t.wins} won · ${t.losses} lost`} />
          <StatTile label="Win rate" value={pct(t.win_rate_pct)} />
          <StatTile label="Net P&L" value={signedINR(t.net)} tone={toneOf(t.net)} />
        </Row>
        <Row>
          <StatTile label="Gross" value={signedINR(t.gross)} tone={toneOf(t.gross)} />
          <StatTile label="Charges" value={formatINR(t.charges)} />
          <StatTile label="Max drawdown" value={formatINR(t.max_drawdown)} sub="realised, trade by trade" />
        </Row>
        <KeyValue label="Entry slippage vs reference" value={`${formatINR(r.slippage.total)} over ${r.slippage.orders} order(s)`} />
        <KeyValue label="Fills today" value={String(r.fills.count)} />
        {r.note ? <AppText tone="muted">{r.note}</AppText> : null}
      </Card>
      <Card testID="report-charges">
        <CardHeader title="Charges breakdown" subtitle="All fills of the day’s trades (estimates)" />
        {['brokerage', 'stt', 'exchange_txn', 'sebi', 'stamp_duty', 'gst'].map((k) => (
          <KeyValue key={k} label={k.replace('_', ' ').toUpperCase()} value={formatINR(b[k])} />
        ))}
        <KeyValue label="TOTAL" value={formatINR(b.total)} />
        {r.charges_reconciliation ? (
          <KeyValue label="Broker-reported vs estimate" value={`${formatINR(r.charges_reconciliation.broker_reported)} (diff ${signedINR(r.charges_reconciliation.diff)})`} />
        ) : null}
      </Card>
      {r.risk_events.length ? (
        <Card testID="report-risk-events">
          <CardHeader title="Risk events" />
          {r.risk_events.map((e) => (
            <KeyValue key={`${e.type}-${e.at}`} label={`${e.type.replace(/_/g, ' ')} · ${e.at.slice(11, 16)} IST`} value={e.scope === 'ALL_ACCOUNTS' ? 'All accounts' : e.scope} />
          ))}
        </Card>
      ) : null}
      <GroupList title="By bot" rows={r.by_bot} testID="report-by-bot" />
      <GroupList title="By strategy" rows={r.by_strategy} testID="report-by-strategy" />
      <GroupList title="By exit reason" rows={r.by_exit_reason} testID="report-by-exit" />
    </View>
  );
}

export function PnlView({ p }) {
  const theme = useTheme();
  const bars = netBars(p.daily);
  return (
    <View style={{ gap: 12 }}>
      <Card testID="report-pnl">
        <CardHeader title="P&L analytics" subtitle={`${dayLabel(p.from)} – ${dayLabel(p.to)} · ${p.mode_label}`} />
        <Row>
          <StatTile label="Cumulative net" value={signedINR(p.cumulative_net)} tone={toneOf(p.cumulative_net)} />
          <StatTile label="Max drawdown" value={formatINR(p.max_drawdown)} />
          <StatTile label="Current drawdown" value={formatINR(p.current_drawdown)} />
        </Row>
        {bars.length === 0 ? <AppText tone="muted">No closed trades in this period.</AppText> : null}
        {bars.map((d) => (
          <View key={d.day} style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }} testID={`pnl-bar-${d.day}`}>
            <AppText variant="caption" tone="muted" style={{ width: 74 }}>{d.day.slice(5)}</AppText>
            <View style={{ flex: 1, height: 10, backgroundColor: theme.colors.surfaceRaised, borderRadius: 5 }}>
              <View style={{ width: `${d.pct}%`, height: 10, borderRadius: 5, backgroundColor: d.positive ? theme.colors.positive : theme.colors.negative }} />
            </View>
            <AppText variant="caption" style={{ width: 92, textAlign: 'right' }}>{signedINR(d.net)}</AppText>
          </View>
        ))}
      </Card>
      <GroupList title="By exit reason (period)" rows={p.by_exit_reason} testID="pnl-by-exit" />
      <GroupList title="By weekday" rows={p.by_weekday} testID="pnl-by-weekday" />
      <GroupList title="By instrument" rows={p.by_instrument} testID="pnl-by-instrument" />
    </View>
  );
}

export function ExportCard({ mode }) {
  const [busy, setBusy] = useState(null);
  const [error, setError] = useState(null);
  const items = CSV_EXPORTS.filter((x) => !(x.demoOnly && mode !== 'DEMO'));
  return (
    <Card testID="report-exports">
      <CardHeader title="CSV export" subtitle={`${mode} account · IST timestamps · account_mode column`} />
      {!CAN_EXPORT ? <NoticeBanner testID="export-web-only">Export is available on the web app. This device has no file-sharing support installed.</NoticeBanner> : null}
      <Row>
        {items.map((x) => (
          <Button
            key={x.id}
            testID={`export-${x.id}`}
            title={x.label}
            size="sm"
            variant="secondary"
            disabled={!CAN_EXPORT}
            loading={busy === x.id}
            onPress={async () => {
              setBusy(x.id);
              setError(null);
              try {
                await downloadCsv(x.id, mode);
              } catch (e) {
                setError(e?.message ?? 'Export failed.');
              } finally {
                setBusy(null);
              }
            }}
          />
        ))}
      </Row>
      {error ? <AppText tone="negative">{error}</AppText> : null}
    </Card>
  );
}

export default function Reports() {
  const { mode } = useAccountMode();
  const today = istToday();
  const [day, setDay] = useState(today);
  const daily = useDailyReport(day, mode);
  const pnl = usePnl(shiftDay(day, -29), day, mode);
  return (
    <Screen edges={['bottom']}>
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
        <AccountModeBadge mode={mode} />
        <AppText tone="muted" variant="caption">Reports cover the active account only. DEMO and REAL are never combined.</AppText>
      </View>
      <Card testID="report-day-picker">
        <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
          <Button title="Previous" icon="chevron-back" size="sm" variant="secondary" testID="day-prev" onPress={() => setDay(shiftDay(day, -1))} />
          <AppText variant="bodyStrong" testID="day-label">{dayLabel(day)}</AppText>
          <Button title="Next" icon="chevron-forward" size="sm" variant="secondary" testID="day-next" disabled={day >= today} onPress={() => setDay(shiftDay(day, 1))} />
        </View>
      </Card>
      <QueryState query={daily}>{(r) => <DailyReportView r={r} />}</QueryState>
      <QueryState query={pnl}>{(p) => <PnlView p={p} />}</QueryState>
      <ExportCard mode={mode} />
    </Screen>
  );
}
