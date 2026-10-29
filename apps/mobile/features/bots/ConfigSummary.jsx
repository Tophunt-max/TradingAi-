import { AppText, Card, CardHeader, KeyValue } from '../../components/ui';
import { describeCondition, describeLevel } from './config';

export function ConfigSummary({ config, strategyName }) {
  const c = config;
  return (
    <>
      <Card>
        <CardHeader title="Strategy & instrument" />
        <KeyValue label="Strategy" value={strategyName ?? c.strategy_id} />
        <KeyValue label="Timeframe" value={c.timeframe} />
        <KeyValue label="Instrument" value={`${c.instrument.underlying} ${c.instrument.option_type} · buy only`} />
        <KeyValue label="Strike / expiry" value={`${c.instrument.strike_selection.replace('_', ' ')} · ${c.instrument.expiry_selection.toLowerCase()}`} />
      </Card>
      <Card>
        <CardHeader title={`Entry (${c.entry.logic === 'ALL' ? 'all must match' : 'any may match'})`} />
        {c.entry.conditions.length ? (
          c.entry.conditions.map((cond, i) => <AppText key={i} tone="muted">• {describeCondition(cond)}</AppText>)
        ) : (
          <AppText tone="warning">No entry conditions defined.</AppText>
        )}
      </Card>
      <Card>
        <CardHeader title="Exit & orders" />
        <KeyValue label="Stop-loss" value={describeLevel(c.exit.stop_loss)} />
        <KeyValue label="Target" value={describeLevel(c.exit.target)} />
        <KeyValue label="Trailing stop" value={describeLevel(c.exit.trailing_stop)} />
        <KeyValue label="Square-off" value={`${c.exit.square_off_time_ist} IST`} />
        <KeyValue label="Order type" value={c.order.order_type === 'LIMIT' ? `Limit (±${Number(c.order.limit_offset_pct)}%)` : 'Market'} />
      </Card>
      <Card>
        <CardHeader title="Risk & session" />
        <KeyValue label="Capital allocation" value={`₹${Number(c.risk.capital_allocation).toLocaleString('en-IN')}`} />
        <KeyValue label="Risk per trade" value={`₹${Number(c.risk.risk_per_trade).toLocaleString('en-IN')}`} />
        <KeyValue label="Max entries / day" value={c.risk.max_entries_per_day} />
        <KeyValue label="Max open positions" value={c.risk.max_open_positions} />
        <KeyValue label="Re-entry after SL" value={c.risk.re_entry_after_stop_loss ? 'Allowed' : 'Disabled'} />
        <KeyValue label="Session" value={`${c.session.start_ist}–${c.session.end_ist} IST · cooldown ${c.session.cooldown_minutes}m`} />
        <KeyValue label="Mode" value="PAPER" tone="primary" />
      </Card>
    </>
  );
}
