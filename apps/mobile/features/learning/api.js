import { useQuery } from '@tanstack/react-query';
import { api } from '../../store/auth';

const qs = (params) =>
  Object.entries(params)
    .filter(([, v]) => v !== undefined && v !== null && v !== '')
    .map(([k, v]) => `${k}=${encodeURIComponent(v)}`)
    .join('&');

export function useLearningSummary(symbol) {
  return useQuery({ queryKey: ['learning-summary', symbol], queryFn: () => api.get(`/api/v1/learning/summary?${qs({ symbol })}`) });
}

export function useDecisions({ symbol, decision, limit = 30 } = {}) {
  return useQuery({
    queryKey: ['decisions', symbol, decision, limit],
    queryFn: () => api.get(`/api/v1/learning/decisions?${qs({ symbol, decision, limit })}`),
  });
}

export function useDrift() {
  return useQuery({ queryKey: ['drift'], queryFn: () => api.get('/api/v1/learning/drift') });
}

export function useFundamentals(symbol) {
  return useQuery({ queryKey: ['fundamentals', symbol], queryFn: () => api.get(`/api/v1/fundamentals/${symbol}`), staleTime: 300_000 });
}

export function useEvents(symbol) {
  return useQuery({ queryKey: ['events', symbol], queryFn: () => api.get(`/api/v1/events?${qs({ symbol })}`), refetchInterval: 300_000 });
}

export function useEventRisk(symbol) {
  return useQuery({ queryKey: ['event-risk', symbol], queryFn: () => api.get(`/api/v1/events/risk?${qs({ symbol })}`), refetchInterval: 60_000 });
}
