import { useQuery } from '@tanstack/react-query';
import { api } from '../../store/auth';

const qs = (params) =>
  Object.entries(params)
    .filter(([, v]) => v !== undefined && v !== null && v !== '')
    .map(([k, v]) => `${k}=${encodeURIComponent(v)}`)
    .join('&');

export function useQuotes(segment = 'ALL', symbols) {
  return useQuery({
    queryKey: ['quotes', segment, symbols],
    queryFn: () => api.get(`/api/v1/market/quotes?${qs({ segment, symbols })}`),
    refetchInterval: 15_000,
  });
}

export function useMovers(limit = 5) {
  return useQuery({ queryKey: ['movers', limit], queryFn: () => api.get(`/api/v1/market/movers?limit=${limit}`), refetchInterval: 30_000 });
}

export function useSearch(q) {
  return useQuery({
    queryKey: ['search', q],
    queryFn: () => api.get(`/api/v1/market/search?${qs({ q })}`),
    enabled: q.trim().length > 0,
    staleTime: 60_000,
  });
}

export function useCandles(symbol, timeframe, indicators, limit = 90, annotations = false) {
  return useQuery({
    queryKey: ['candles', symbol, timeframe, indicators, limit, annotations],
    queryFn: () => api.get(`/api/v1/charts/${symbol}/candles?${qs({ timeframe, indicators, limit, annotations: annotations ? 'true' : undefined })}`),
    refetchInterval: 30_000,
    placeholderData: (prev) => (prev?.symbol === symbol ? prev : undefined),
  });
}

export function useExpiries(underlying) {
  return useQuery({ queryKey: ['expiries', underlying], queryFn: () => api.get(`/api/v1/options/${underlying}/expiries`) });
}

export function useOptionChain(underlying, expiry, strikes = 10) {
  return useQuery({
    queryKey: ['chain', underlying, expiry, strikes],
    queryFn: () => api.get(`/api/v1/options/${underlying}/chain?${qs({ expiry, strikes })}`),
    refetchInterval: 15_000,
  });
}

export function useAnalysis(symbol, timeframe) {
  return useQuery({
    queryKey: ['analysis', symbol, timeframe],
    queryFn: () => api.get(`/api/v1/analysis/${symbol}?${qs({ timeframe })}`),
    refetchInterval: 60_000,
  });
}

export function useMtf(symbol) {
  return useQuery({ queryKey: ['mtf', symbol], queryFn: () => api.get(`/api/v1/analysis/${symbol}/mtf`), refetchInterval: 120_000 });
}

export function useModels() {
  return useQuery({ queryKey: ['models'], queryFn: () => api.get('/api/v1/models') });
}

export function usePredictionHistory(symbol) {
  return useQuery({ queryKey: ['predictions', symbol], queryFn: () => api.get(`/api/v1/models/predictions?${qs({ symbol, limit: 50 })}`) });
}
