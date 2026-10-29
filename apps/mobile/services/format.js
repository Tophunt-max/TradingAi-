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

export function formatINR(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return '—';
  return `₹${n.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}
