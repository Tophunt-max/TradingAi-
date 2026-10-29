// Backend sends UTC; display uses Indian Standard Time.
export function formatIST(iso, { withDate = false } = {}) {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '—';
  return d.toLocaleString('en-IN', {
    timeZone: 'Asia/Kolkata',
    hour: '2-digit',
    minute: '2-digit',
    second: withDate ? undefined : '2-digit',
    ...(withDate ? { day: '2-digit', month: 'short', year: 'numeric' } : {}),
    hour12: false,
  });
}

/** Price with Indian grouping; '—' when the provider sent nothing. */
export function formatPrice(value, digits = 2) {
  if (value === null || value === undefined || value === '') return '—';
  const n = Number(value);
  if (!Number.isFinite(n)) return '—';
  return n.toLocaleString('en-IN', { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

/** Fixed decimals for nullable numbers. */
export function fixed(value, digits = 2) {
  if (value === null || value === undefined) return '—';
  const n = Number(value);
  return Number.isFinite(n) ? n.toFixed(digits) : '—';
}

export function formatINR(value) {
  // null/undefined mean "unknown" (e.g. a REAL balance without a broker): never show ₹0.00.
  if (value === null || value === undefined || value === '') return '—';
  const n = Number(value);
  if (!Number.isFinite(n)) return '—';
  return `₹${n.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}
