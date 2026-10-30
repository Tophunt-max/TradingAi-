import { View } from 'react-native';
import { AccountModeBadge, AppText, Badge, Card, CardHeader, DataSourceBadge, KeyValue } from '../../components/ui';
import { formatINR, formatIST } from '../../services/format';
import { STATUS_TONE } from './api';
import { DecisionBadge } from './DecisionView';

const pct = (v) => (v == null ? '—' : `${(Number(v) * 100).toFixed(0)}%`);

const TITLES = {
  regime: 'Market regime',
  forecast: 'Forecast probabilities',
  models: 'Model versions',
  calibration: 'Confidence calibration',
  prediction_history: 'Prediction history',
  strategy_performance: 'Strategy performance',
  account_results: 'Account results',
  backtests: 'Backtest results',
  risk: 'Risk-limit usage',
  data_freshness: 'Data freshness',
  broker: 'Broker connection',
  drift: 'Model drift',
  error_attribution: 'Error attribution',
  no_trade: 'Latest decision',
};

function Body({ name, s }) {
  const d = s.data;
  if (s.status !== 'OK' || d == null) return null;
  switch (name) {
    case 'regime':
      return (
        <>
          <KeyValue label="State" value={d.state} tone={d.no_trade ? 'warning' : undefined} />
          <KeyValue label="Top" value={(d.top ?? []).map((t) => `${t.state} ${pct(t.confidence)}`).join(' · ')} />
          <KeyValue label="Calibration" value={d.calibration_status} />
        </>
      );
    case 'forecast':
      return <KeyValue label="Probabilities" value={Object.entries(d.probabilities).map(([k, v]) => `${k} ${pct(v)}`).join(' · ')} />;
    case 'models':
      return (
        <>
          <KeyValue label="Promoted / shadow / candidates" value={`${d.promoted} / ${d.shadow} / ${d.candidates}`} />
          {d.latest ? <KeyValue label="Latest" value={`${d.latest.name} (${d.latest.status})`} /> : null}
        </>
      );
    case 'calibration':
      return <KeyValue label="ECE" value={`${d.ece} on ${d.samples} samples`} />;
    case 'prediction_history':
      return d.slice(0, 5).map((x) => (
        <View key={x.id} style={{ flexDirection: 'row', gap: 6, alignItems: 'center', paddingVertical: 2 }}>
          <DecisionBadge decision={x.decision} />
          <AppText variant="caption" tone="muted">
            {x.origin} · {x.timeframe} · {formatIST(x.decision_ts, { withDate: true })} · {x.outcome_status}
          </AppText>
        </View>
      ));
    case 'strategy_performance':
      return d.map((x) => <KeyValue key={x.strategy} label={x.strategy} value={`${x.trades} trades · ${formatINR(x.net_pnl)} · win ${pct(x.win_rate)}`} />);
    case 'account_results':
      return <KeyValue label="Realised net" value={`${formatINR(d.realized_net)} · ${d.closed_trades} trade(s)`} />;
    case 'risk':
      return (
        <>
          <KeyValue label="New entries" value={d.new_entries_allowed ? 'Allowed' : 'Blocked'} tone={d.new_entries_allowed ? 'positive' : 'warning'} />
          <KeyValue label="Daily loss used" value={`${d.daily_loss_used_pct}%`} />
          <KeyValue label="Entries · positions" value={`${d.entries} · ${d.open_positions}`} />
        </>
      );
    case 'data_freshness':
      return (
        <>
          <KeyValue label="Provider" value={`${d.provider} · ${d.provider_status ?? '—'}`} />
          <KeyValue label="Last candle age" value={`${Math.round(d.last_candle_age_seconds)} s`} />
          <KeyValue label="Quality" value={d.quality?.status} />
        </>
      );
    case 'drift':
      return d.map((x) => <KeyValue key={x.model_id} label={x.model_name} value={x.status} />);
    case 'error_attribution':
      return Object.entries(d.error_classes).map(([k, v]) => <KeyValue key={k} label={k} value={v} />);
    case 'no_trade':
      return (
        <>
          <DecisionBadge decision={d.decision} />
          {(d.reasons ?? []).slice(0, 4).map((x) => <AppText key={x} variant="caption" tone="muted">• {x}</AppText>)}
        </>
      );
    default:
      return null;
  }
}

export function DashboardSection({ name, s }) {
  return (
    <Card testID={`intel-section-${name}`}>
      <CardHeader title={TITLES[name] ?? name} subtitle={s.source} right={<Badge label={s.status.replace('_', ' ')} tone={STATUS_TONE[s.status] ?? 'neutral'} testID={`intel-status-${name}`} />} />
      {s.label ? <AppText variant="caption" tone="faint">{s.label === 'DEMO_VIRTUAL' ? 'DEMO · virtual money' : s.label}</AppText> : null}
      <Body name={name} s={s} />
      {s.reason ? <AppText variant="caption" tone="muted">{s.reason}</AppText> : null}
    </Card>
  );
}

export const SECTION_ORDER = ['regime', 'forecast', 'no_trade', 'models', 'calibration', 'prediction_history', 'strategy_performance', 'account_results', 'backtests', 'risk', 'data_freshness', 'broker', 'drift', 'error_attribution'];

export function DashboardView({ data }) {
  return (
    <View style={{ gap: 12 }}>
      <View style={{ flexDirection: 'row', gap: 6, alignItems: 'center', flexWrap: 'wrap' }}>
        <AccountModeBadge mode={data.account_mode} />
        {data.data_source ? <DataSourceBadge source={data.data_source} /> : null}
        <AppText variant="caption" tone="faint">
          {data.underlying} · {data.timeframe} · {formatIST(data.as_of)} IST
        </AppText>
      </View>
      {SECTION_ORDER.filter((k) => data.sections[k]).map((k) => (
        <DashboardSection key={k} name={k} s={data.sections[k]} />
      ))}
      <AppText variant="caption" tone="faint">{data.note}</AppText>
    </View>
  );
}
