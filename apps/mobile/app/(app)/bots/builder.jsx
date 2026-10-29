import { useMemo, useState } from 'react';
import { View } from 'react-native';
import { Stack, router, useLocalSearchParams } from 'expo-router';
import {
  AppText, Button, Card, CardHeader, ChipRow, NoticeBanner, QueryState, Screen, SegmentedControl, Select, Stepper, TextField, ToggleRow,
} from '../../../components/ui';
import { useTheme } from '../../../components/theme/ThemeProvider';
import { describeError } from '../../../features/auth/api';
import { ConfigSummary } from '../../../features/bots/ConfigSummary';
import { INDICATORS, OPERATORS, TIMEFRAMES, blankConfig, describeCondition, setIn } from '../../../features/bots/config';
import { useBot, useRisk, useSaveBot, useStrategies } from '../../../features/trading/api';

const STEPS = ['Basic', 'Strategy', 'Entry', 'Exit', 'Risk', 'Review'];

function LevelField({ label, value, onChange, allowOff }) {
  return (
    <View style={{ gap: 8 }}>
      <SegmentedControl
        value={value ? value.type : 'OFF'}
        onChange={(t) => onChange(t === 'OFF' ? null : { type: t, value: value?.value ?? '20' })}
        options={[...(allowOff ? [{ value: 'OFF', label: 'Off' }] : []), { value: 'PERCENT', label: '% of premium' }, { value: 'POINTS', label: 'Points' }]}
      />
      {value ? <TextField label={label} value={String(value.value)} keyboardType="decimal-pad" onChangeText={(v) => onChange({ ...value, value: v })} /> : null}
    </View>
  );
}

function ConditionEditor({ conditions, onChange }) {
  const theme = useTheme();
  const [draft, setDraft] = useState({ indicator: 'RSI', operator: 'GT', value: '55' });
  const needsValue = ['GT', 'LT'].includes(draft.operator);
  return (
    <View style={{ gap: 12 }}>
      {conditions.length === 0 ? <AppText tone="warning">Add at least one entry condition.</AppText> : null}
      {conditions.map((c, i) => (
        <View key={i} style={{ flexDirection: 'row', alignItems: 'center', gap: 8, padding: 10, borderRadius: 10, backgroundColor: theme.colors.surfaceRaised }}>
          <AppText style={{ flex: 1 }}>{describeCondition(c)}</AppText>
          <Button title="Remove" size="sm" variant="ghost" onPress={() => onChange(conditions.filter((_, j) => j !== i))} />
        </View>
      ))}
      <Card style={{ gap: 10 }}>
        <AppText variant="bodyStrong">Add condition</AppText>
        <Select label="Indicator" value={draft.indicator} onChange={(v) => setDraft({ ...draft, indicator: v })} options={INDICATORS} />
        <Select label="Operator" value={draft.operator} onChange={(v) => setDraft({ ...draft, operator: v })} options={OPERATORS} />
        {needsValue ? <TextField label="Value" value={draft.value} keyboardType="decimal-pad" onChangeText={(v) => setDraft({ ...draft, value: v })} /> : null}
        <Button
          title="Add"
          size="sm"
          variant="secondary"
          icon="add"
          disabled={conditions.length >= 8 || (needsValue && !Number.isFinite(Number(draft.value)))}
          onPress={() => onChange([...conditions, { indicator: draft.indicator, operator: draft.operator, params: {}, value: needsValue ? Number(draft.value) : null }])}
        />
      </Card>
    </View>
  );
}

