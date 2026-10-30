import { useQuery } from '@tanstack/react-query';
import { api } from '../../store/auth';

const qs = (params) =>
  Object.entries(params)
    .filter(([, v]) => v !== undefined && v !== null && v !== '')
    .map(([k, v]) => `${k}=${encodeURIComponent(v)}`)
    .join('&');

/** Options intelligence for one strike and side (research output, never an order). */
export function useOptionsIntelligence({ underlying, expiry, side, strike, stopPct, targetPct, horizon, enabled = true }) {
  return useQuery({
    queryKey: ['options-intel', underlying, expiry, side, strike, stopPct, targetPct, horizon],
    queryFn: () =>
      api.get(`/api/v1/options/${underlying}/intelligence?${qs({ expiry, side, strike, stop_pct: stopPct, target_pct: targetPct, horizon_minutes: horizon })}`),
    enabled: enabled && !!underlying && strike != null,
    staleTime: 30_000,
  });
}

export function useModelCompare(symbol, timeframe) {
  return useQuery({
    queryKey: ['model-compare', symbol, timeframe],
    queryFn: () => api.get(`/api/v1/models/compare?${qs({ symbol, timeframe })}`),
  });
}
