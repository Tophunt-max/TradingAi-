// Pure helpers for the Reports screen (unit-tested in __tests__/reports.test.jsx).

/** YYYY-MM-DD of "today" in IST. */
export function istToday(now = new Date()) {
  const ist = new Date(now.getTime() + 330 * 60 * 1000);
  return ist.toISOString().slice(0, 10);
}

/** Shift a YYYY-MM-DD date by n days (calendar arithmetic in UTC, no DST issues). */
export function shiftDay(day, n) {
  const d = new Date(`${day}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + n);
  return d.toISOString().slice(0, 10);
}

/** "Wed 28 Oct 2026" for a YYYY-MM-DD string. */
export function dayLabel(day) {
  const d = new Date(`${day}T00:00:00Z`);
  if (Number.isNaN(d.getTime())) return day;
  return d.toLocaleDateString('en-IN', { weekday: 'short', day: '2-digit', month: 'short', year: 'numeric', timeZone: 'UTC' });
}

/** Signed rupees: "+₹110.00", "−₹192.00", "₹0.00"; "—" for null. */
export function signedINR(value) {
  if (value === null || value === undefined || value === '') return '—';
  const n = Number(value);
  if (!Number.isFinite(n)) return '—';
  const abs = Math.abs(n).toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  if (n > 0) return `+₹${abs}`;
  if (n < 0) return `−₹${abs}`;
  return `₹${abs}`;
}

/** Bars for a daily-net list: width as a share of the largest absolute value. */
export function netBars(daily = []) {
  const max = Math.max(0, ...daily.map((d) => Math.abs(Number(d.net) || 0)));
  return daily.map((d) => {
    const n = Number(d.net) || 0;
    return { day: d.day, net: d.net, positive: n >= 0, pct: max ? Math.round((Math.abs(n) / max) * 100) : 0 };
  });
}

/** "66.67%" or "—" when there were no trades. */
export function pct(value) {
  return value === null || value === undefined ? '—' : `${value}%`;
}

export const CSV_EXPORTS = [
  { id: 'trades', label: 'Trades' },
  { id: 'orders', label: 'Orders' },
  { id: 'fills', label: 'Fills' },
  { id: 'ledger', label: 'Ledger', demoOnly: true },
  { id: 'daily-report', label: 'Daily summary' },
];

/** Export path for the API, scoped to an account mode. */
export function exportPath(id, mode) {
  return `/api/v1/reports/${id}.csv?account_mode=${encodeURIComponent(mode)}`;
}
