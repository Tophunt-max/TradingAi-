import { useEffect, useState } from 'react';
import { View } from 'react-native';
import { Stack, router, useLocalSearchParams } from 'expo-router';
import { Ionicons } from '@expo/vector-icons';
import { AppText, Badge, Button, Card, CardHeader, ConfirmDialog, KeyValue, ModeBadge, NoticeBanner, QueryState, Screen, SegmentedControl, StatTile, StateView } from '../../../components/ui';
import { useTheme } from '../../../components/theme/ThemeProvider';
import { ConfigSummary } from '../../../features/bots/ConfigSummary';
import { HEALTH, STATUS_TONE, actionsFor, isEditable } from '../../../features/bots/health';
import { useBot, useBotAction, useBotFeed, useOrders, usePositions, useValidateBot } from '../../../features/trading/api';
import { formatINR, formatIST } from '../../../services/format';
import { OrderCard, PositionCard } from '../../../features/portfolio/Lists';

const ICON = { PASS: ['checkmark-circle', 'positive'], FAIL: ['close-circle', 'negative'], WARN: ['alert-circle', 'warning'] };
const ACTION = {
  start: { title: 'Start', icon: 'play', variant: 'primary' },
  resume: { title: 'Resume', icon: 'play', variant: 'primary' },
  pause: { title: 'Pause', icon: 'pause', variant: 'secondary' },
  stop: { title: 'Stop', icon: 'stop', variant: 'danger' },
};
const CONFIRM = {
  start: ['Start bot in paper mode?', 'The worker evaluates your entry conditions on every closed candle. Each signal is checked by the global risk manager and, if allowed, becomes a simulated paper order. No real money or broker is involved.'],
  resume: ['Resume bot?', 'Activation checks run again. The bot continues with the configuration version it started with.'],
  pause: ['Pause bot?', 'The worker stops evaluating this bot until you resume it.'],
  stop: ['Stop bot?', 'The current run ends. You can edit the bot and start a new run later.'],
};
const LEVEL_TONE = { INFO: 'textMuted', WARN: 'warning', ERROR: 'negative' };

function CheckRow({ c }) {
  const theme = useTheme();
  const [icon, tone] = ICON[c.status];
  return (
    <View style={{ flexDirection: 'row', gap: 10, paddingVertical: 8, borderBottomWidth: 1, borderColor: theme.colors.borderSubtle }}>
      <Ionicons name={icon} size={18} color={theme.colors[tone]} style={{ marginTop: 2 }} />
      <View style={{ flex: 1 }}>
        <AppText variant="bodyStrong">{c.label}</AppText>
        <AppText variant="caption" tone="muted">{c.message}</AppText>
      </View>
    </View>
  );
}

function ValidationCard({ report }) {
  const act = report.checks.filter((c) => c.required_for === 'ACTIVATION');
  const ord = report.checks.filter((c) => c.required_for === 'ORDERS');
  return (
    <Card testID="validation-report">
      <CardHeader
        title="Review & validate"
        subtitle={`Version ${report.version}`}
        right={<Badge label={report.can_activate ? `READY (${report.activation_mode.replace('_', '-')})` : 'BLOCKED'} tone={report.can_activate ? 'positive' : 'negative'} />}
      />
      <AppText variant="label" tone="muted">NEEDED TO START</AppText>
      {act.map((c) => <CheckRow key={c.key} c={c} />)}
      <AppText variant="label" tone="muted" style={{ marginTop: 10 }}>NEEDED TO PLACE ORDERS</AppText>
      {ord.map((c) => <CheckRow key={c.key} c={c} />)}
      <Badge
        label={report.can_place_orders ? (report.activation_mode === 'PAPER' ? 'PAPER ORDERS ALLOWED' : 'ORDERS ALLOWED') : 'ORDERS BLOCKED'}
        tone={report.can_place_orders ? 'positive' : 'warning'}
        testID="orders-badge"
      />
      <AppText variant="caption" tone="faint">Live orders stay disabled. Every paper entry still passes the global risk manager at the moment it is placed.</AppText>
    </Card>
  );
}

function HealthCard({ b }) {
  const h = HEALTH[b.health] ?? HEALTH.NOT_RUNNING;
  const r = b.run;
  return (
    <Card testID="bot-health">
      <CardHeader title="Runtime" subtitle={h.label === 'NOT RUNNING' ? 'Not running' : `Run started ${formatIST(r?.started_at, { withDate: true })} IST`} right={<Badge label={h.label} tone={h.tone} />} />
      {b.health_detail ? <AppText variant="caption" tone="muted">{b.health_detail}</AppText> : null}
      {r ? (
        <>
          <KeyValue label="Mode" value={r.mode.replace('_', '-')} tone="primary" />
          <KeyValue label="Config version" value={`v${r.config_version}`} />
          <KeyValue label="Last worker pass" value={r.last_heartbeat_at ? `${formatIST(r.last_heartbeat_at, { withDate: true })} IST` : '—'} />
          <KeyValue label="Last evaluated candle" value={r.last_bar_ts ? `${formatIST(r.last_bar_ts, { withDate: true })} IST` : '—'} />
          <KeyValue label="Signals this run" value={r.signals_count} />
          {r.last_error ? <KeyValue label="Last error" value={r.last_error} tone="negative" /> : null}
          {r.status === 'ENDED' && r.end_reason ? <KeyValue label="Ended" value={r.end_reason} /> : null}
        </>
      ) : null}
    </Card>
  );
}

