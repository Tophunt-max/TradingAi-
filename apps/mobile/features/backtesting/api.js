import { useMutation, useQuery } from '@tanstack/react-query';
import { api } from '../../store/auth';

// Backtests are computed on request (the backend memoises identical requests in-process).
export function useBacktestStrategies() {
  return useQuery({ queryKey: ['backtest-strategies'], queryFn: () => api.get('/api/v1/backtests/strategies'), staleTime: 5 * 60_000 });
}

export function useRunBacktest() {
  return useMutation({ mutationFn: (body) => api.post('/api/v1/backtests/run', body) });
}

export function useMonteCarlo() {
  return useMutation({ mutationFn: (body) => api.post('/api/v1/backtests/monte-carlo', body) });
}

export function useWalkForward() {
  return useMutation({ mutationFn: (body) => api.post('/api/v1/backtests/walk-forward', body) });
}

export function useSensitivity() {
  return useMutation({ mutationFn: (body) => api.post('/api/v1/backtests/sensitivity', body) });
}
