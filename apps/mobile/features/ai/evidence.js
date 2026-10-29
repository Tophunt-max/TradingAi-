/** Pure helpers for pattern context and evidence families (ta-3 payloads). */

const STATUS = {
  CONFIRMED: { label: 'CONFIRMED', tone: 'positive' },
  PENDING: { label: 'UNCONFIRMED', tone: 'warning' },
  INVALIDATED: { label: 'INVALIDATED', tone: 'negative' },
  EXPIRED: { label: 'NOT CONFIRMED', tone: 'neutral' },
};

const FAMILY_LABEL = { trend: 'Trend', momentum: 'Momentum', volume: 'Volume', volatility: 'Volatility' };
const TONE = { BULLISH: 'positive', BEARISH: 'negative', NEUTRAL: 'neutral' };

const num = (v, d = 2) => (v == null || Number.isNaN(Number(v)) ? null : Number(v).toFixed(d));

/** Badge for a pattern's confirmation state, or null for events without one. */
export function patternStatus(e) {
  const s = e?.confirmation?.status;
  return s ? STATUS[s] ?? { label: s, tone: 'neutral' } : null;
}

/** One short line: invalidation, relative volume, prior trend. Empty string if no context. */
export function patternContextLine(e) {
  if (!e?.confirmation && !e?.context) return '';
  const parts = [];
  const inv = e.invalidation?.level;
  const dir = e.confirmation?.direction ?? e.direction;
  if (inv != null) parts.push(`invalid ${dir === 'BEARISH' ? 'above' : 'below'} ${num(inv)}`);
  const rv = e.context?.relative_volume;
  if (rv != null) parts.push(`rel. vol ${num(rv)}×`);
  const trend = e.context?.prior_trend;
  if (trend) parts.push(`prior trend ${trend.toLowerCase()}`);
  return parts.join(' · ');
}

/** Directional families with their single vote and how many members agree. */
export function familyRows(technical) {
  if (!technical?.families) return [];
  return technical.families.map((f) => {
    const voted = f.members.filter((m) => m.vote != null);
    const agree = f.directional ? voted.filter((m) => m.vote === f.vote && m.vote !== 0).length : null;
    return {
      key: f.family,
      label: FAMILY_LABEL[f.family] ?? f.family,
      bias: f.directional ? f.bias : 'CONTEXT',
      tone: f.directional ? TONE[f.bias] : 'info',
      detail: f.directional
        ? `${agree} of ${voted.length} indicators agree · counts as 1 vote`
        : `${technical.volatility_state ?? 'n/a'} · context only, no vote`,
      members: f.members,
    };
  });
}

/** Human summary of highly correlated indicator pairs (top `limit`). */
export function correlationNote(technical, limit = 3) {
  const pairs = technical?.correlated_pairs ?? [];
  if (!pairs.length) return 'No indicator pairs above the correlation threshold.';
  const top = pairs.slice(0, limit).map((p) => `${p.a}–${p.b} (${num(p.rho)})`).join(', ');
  const more = pairs.length > limit ? ` and ${pairs.length - limit} more` : '';
  return `${pairs.length} pairs move together (|ρ| > ${technical.correlation_threshold}): ${top}${more}. They are not independent confirmations.`;
}
