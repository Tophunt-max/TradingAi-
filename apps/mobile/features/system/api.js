import { useQuery } from '@tanstack/react-query';
import { api } from '../../store/auth';

export function useSystemStatus() {
  return useQuery({
    queryKey: ['system-status'],
    queryFn: () => api.get('/api/v1/system/status', { auth: false }),
    staleTime: 60_000,
  });
}

export function useHealth() {
  return useQuery({
    queryKey: ['health'],
    // /ready answers 503 when the database is down; that surfaces as a query error.
    queryFn: () => api.get('/api/v1/health/ready', { auth: false }),
    refetchInterval: 30_000,
  });
}

// Market data older than this is flagged STALE in the UI.
export const STALE_AFTER_MS = 60_000;

export function useIndices() {
  return useQuery({
    queryKey: ['market', 'indices'],
    queryFn: () => api.get('/api/v1/market/indices'),
    refetchInterval: 15_000,
  });
}

export function isStale(asOf, now = Date.now()) {
  if (!asOf) return true;
  return now - new Date(asOf).getTime() > STALE_AFTER_MS;
}