function BotTrades({ id }) {
  const open = usePositions('OPEN', id);
  const closed = usePositions('CLOSED', id);
  const rows = [...(open.data?.items ?? []), ...(closed.data?.items ?? [])];
  if (open.isLoading || closed.isLoading) return <AppText tone="muted">Loading…</AppText>;
  if (!rows.length) return <AppText tone="muted">No paper trades yet. Trades appear when a signal passes the risk manager and fills.</AppText>;
  return rows.map((p) => <PositionCard key={p.id} p={p} />);
}

function BotOrders({ id }) {
  const q = useOrders(undefined, id);
  return (
    <QueryState query={q} isEmpty={(d) => !d.items.length} emptyMessage="No orders yet. Rejected entries also show here, with the risk manager's reason.">
      {(page) => page.items.map((o) => <OrderCard key={o.id} o={o} />)}
    </QueryState>
  );
}

function Feed({ id }) {
  const [tab, setTab] = useState('signals');
  return (
    <Card testID="bot-feed">
      <SegmentedControl
        value={tab}
        onChange={setTab}
        testID="bot-feed-tabs"
        options={[{ value: 'signals', label: 'Signals' }, { value: 'trades', label: 'Trades' }, { value: 'orders', label: 'Orders' }, { value: 'events', label: 'Logs' }, { value: 'runs', label: 'Runs' }]}
      />
      <View style={{ marginTop: 10, gap: 8 }}>
        {tab === 'trades' ? <BotTrades id={id} /> : tab === 'orders' ? <BotOrders id={id} /> : <FeedList id={id} tab={tab} />}
      </View>
    </Card>
  );
}

function FeedList({ id, tab }) {
  const theme = useTheme();
  const q = useBotFeed(id, tab);
  return (
    <>
        <QueryState query={q} isEmpty={(d) => !d.length} emptyMessage={tab === 'signals' ? 'No signals yet. Signals appear when all entry conditions are met on a closed candle.' : 'Nothing yet.'}>
          {(rows) =>
            rows.map((x) =>
              tab === 'signals' ? (
                <View key={x.id} style={{ paddingVertical: 8, borderBottomWidth: 1, borderColor: theme.colors.borderSubtle }}>
                  <View style={{ flexDirection: 'row', justifyContent: 'space-between' }}>
                    <AppText variant="bodyStrong">{x.side} at {Number(x.price).toFixed(2)}</AppText>
                    <Badge label={x.data_source} tone={x.data_source === 'DEMO' ? 'info' : 'positive'} />
                  </View>
                  <AppText variant="caption" tone="muted">{formatIST(x.bar_ts, { withDate: true })} IST · {x.regime?.replace('_', ' ') ?? '—'} · v{x.config_version}</AppText>
                  {x.conditions.map((c) => <AppText key={c.label} variant="caption" tone="faint">✓ {c.label}</AppText>)}
                </View>
              ) : tab === 'events' ? (
                <View key={x.id} style={{ paddingVertical: 6, borderBottomWidth: 1, borderColor: theme.colors.borderSubtle }}>
                  <AppText variant="caption" style={{ color: theme.colors[LEVEL_TONE[x.level]], fontWeight: '700' }}>{x.kind} · {formatIST(x.ts, { withDate: true })}</AppText>
                  <AppText variant="caption" tone="muted">{x.message}</AppText>
                </View>
              ) : (
                <View key={x.id} style={{ paddingVertical: 6, borderBottomWidth: 1, borderColor: theme.colors.borderSubtle }}>
                  <AppText variant="bodyStrong">{x.status} · v{x.config_version}</AppText>
                  <AppText variant="caption" tone="muted">
                    {formatIST(x.started_at, { withDate: true })} → {x.ended_at ? formatIST(x.ended_at, { withDate: true }) : 'now'} · {x.signals_count} signals{x.end_reason ? ` · ${x.end_reason}` : ''}
                  </AppText>
                </View>
              ),
            )
          }
        </QueryState>
    </>
  );
}

