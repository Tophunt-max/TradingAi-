import { useState } from 'react';
import { View } from 'react-native';
import { router } from 'expo-router';
import { AppText, Button, Card, CardHeader, ChipRow, NoticeBanner, Screen, SegmentedControl, Stepper, TextField } from '../../../components/ui';
import { describeError } from '../../../features/auth/api';
import { ConfigSummary } from '../../../features/bots/ConfigSummary';
import { useAiDraft, useRisk, useSaveBot, useStrategies } from '../../../features/trading/api';

const STEPS = ['Market', 'Risk', 'Review', 'Save'];
const STYLES = [
  { value: 'AUTO', label: 'Auto' },
  { value: 'MOMENTUM', label: 'Momentum' },
  { value: 'BREAKOUT', label: 'Breakout' },
  { value: 'SCALPING', label: 'Scalping' },
  { value: 'REVERSAL', label: 'Reversal' },
  { value: 'SMC', label: 'SMC' },
];

export default function AiCreate() {
  const risk = useRisk();
  const draft = useAiDraft();
  const save = useSaveBot();
  const strategies = useStrategies();
  const [step, setStep] = useState(0);
  const [form, setForm] = useState({ underlying: 'NIFTY', style: 'AUTO', timeframe: '5m', option_type: 'CE', capital: '', risk_profile: 'CONSERVATIVE' });
  const set = (k) => (v) => setForm((f) => ({ ...f, [k]: v }));
  const capital = form.capital || String(Number(risk.data?.limits.capital ?? 10000));

  const generate = () =>
    draft.mutate({ ...form, capital }, { onSuccess: () => setStep(2) });

  return (
    <Screen keyboard edges={['bottom']}>
      <Stepper steps={STEPS} current={step} />
      {step === 0 ? (
        <Card style={{ gap: 12 }}>
          <CardHeader title="Select market" />
          <SegmentedControl value={form.underlying} onChange={set('underlying')} options={[{ value: 'NIFTY', label: 'NIFTY' }, { value: 'BANKNIFTY', label: 'BANKNIFTY' }]} testID="ai-underlying" />
          <AppText variant="label" tone="muted">TRADING STYLE</AppText>
          <ChipRow value={form.style} onChange={set('style')} options={STYLES} testIDPrefix="ai-style" />
          <AppText variant="label" tone="muted">TIMEFRAME</AppText>
          <ChipRow value={form.timeframe} onChange={set('timeframe')} options={['1m', '3m', '5m', '15m'].map((t) => ({ value: t, label: t }))} />
          <SegmentedControl value={form.option_type} onChange={set('option_type')} options={[{ value: 'CE', label: 'CE (Call)' }, { value: 'PE', label: 'PE (Put)' }]} />
          <NoticeBanner tone="info">
            This assistant builds a draft from rules and your risk limits. It does not analyse the market yet; that arrives with the AI engine in Phase 4.
          </NoticeBanner>
          <Button title="Next" icon="arrow-forward" fullWidth onPress={() => setStep(1)} />
        </Card>
      ) : null}
      {step === 1 ? (
        <Card style={{ gap: 12 }}>
          <CardHeader title="Capital & risk profile" />
          <TextField label="Capital allocation (₹)" value={capital} onChangeText={set('capital')} keyboardType="decimal-pad" hint={risk.data ? `Global capital ₹${Number(risk.data.limits.capital).toLocaleString('en-IN')}` : undefined} />
          <SegmentedControl
            value={form.risk_profile}
            onChange={set('risk_profile')}
            options={[{ value: 'CONSERVATIVE', label: 'Conservative' }, { value: 'MODERATE', label: 'Moderate' }, { value: 'AGGRESSIVE', label: 'Aggressive' }]}
          />
          {draft.error ? <AppText tone="negative">{describeError(draft.error)}</AppText> : null}
          <View style={{ flexDirection: 'row', gap: 10 }}>
            <View style={{ flex: 1 }}><Button title="Back" variant="secondary" fullWidth onPress={() => setStep(0)} /></View>
            <View style={{ flex: 1 }}><Button title="Generate draft" icon="sparkles-outline" fullWidth loading={draft.isPending} disabled={!(Number(capital) > 0)} onPress={generate} testID="ai-generate" /></View>
          </View>
        </Card>
      ) : null}
      {step >= 2 && draft.data ? (
        <>
          <Card>
            <CardHeader title="Why this configuration" subtitle={`Generator: ${draft.data.generator}`} />
            {draft.data.rationale.map((r) => <AppText key={r} tone="muted">• {r}</AppText>)}
          </Card>
          {draft.data.warnings.map((w) => <NoticeBanner key={w}>{w}</NoticeBanner>)}
          <ConfigSummary config={draft.data.config} strategyName={strategies.data?.find((x) => x.id === draft.data.config.strategy_id)?.name} />
          {save.error ? <AppText tone="negative">{describeError(save.error)}</AppText> : null}
          <View style={{ flexDirection: 'row', gap: 10 }}>
            <View style={{ flex: 1 }}>
              <Button title="Edit in builder" variant="secondary" fullWidth onPress={() => save.mutate({ creation_method: 'AI', config: draft.data.config }, { onSuccess: (b) => router.replace({ pathname: '/bots/builder', params: { id: b.id } }) })} />
            </View>
            <View style={{ flex: 1 }}>
              <Button
                title="Save draft & validate"
                fullWidth
                loading={save.isPending}
                testID="ai-save"
                onPress={() => save.mutate({ creation_method: 'AI', config: draft.data.config }, { onSuccess: (b) => router.replace({ pathname: `/bots/${b.id}`, params: { validate: '1' } }) })}
              />
            </View>
          </View>
        </>
      ) : null}
    </Screen>
  );
}
