import { useState } from 'react';
import { Pressable, View } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { AppText, Badge, Card, CardHeader, KeyValue, ListRow } from '../../components/ui';
import { useTheme } from '../../components/theme/ThemeProvider';
import { fixed, formatINR, formatPrice } from '../../services/format';

export const BIAS_TONE = { BULLISH: 'positive', BEARISH: 'negative', NEUTRAL: 'neutral', SIDEWAYS: 'warning' };
const STATUS_ICON = { DONE: ['checkmark-circle', 'positive'], SKIPPED: ['remove-circle-outline', 'textMuted'], FAILED: ['close-circle', 'negative'], BLOCKED: ['lock-closed', 'warning'] };
const DIR_TONE = { CE: 'positive', PE: 'negative', NONE: 'neutral', UNAVAILABLE: 'warning', SKIPPED: 'neutral' };

function Expandable({ title, subtitle, right, children, testID, initiallyOpen = false }) {
  const theme = useTheme();
  const [open, setOpen] = useState(initiallyOpen);
  return (
    <Card testID={testID}>
      <Pressable accessibilityRole="button" accessibilityState={{ expanded: open }} onPress={() => setOpen((o) => !o)} style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
        <View style={{ flex: 1 }}>
          <AppText variant="heading">{title}</AppText>
          {subtitle ? <AppText variant="caption" tone="muted">{subtitle}</AppText> : null}
        </View>
        {right}
        <Ionicons name={open ? 'chevron-up' : 'chevron-down'} size={18} color={theme.colors.textMuted} />
      </Pressable>
      {open ? <View style={{ marginTop: 10, gap: 4 }}>{children}</View> : null}
    </Card>
  );
}

export function RegimeCard({ regime, trend }) {
  if (!regime) return null;
  return (
    <Card testID="regime-card">
      <CardHeader title="Market regime" subtitle={regime.method} right={<Badge label={regime.label.replace('_', ' ')} tone={regime.family === 'TRENDING' ? 'info' : regime.label === 'VOLATILE' ? 'negative' : 'warning'} />} />
      {regime.reasons.map((r) => <AppText key={r} variant="caption" tone="muted">• {r}</AppText>)}
      <KeyValue label="Trend reading" value={trend} tone={BIAS_TONE[trend]} />
    </Card>
  );
}

export function MtfStrip({ data }) {
  const theme = useTheme();
  if (!data) return null;
  return (
    <Card testID="mtf-strip">
      <CardHeader title="Multi-timeframe" subtitle={data.note} right={<Badge label={data.alignment.replace('_', ' ')} tone={data.alignment === 'MIXED' ? 'warning' : 'info'} />} />
      <View style={{ flexDirection: 'row', gap: 8 }}>
        {data.views.map((v) => (
          <View key={v.timeframe} style={{ flex: 1, padding: 8, borderRadius: 10, backgroundColor: theme.colors.surfaceRaised, gap: 2 }}>
            <AppText variant="label" tone="muted">{v.timeframe.toUpperCase()}</AppText>
            {v.available ? (
              <>
                <AppText variant="caption" tone={BIAS_TONE[v.trend]} style={{ fontWeight: '700' }}>{v.trend}</AppText>
                <AppText variant="caption" tone="faint" numberOfLines={1}>{v.regime?.replace('_', ' ') ?? '—'}</AppText>
                <AppText variant="caption" tone="faint">RSI {v.rsi ?? '—'}</AppText>
              </>
            ) : (
              <AppText variant="caption" tone="faint">n/a</AppText>
            )}
          </View>
        ))}
      </View>
    </Card>
  );
}

export function PipelineCard({ steps, reasons, explanation }) {
  const theme = useTheme();
  return (
    <Expandable testID="analysis-decision" title="Decision" subtitle={explanation.slice(0, 2).join(' ')} right={<Badge label="NO TRADE" tone="warning" />}>
      <AppText variant="label" tone="muted">WHY NO TRADE</AppText>
      {reasons.map((r) => <AppText key={r} variant="caption" tone="muted">• {r}</AppText>)}
      <AppText variant="label" tone="muted" style={{ marginTop: 8 }}>PIPELINE</AppText>
      {steps.map((s, i) => {
        const [icon, tone] = STATUS_ICON[s.status];
        return (
          <View key={s.key} style={{ flexDirection: 'row', gap: 8, alignItems: 'flex-start', paddingVertical: 3 }}>
            <Ionicons name={icon} size={15} color={theme.colors[tone]} style={{ marginTop: 2 }} />
            <AppText variant="caption" style={{ flex: 1 }}>
              <AppText variant="caption" style={{ fontWeight: '700' }}>{i + 1}. {s.label}: </AppText>
              <AppText variant="caption" tone="muted">{s.detail}</AppText>
            </AppText>
          </View>
        );
      })}
      <AppText variant="label" tone="muted" style={{ marginTop: 8 }}>EXPLANATION</AppText>
      {explanation.map((e) => <AppText key={e} variant="caption" tone="muted">{e}</AppText>)}
    </Expandable>
  );
}