export default function BotDetail() {
  const { id, validate: autoValidate } = useLocalSearchParams();
  const bot = useBot(id);
  const action = useBotAction();
  const validate = useValidateBot();
  // The action stays set while the dialog fades out, so its text never goes blank.
  const [confirm, setConfirmAction] = useState('start');
  const [confirmOpen, setConfirmOpen] = useState(false);
  const setConfirm = (a) => {
    if (a) setConfirmAction(a);
    setConfirmOpen(!!a);
  };
  const [notice, setNotice] = useState(null);

  useEffect(() => {
    if (autoValidate === '1' && id) validate.mutate(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autoValidate, id]);

  const run = (name) => {
    setNotice(null);
    action.mutate(
      { id, action: name },
      {
        onError: (e) => setNotice(e.message),
        onSuccess: (data) => {
          if (name === 'clone') router.replace(`/bots/${data.id}`);
          else if (['start', 'resume'].includes(name)) validate.mutate(id);
        },
        onSettled: () => setConfirm(null),
      },
    );
  };

  return (
    <Screen edges={['bottom']} refreshing={bot.isRefetching} onRefresh={bot.refetch}>
      <QueryState query={bot}>
        {(b) => {
          const editable = isEditable(b.status);
          return (
            <>
              <Stack.Screen options={{ title: b.name }} />
              <View style={{ flexDirection: 'row', gap: 6, flexWrap: 'wrap' }}>
                <Badge label={b.status} tone={STATUS_TONE[b.status]} testID="bot-status" />
                <ModeBadge live={b.mode === 'LIVE'} />
                <Badge label={b.creation_method === 'AI' ? 'AI GENERATED' : b.creation_method} tone={b.creation_method === 'AI' ? 'info' : 'neutral'} />
                <Badge label={`v${b.current_version}`} />
              </View>
              <AppText variant="caption" tone="faint">
                {b.underlying} · {b.strategy_name} · {b.timeframe}
                {b.status_reason ? ` · ${b.status_reason}` : ''}
              </AppText>

              <View style={{ flexDirection: 'row', gap: 10, flexWrap: 'wrap' }}>
                <StatTile label="Today P&L" value={b.today_pnl == null ? '—' : formatINR(b.today_pnl)} tone={Number(b.today_pnl) > 0 ? 'positive' : Number(b.today_pnl) < 0 ? 'negative' : 'default'} sub={b.today_pnl == null ? 'No paper trades today' : 'Paper, net of charges'} />
                <StatTile label="Signals today" value={b.signals_today} sub={b.last_signal ?? 'No signals yet'} />
              </View>

              <View style={{ flexDirection: 'row', gap: 8, flexWrap: 'wrap' }}>
                {actionsFor(b.status).map((a) => (
                  <Button key={a} title={ACTION[a].title} icon={ACTION[a].icon} size="sm" variant={ACTION[a].variant} onPress={() => setConfirm(a)} testID={`bot-${a}`} />
                ))}
              </View>
              {notice ? <NoticeBanner tone="danger" testID="bot-notice">{notice}</NoticeBanner> : null}

              <HealthCard b={b} />

              {validate.data ? (
                <ValidationCard report={validate.data} />
              ) : (
                <Button title="Run validation" icon="shield-checkmark-outline" variant="secondary" fullWidth loading={validate.isPending} onPress={() => validate.mutate(id)} testID="bot-validate" />
              )}
              {validate.error ? <AppText tone="negative">{validate.error.message}</AppText> : null}

              <Feed id={id} />
              <ConfigSummary config={b.config} strategyName={b.strategy_name} />

              <View style={{ flexDirection: 'row', gap: 8, flexWrap: 'wrap' }}>
                <Button title="Edit" icon="create-outline" size="sm" variant="secondary" disabled={!editable} onPress={() => router.push({ pathname: '/bots/builder', params: { id } })} />
                <Button title="Clone" icon="copy-outline" size="sm" variant="secondary" onPress={() => run('clone')} />
                <Button title="Delete" icon="trash-outline" size="sm" variant="danger" disabled={!editable} onPress={() => setConfirm('delete')} />
              </View>
              {!editable ? <AppText variant="caption" tone="faint">Stop the bot to edit or delete it.</AppText> : null}
              <ConfirmDialog
                visible={confirmOpen}
                title={confirm === 'delete' ? 'Delete bot?' : CONFIRM[confirm][0]}
                message={confirm === 'delete' ? `"${b.name}", its ${b.versions.length} configuration versions and its signals and logs will be deleted.` : CONFIRM[confirm][1]}
                confirmLabel={confirm === 'delete' ? 'Delete' : ACTION[confirm].title}
                destructive={confirm === 'delete' || confirm === 'stop'}
                loading={action.isPending}
                onCancel={() => setConfirm(null)}
                onConfirm={() =>
                  confirm === 'delete'
                    ? action.mutate({ id, action: 'delete' }, { onSuccess: () => router.replace('/bots'), onSettled: () => setConfirm(null) })
                    : run(confirm)
                }
              />
            </>
          );
        }}
      </QueryState>
      {bot.isError && bot.error?.status === 404 ? <StateView kind="empty" title="Bot not found" /> : null}
    </Screen>
  );
}
