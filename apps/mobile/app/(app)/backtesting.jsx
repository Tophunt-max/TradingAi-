import { useMemo, useState } from 'react';
import { View } from 'react-native';
import { AppText, Button, Card, CardHeader, ChipRow, NoticeBanner, QueryState, Screen, SegmentedControl, StateView, TextField } from '../../components/ui';
import { ActiveModeHeader } from '../../features/account/ActiveModeBadge';
import { useBacktestStrategies, useMonteCarlo, useRunBacktest, useWalkForward } from '../../features/backtesting/api';
import { buildRunRequest, DEFAULT_FORM } from '../../features/backtesting/format';
import { BacktestResults, WalkForwardSummary } from '../../features/backtesting/Results';

const UNDERLYINGS = ['NIFTY', 'BANKNIFTY', 'FINNIFTY'].map((v) => ({ value: v, label: v }));
const TIMEFRAMES = ['1m', '3m', '5m', '15m', '30m', '1h'].map((v) => ({ value: v, label: v }));

function NumberField({ form, setForm, errors, name, label, testID }) {
  return (
    <TextField
      label={label}
      value={form[name]}
      onChangeText={(v) => setForm((f) => ({ ...f, [name]: v }))}
      keyboardType="decimal-pad"
      error={errors[name]}
      hint={errors[name]}
      testID={testID}
      style={{ flex: 1, minWidth: 140 }}
    />
  );
}

function ErrorBanner({ error }) {
  if (!error) return null;
  return <NoticeBanner tone="danger" icon="close-circle-outline">{error.message ?? 'The backtest failed.'}</NoticeBanner>;
}

export default function Backtesting() {
  const strategies = useBacktestStrategies();
  const run = useRunBacktest();
  const mc = useMonteCarlo();
  const wf = useWalkForward();
  const [form, setForm] = useState(DEFAULT_FORM);
  const [errors, setErrors] = useState({});
  const [tab, setTab] = useState('RESULTS');

  const options = useMemo(
    () => (strategies.data ?? []).filter((s) => s.backtestable && !s.requires_entry_conditions).map((s) => ({ value: s.id, label: s.name })),
    [strategies.data],
  );

  const submit = () => {
    const { body, errors: errs } = buildRunRequest(form);
    setErrors(errs);
    if (!body) return null;
    return body;
  };

  const onRun = () => {
    const body = submit();
    if (!body) return;
    mc.reset();
    run.mutate(body, {
      onSuccess: (res) => {
        if (res.metrics.trades > 0) mc.mutate({ run: body, simulations: 1000, seed: 7 });
      },
    });
  };

  const onWalkForward = () => {
    const body = submit();
    if (!body) return;
    wf.mutate({ run: { ...body, bars: Math.max(body.bars, 900) }, train_bars: 400, test_bars: 150, holdout_pct: 20, objective: 'net_pnl', min_trades: 3 });
  };

  return (
    <Screen edges={['bottom']} keyboard>
      <ActiveModeHeader note="Backtests are simulations: they never place orders in either account." />
      <NoticeBanner tone="warning" icon="flask-outline">
        Backtests replay closed candles bar by bar with costs, slippage and your global risk limits. The configured data source has no historical option premiums, so results are an underlying proxy, not an options backtest.
      </NoticeBanner>
      <Card>
        <CardHeader title="Setup" subtitle="Decisions use closed candles only; orders fill at the next bar" />
        <View style={{ gap: 12 }}>
          <AppText variant="label" tone="muted">STRATEGY</AppText>
          <QueryState query={strategies}>
            {() => <ChipRow options={options} value={form.strategyId} onChange={(v) => setForm((f) => ({ ...f, strategyId: v }))} testIDPrefix="bt-strategy" />}
          </QueryState>
          <AppText variant="label" tone="muted">UNDERLYING</AppText>
          <ChipRow options={UNDERLYINGS} value={form.underlying} onChange={(v) => setForm((f) => ({ ...f, underlying: v }))} testIDPrefix="bt-underlying" />
          <SegmentedControl value={form.side} onChange={(v) => setForm((f) => ({ ...f, side: v }))} options={[{ value: 'CE', label: 'CE (long proxy)' }, { value: 'PE', label: 'PE (short proxy)' }]} />
          <AppText variant="label" tone="muted">TIMEFRAME</AppText>
          <ChipRow options={TIMEFRAMES} value={form.timeframe} onChange={(v) => setForm((f) => ({ ...f, timeframe: v }))} testIDPrefix="bt-tf" />
          <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 10 }}>
            <NumberField form={form} setForm={setForm} errors={errors} name="bars" label="Bars (150–2000)" testID="bt-bars" />
            <NumberField form={form} setForm={setForm} errors={errors} name="stopPct" label="Stop % (underlying)" testID="bt-stop" />
            <NumberField form={form} setForm={setForm} errors={errors} name="targetPct" label="Target % (underlying)" testID="bt-target" />
            <NumberField form={form} setForm={setForm} errors={errors} name="spreadBps" label="Spread (bps)" testID="bt-spread" />
            <NumberField form={form} setForm={setForm} errors={errors} name="slippageTicks" label="Slippage (ticks)" testID="bt-slippage" />
            <NumberField form={form} setForm={setForm} errors={errors} name="brokerage" label="Brokerage / order (₹)" testID="bt-brokerage" />
          </View>
          <Button title="Run backtest" icon="play-outline" onPress={onRun} loading={run.isPending} fullWidth testID="bt-run" />
        </View>
      </Card>
      <SegmentedControl value={tab} onChange={setTab} options={[{ value: 'RESULTS', label: 'Results' }, { value: 'WALK_FORWARD', label: 'Walk-forward' }]} />
      {tab === 'RESULTS' ? (
        <>
          <ErrorBanner error={run.error} />
          {run.data ? <BacktestResults result={run.data} monteCarlo={mc.data} /> : !run.isPending && !run.error ? <StateView kind="empty" title="No backtest yet" message="Choose a strategy and tap Run backtest." /> : null}
          {mc.isPending ? <AppText tone="muted">Running Monte Carlo…</AppText> : null}
          <ErrorBanner error={mc.error} />
        </>
      ) : (
        <>
          <AppText tone="muted">
            Rolling windows: parameters are chosen on each train window and applied unchanged to the next test window. Only test windows are aggregated; the last 20% of bars is a holdout evaluated once.
          </AppText>
          <Button title="Run walk-forward" variant="secondary" icon="git-compare-outline" onPress={onWalkForward} loading={wf.isPending} fullWidth testID="bt-wf-run" />
          <ErrorBanner error={wf.error} />
          <WalkForwardSummary wf={wf.data} />
        </>
      )}
    </Screen>
  );
}