export function StrategiesCard({ strategies }) {
  const theme = useTheme();
  const active = strategies.filter((s) => s.direction === 'CE' || s.direction === 'PE').length;
  return (
    <Expandable testID="strategies-card" title="Strategy evaluation" subtitle={`${active} of ${strategies.length} signalling on the last closed candle`}>
      {strategies.map((s) => (
        <View key={s.id} style={{ paddingVertical: 8, borderBottomWidth: 1, borderColor: theme.colors.borderSubtle, gap: 4 }}>
          <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
            <AppText variant="bodyStrong" style={{ flex: 1 }}>{s.name}</AppText>
            {s.regime_compatible === false ? <Badge label="REGIME MISMATCH" tone="warning" /> : null}
            <Badge label={s.direction} tone={DIR_TONE[s.direction]} />
          </View>
          <AppText variant="caption" tone="muted">{s.summary}</AppText>
          {s.conditions.map((c) => (
            <View key={c.label} style={{ flexDirection: 'row', gap: 6 }}>
              <Ionicons name={c.met ? 'checkmark' : c.met === null ? 'help' : 'close'} size={14} color={c.met ? theme.colors.positive : theme.colors.textFaint} />
              <AppText variant="caption" tone={c.met ? 'default' : 'faint'}>{c.label}</AppText>
            </View>
          ))}
        </View>
      ))}
    </Expandable>
  );
}

function EventRow({ e }) {
  return (
    <ListRow
      title={e.name}
      subtitle={`${e.detail} · ${e.bars_ago === 0 ? 'last candle' : `${e.bars_ago} bars ago`}${e.mitigated ? ' · mitigated' : ''}`}
      right={<Badge label={e.direction} tone={BIAS_TONE[e.direction]} />}
    />
  );
}

export function PatternsCard({ patterns, structure }) {
  const recent = [...patterns].reverse();
  return (
    <Expandable testID="patterns-card" title="Patterns & structure" subtitle={structure ? structure.label : `${patterns.length} confirmed events`}>
      <AppText variant="caption" tone="faint">Only confirmed patterns are shown: each appears once all the candles it needs have closed.</AppText>
      {recent.length ? recent.map((e, i) => <EventRow key={`${e.name}-${e.confirmed_ts}-${i}`} e={e} />) : <AppText tone="muted">No patterns in this window.</AppText>}
    </Expandable>
  );
}

export function SmcCard({ smc }) {
  const groups = [
    ['Structure breaks', smc.structure_events],
    ['Open order blocks', smc.order_blocks],
    ['Open fair value gaps', smc.fair_value_gaps],
    ['Liquidity sweeps', smc.liquidity_sweeps],
    ['Equal highs / lows', smc.equal_levels],
    ['Displacement', smc.displacement],
  ];
  return (
    <Expandable testID="smc-card" title="Smart Money Concepts" subtitle={`Structure trend: ${smc.trend.toLowerCase()}`}>
      {groups.map(([title, items]) => (
        <View key={title}>
          <AppText variant="label" tone="muted" style={{ marginTop: 6 }}>{title.toUpperCase()}</AppText>
          {items.length ? [...items].reverse().map((e, i) => <EventRow key={`${title}-${i}`} e={e} />) : <AppText variant="caption" tone="faint">None</AppText>}
        </View>
      ))}
    </Expandable>
  );
}

