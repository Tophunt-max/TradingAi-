/** Map annotation timestamps onto visible candle indexes. */
export function mapAnnotations(candles, ann) {
  if (!ann) return { markers: [], zones: [] };
  const index = new Map(candles.map((c, i) => [c.ts, i]));
  const markers = ann.patterns
    .filter((e) => e.direction !== 'NEUTRAL' && index.has(e.confirmed_ts))
    .map((e) => ({ index: index.get(e.confirmed_ts), direction: e.direction, label: e.name }));
  const zones = ann.zones
    .filter((z) => index.has(z.start_ts) && index.has(z.end_ts))
    .map((z) => ({ start: index.get(z.start_ts), end: index.get(z.end_ts), top: z.top, bottom: z.bottom, direction: z.direction, label: z.name }));
  return { markers, zones };
}
