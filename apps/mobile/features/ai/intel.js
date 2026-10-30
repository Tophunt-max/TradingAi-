/** Pure helpers for options intelligence, regime v2, strategy router v2 and model comparison. */

export const VERDICT_TONE = { COMPATIBLE: 'positive', REQUIRES_VALIDATION: 'warning', INCOMPATIBLE: 'neutral', NO_TRADE: 'negative' };
export const VERDICT_LABEL = { COMPATIBLE: 'COMPATIBLE', REQUIRES_VALIDATION: 'NEEDS VALIDATION', INCOMPATIBLE: 'INCOMPATIBLE', NO_TRADE: 'NO TRADE' };

const isNum = (v) => v != null && !Number.isNaN(Number(v));

/** 0.4135 -> "41.4%"; null -> "—". */
export function pct(p, d = 1) {
  if (!isNum(p)) return '—';
  // Round half-up in decimal: 0.4135 * 100 is 41.349999… in binary floating point.
  const f = 10 ** d;
  return `${(Math.round(Number(p) * 100 * f + 1e-9) / f).toFixed(d)}%`;
}

/** Rupees with sign: -47.5 -> "−₹47.50". */
export function rupees(v) {
  if (!isNum(v)) return '—';
  const n = Number(v);
  return `${n < 0 ? '−' : n > 0 ? '+' : ''}₹${Math.abs(n).toFixed(2)}`;
}

export const stateLabel = (s) => (s ? s.replace(/_/g, ' ') : '—');

/** Regime v2 confidences as sorted bars (highest first). */
export function regimeBars(regime) {
  if (!regime?.confidence) return [];
  return Object.entries(regime.confidence)
    .map(([state, value]) => ({ state, label: stateLabel(state), value, isTop: state === regime.state }))
    .sort((a, b) => b.value - a.value);
}

/** Router rows with display tone, label and score text. */
export function routerRows(router) {
  if (!router?.strategies) return [];
  return router.strategies.map((s) => ({
    ...s,
    tone: VERDICT_TONE[s.verdict] ?? 'neutral',
    verdictLabel: VERDICT_LABEL[s.verdict] ?? s.verdict,
    score: isNum(s.expected_compatibility) ? Number(s.expected_compatibility).toFixed(2) : '—',
  }));
}

/** The four separated outputs of one intelligence candidate, as display strings. */
export function intelSections(c) {
  if (!c) return null;
  const ud = c.underlying_direction ?? {};
  const pp = c.premium_path ?? {};
  const en = c.expected_net ?? {};
  const r = c.risks ?? {};
  return {
    direction: ud.probabilities
      ? { available: true, text: `P(${ud.relevant_outcome}) ${pct(ud.relevant_probability)}`, detail: ud.model_name ?? ud.model_version ?? '' }
      : { available: false, text: 'Not available', detail: ud.reason ?? 'No promoted model.' },
    premium: pp.available
      ? {
          available: true,
          target: pct(pp.p_target_first?.p),
          stop: pct(pp.p_stop_first?.p),
          neither: pct(pp.p_neither?.p),
          detail: `Target +${pp.target_pct}% / stop −${pp.stop_pct}% within ${pp.horizon_minutes} min · vol ${pct(pp.path_volatility)} (${(pp.path_volatility_source ?? '').replace(/_/g, ' ').toLowerCase()}) · ${pp.paths} paths`,
          scenarios: (pp.iv_scenarios ?? []).map((s) => `IV ${s.iv_shock_pts > 0 ? '+' : ''}${s.iv_shock_pts} pts: target ${pct(s.p_target_first)}, stop ${pct(s.p_stop_first)}`),
        }
      : { available: false, detail: pp.reason ?? 'Premium path not modelled.' },
    net: en.available
      ? {
          available: true,
          net: rupees(en.expected_net_pnl),
          gross: rupees(en.expected_premium_pnl),
          costs: rupees(-(en.costs?.total ?? 0)),
          detail: `Spread ₹${Number(en.costs?.spread_crossing ?? 0).toFixed(2)} · slippage ₹${Number(en.costs?.slippage ?? 0).toFixed(2)} · charges ₹${Number(en.costs?.charges ?? 0).toFixed(2)} · qty ${en.qty}`,
          negative: Number(en.expected_net_pnl) < 0,
        }
      : { available: false, detail: en.reason ?? 'Expected outcome not modelled.' },
    risks: {
      theta: rupees(r.theta_per_day_lot),
      spread: isNum(r.spread_pct) ? `${Number(r.spread_pct).toFixed(2)}% (₹${Number(r.spread_cost_lot ?? 0).toFixed(2)} to cross)` : '—',
      vega: r.iv_move_pnl_lot ? `${rupees(r.iv_move_pnl_lot['-2'])} / ${rupees(r.iv_move_pnl_lot['+2'])}` : '—',
      gamma: isNum(r.gamma) ? Number(r.gamma).toFixed(5) : '—',
      flags: (r.flags ?? []).map((f) => f.message),
    },
    eligible: !!c.eligible,
    blocked: c.blocked_reasons ?? [],
  };
}

/** "−0.0123 [−0.0200, −0.0040]" for a bootstrap comparison, or "—". */
export function ciText(vs) {
  if (!vs) return '—';
  const f = (v) => `${v < 0 ? '−' : '+'}${Math.abs(v).toFixed(4)}`;
  return `${f(vs.mean_diff)} [${f(vs.ci_low)}, ${f(vs.ci_high)}]`;
}

const yesNo = (b) => (b == null ? '—' : b ? 'YES' : 'NO');

/** Model comparison rows for display, best (lowest) log loss first. */
export function compareRows(payload) {
  return (payload?.rows ?? []).map((r) => ({
    id: r.id,
    title: `${r.model_type.replace('_', ' ')} · ${r.target.replace('_', ' ')}`,
    subtitle: `${r.name} · ${r.status} · ${r.data_source}`,
    metrics: `log loss ${r.log_loss ?? '—'} (clim. ${r.climatology_log_loss ?? '—'}) · Brier ${r.brier ?? '—'} · ECE ${r.ece ?? '—'} · n=${r.oos_samples ?? '—'}`,
    vsLogistic: r.vs_logistic ? `${yesNo(r.better_than_logistic)} ${ciText(r.vs_logistic)}` : 'baseline',
    vsClimatology: `${yesNo(r.better_than_climatology)} ${ciText(r.vs_climatology)}`,
    calibration: r.calibration_status,
    promotable: r.promotable,
    promotableReason: r.promotable_reason,
  }));
}