export function LevelsCard({ data }) {
  const ind = data.indicators;
  return (
    <Expandable title="Indicators & levels" subtitle={`RSI ${fixed(ind.rsi14, 1)} · ADX ${fixed(ind.adx14, 1)} · Supertrend ${ind.supertrend_direction ?? '—'}`}>
      <KeyValue label="Last close" value={formatPrice(data.last_price)} />
      {data.resistance.map((r, i) => <KeyValue key={`r${r}`} label={`Resistance ${i + 1} (swing)`} value={formatPrice(r)} tone="negative" />)}
      {data.support.map((s, i) => <KeyValue key={`s${s}`} label={`Support ${i + 1} (swing)`} value={formatPrice(s)} tone="positive" />)}
      <KeyValue label="EMA 9 / 21 / 50 / 200" value={[ind.ema9, ind.ema21, ind.ema50, ind.ema200].map((v) => formatPrice(v)).join(' / ')} />
      <KeyValue label="VWAP" value={formatPrice(ind.vwap)} />
      <KeyValue label="Supertrend (10, 3)" value={`${formatPrice(ind.supertrend)} ${ind.supertrend_direction ?? ''}`} />
      <KeyValue label="MACD / signal" value={`${fixed(ind.macd)} / ${fixed(ind.macd_signal)}`} />
      <KeyValue label="Bollinger (20, 2)" value={`${formatPrice(ind.bb_lower)} – ${formatPrice(ind.bb_upper)}`} />
      <KeyValue label="ATR 14" value={fixed(ind.atr14)} />
      <KeyValue label="ADX / +DI / −DI" value={`${fixed(ind.adx14, 1)} / ${fixed(ind.pdi14, 1)} / ${fixed(ind.mdi14, 1)}`} />
      <KeyValue label="CCI 20" value={fixed(ind.cci20, 1)} />
      <KeyValue label="Stochastic %K / %D" value={`${fixed(ind.stoch_k, 1)} / ${fixed(ind.stoch_d, 1)}`} />
      <KeyValue label="Relative volume" value={ind.relative_volume == null ? '—' : `${fixed(ind.relative_volume)}×`} />
      <KeyValue label="Last candle" value={`${ind.candle.direction.toLowerCase()} · body ${(ind.candle.body_pct * 100).toFixed(0)}% of range`} />
      {ind.pivots ? (
        <>
          <AppText variant="label" tone="muted" style={{ marginTop: 6 }}>PIVOTS · {ind.pivots_basis}</AppText>
          {['R2', 'R1', 'P', 'S1', 'S2'].map((k) => <KeyValue key={k} label={k} value={formatPrice(ind.pivots[k])} />)}
        </>
      ) : null}
      {ind.fibonacci ? (
        <>
          <AppText variant="label" tone="muted" style={{ marginTop: 6 }}>FIBONACCI · {ind.fibonacci_basis}</AppText>
          {Object.entries(ind.fibonacci).map(([k, v]) => <KeyValue key={k} label={k} value={formatPrice(v)} />)}
        </>
      ) : null}
    </Expandable>
  );
}

export function SetupCard({ setup, options }) {
  if (!setup) return null;
  return (
    <Card testID="setup-card">
      <CardHeader title="Setup candidate (research)" subtitle={setup.basis} right={<Badge label={`${setup.side} VIEW`} tone={setup.side === 'CE' ? 'positive' : 'negative'} />} />
      <KeyValue label="Entry zone" value={`${formatPrice(setup.entry_zone[0])} – ${formatPrice(setup.entry_zone[1])}`} />
      <KeyValue label="Stop-loss" value={`${formatPrice(setup.stop_loss)} (${setup.stop_basis.toLowerCase()})`} tone="negative" />
      <KeyValue label="Targets (1R / 2R)" value={setup.targets.map((t) => formatPrice(t)).join(' / ')} tone="positive" />
      <KeyValue label="Target before stop" value="Not modelled" />
      <AppText variant="label" tone="muted" style={{ marginTop: 6 }}>INVALIDATION</AppText>
      {setup.invalidation.map((x) => <AppText key={x} variant="caption" tone="muted">• {x}</AppText>)}
      {options?.candidates?.length ? (
        <>
          <AppText variant="label" tone="muted" style={{ marginTop: 10 }}>CONTRACT SHORTLIST · {options.underlying} {String(options.expiry)}</AppText>
          {options.candidates.map((c) => (
            <View key={c.label} style={{ paddingVertical: 6 }}>
              <View style={{ flexDirection: 'row', justifyContent: 'space-between' }}>
                <AppText variant="bodyStrong">{c.label} · {c.strike} {setup.side}</AppText>
                <Badge label={c.eligible ? 'LIQUID' : 'CHECK FAILED'} tone={c.eligible ? 'positive' : 'warning'} />
              </View>
              <AppText variant="caption" tone="muted">
                Ask {fixed(c.ask)} · spread {c.spread_pct == null ? '—' : `${c.spread_pct}%`} · IV {fixed(c.iv, 1)} · Δ {fixed(c.delta)} · 1 lot {c.premium_per_lot ? formatINR(c.premium_per_lot) : '—'} · θ/day/lot {c.theta_per_lot_per_day ?? '—'}
              </AppText>
              {c.checks.filter((k) => !k.passed).map((k) => <AppText key={k.label} variant="caption" tone="warning">✗ {k.label}</AppText>)}
            </View>
          ))}
          <AppText variant="caption" tone="faint">{options.note}</AppText>
        </>
      ) : null}
      <AppText variant="caption" tone="warning" style={{ marginTop: 6 }}>This is not a trade proposal. Any paper trade must still pass the charges check and the global risk manager.</AppText>
    </Card>
  );
}