function Builder({ initial, botId, strategies, limits, creationMethod }) {
  const [step, setStep] = useState(0);
  const [cfg, setCfg] = useState(initial);
  const save = useSaveBot();
  const set = (path) => (v) => setCfg((c) => setIn(c, path, v));
  const strategy = strategies.find((s) => s.id === cfg.strategy_id);

  const stepError = useMemo(() => {
    if (step === 0 && cfg.name.trim().length < 3) return 'Name needs at least 3 characters.';
    if (step === 2 && cfg.entry.conditions.length === 0) return 'Add at least one entry condition.';
    if (step === 3 && !(Number(cfg.exit.stop_loss.value) > 0)) return 'A stop-loss is required.';
    if (step === 4 && Number(cfg.risk.risk_per_trade) > Number(cfg.risk.capital_allocation)) return 'Risk per trade cannot exceed capital.';
    return null;
  }, [step, cfg]);

  const submit = () =>
    save.mutate(
      { id: botId, creation_method: creationMethod, config: { ...cfg, name: cfg.name.trim() } },
      { onSuccess: (bot) => router.replace(`/bots/${bot.id}`) },
    );

  return (
    <Screen keyboard edges={['bottom']}>
      <Stepper steps={STEPS} current={step} />
      {step === 0 ? (
        <Card style={{ gap: 12 }}>
          <TextField label="Bot name" value={cfg.name} onChangeText={set(['name'])} testID="bot-name" />
          <TextField label="Description (optional)" value={cfg.description ?? ''} onChangeText={set(['description'])} multiline />
          <Select label="Instrument" value={cfg.instrument.underlying} onChange={set(['instrument', 'underlying'])} options={[{ value: 'NIFTY', label: 'NIFTY 50 options' }, { value: 'BANKNIFTY', label: 'BANKNIFTY options' }]} />
          <SegmentedControl value={cfg.instrument.option_type} onChange={set(['instrument', 'option_type'])} options={[{ value: 'CE', label: 'CE (Call)' }, { value: 'PE', label: 'PE (Put)' }]} />
          <Select label="Strike" value={cfg.instrument.strike_selection} onChange={set(['instrument', 'strike_selection'])} options={[{ value: 'ATM', label: 'At the money' }, { value: 'ITM_1', label: '1 strike in the money' }, { value: 'OTM_1', label: '1 strike out of the money' }, { value: 'OTM_2', label: '2 strikes out of the money' }]} />
          <Select label="Expiry" value={cfg.instrument.expiry_selection} onChange={set(['instrument', 'expiry_selection'])} options={[{ value: 'NEAREST', label: 'Nearest expiry' }, { value: 'NEXT', label: 'Next expiry' }]} />
          <AppText variant="caption" tone="faint">Options buying only. Selling and unlimited-risk strategies are not supported.</AppText>
        </Card>
      ) : null}
      {step === 1 ? (
        <Card style={{ gap: 12 }}>
          <Select
            label="Strategy"
            value={cfg.strategy_id}
            onChange={(id) => {
              const s = strategies.find((x) => x.id === id);
              setCfg((c) => ({ ...c, strategy_id: id, timeframe: s.timeframes.includes(c.timeframe) ? c.timeframe : s.timeframes[0], entry: { ...c.entry, conditions: s.default_entry.length ? s.default_entry : c.entry.conditions } }));
            }}
            options={strategies.map((s) => ({ value: s.id, label: s.name }))}
          />
          {strategy ? <AppText variant="caption" tone="muted">{strategy.summary}</AppText> : null}
          {strategy?.research_only ? <NoticeBanner tone="danger">Research only: this strategy can be saved but never activated.</NoticeBanner> : null}
          <AppText variant="label" tone="muted">TIMEFRAME</AppText>
          <ChipRow value={cfg.timeframe} onChange={set(['timeframe'])} options={TIMEFRAMES.map((t) => ({ value: t, label: t }))} />
        </Card>
      ) : null}
      {step === 2 ? (
        <>
          <SegmentedControl value={cfg.entry.logic} onChange={set(['entry', 'logic'])} options={[{ value: 'ALL', label: 'All conditions' }, { value: 'ANY', label: 'Any condition' }]} />
          <ConditionEditor conditions={cfg.entry.conditions} onChange={set(['entry', 'conditions'])} />
        </>
      ) : null}
      {step === 3 ? (
        <Card style={{ gap: 14 }}>
          <CardHeader title="Stop-loss" />
          <LevelField label="Stop-loss value" value={cfg.exit.stop_loss} onChange={set(['exit', 'stop_loss'])} />
          <CardHeader title="Target" />
          <LevelField label="Target value" value={cfg.exit.target} onChange={set(['exit', 'target'])} />
          <CardHeader title="Trailing stop" />
          <LevelField label="Trailing value" value={cfg.exit.trailing_stop} onChange={set(['exit', 'trailing_stop'])} allowOff />
          <TextField label="Square-off time (IST)" value={cfg.exit.square_off_time_ist} onChangeText={set(['exit', 'square_off_time_ist'])} hint={`Global square-off: ${limits.square_off_time_ist} IST`} />
          <SegmentedControl value={cfg.order.order_type} onChange={set(['order', 'order_type'])} options={[{ value: 'LIMIT', label: 'Limit order' }, { value: 'MARKET', label: 'Market order' }]} />
        </Card>
      ) : null}
      {step === 4 ? (
        <Card style={{ gap: 12 }}>
          <TextField label="Capital allocation (₹)" value={String(cfg.risk.capital_allocation)} keyboardType="decimal-pad" onChangeText={set(['risk', 'capital_allocation'])} hint={`Global capital ₹${Number(limits.capital).toLocaleString('en-IN')}`} />
          <TextField label="Risk per trade (₹)" value={String(cfg.risk.risk_per_trade)} keyboardType="decimal-pad" onChangeText={set(['risk', 'risk_per_trade'])} hint={`Global maximum ₹${Number(limits.max_risk_per_trade)}`} />
          <TextField label="Max entries per day" value={String(cfg.risk.max_entries_per_day)} keyboardType="number-pad" onChangeText={(v) => set(['risk', 'max_entries_per_day'])(Number(v) || 1)} hint={`Global maximum ${limits.max_entries_per_day}, shared by all bots`} />
          <ToggleRow title="Re-entry after stop-loss" value={cfg.risk.re_entry_after_stop_loss} onValueChange={set(['risk', 're_entry_after_stop_loss'])} />
          <View style={{ flexDirection: 'row', gap: 10 }}>
            <View style={{ flex: 1 }}><TextField label="Session start" value={cfg.session.start_ist} onChangeText={set(['session', 'start_ist'])} /></View>
            <View style={{ flex: 1 }}><TextField label="Session end" value={cfg.session.end_ist} onChangeText={set(['session', 'end_ist'])} /></View>
          </View>
          <TextField label="Cooldown (minutes)" value={String(cfg.session.cooldown_minutes)} keyboardType="number-pad" onChangeText={(v) => set(['session', 'cooldown_minutes'])(Number(v) || 0)} />
        </Card>
      ) : null}
      {step === 5 ? <ConfigSummary config={cfg} strategyName={strategy?.name} /> : null}

      {stepError ? <AppText tone="warning">{stepError}</AppText> : null}
      {save.error ? <AppText tone="negative" accessibilityRole="alert" testID="builder-error">{describeError(save.error)}</AppText> : null}
      <View style={{ flexDirection: 'row', gap: 10 }}>
        {step > 0 ? <View style={{ flex: 1 }}><Button title="Back" variant="secondary" fullWidth onPress={() => setStep(step - 1)} /></View> : null}
        <View style={{ flex: 1 }}>
          {step < STEPS.length - 1 ? (
            <Button title="Next" icon="arrow-forward" fullWidth disabled={!!stepError} onPress={() => setStep(step + 1)} testID="builder-next" />
          ) : (
            <Button title={botId ? 'Save new version' : 'Save as draft'} fullWidth loading={save.isPending} onPress={submit} testID="builder-save" />
          )}
        </View>
      </View>
    </Screen>
  );
}

export default function BuilderScreen() {
  const { id, template } = useLocalSearchParams();
  const strategies = useStrategies();
  const risk = useRisk();
  const bot = useBot(id);
  const ready = strategies.data && risk.data && (!id || bot.data);
  const query = !strategies.data ? strategies : !risk.data ? risk : id ? bot : strategies;
  return (
    <>
      <Stack.Screen options={{ title: id ? 'Edit Bot' : 'Manual Bot Builder' }} />
      {ready ? (
        <Builder
          key={id ?? template ?? 'new'}
          botId={id}
          strategies={strategies.data}
          limits={risk.data.limits}
          creationMethod={template ? 'TEMPLATE' : 'MANUAL'}
          initial={id ? bot.data.config : blankConfig(strategies.data.find((s) => s.id === (template ?? 'custom')), risk.data.limits)}
        />
      ) : (
        <Screen edges={['bottom']}>
          <QueryState query={query}>{() => null}</QueryState>
        </Screen>
      )}
    </>
  );
}
