import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../../store/auth';

// Everything that is scoped to the active account on the backend.
const ACCOUNT_SCOPED = ['accounts', 'demo-ledger', 'portfolio', 'orders', 'order', 'positions', 'risk', 'bots', 'bot', 'alerts', 'intelligence', 'decision'];

export function invalidateAccountScoped(qc) {
  ACCOUNT_SCOPED.forEach((k) => qc.invalidateQueries({ queryKey: [k] }));
}

/** GET /api/v1/accounts: both accounts with balances, the active mode and availability. */
export function useAccounts() {
  return useQuery({ queryKey: ['accounts'], queryFn: () => api.get('/api/v1/accounts'), refetchInterval: 30_000 });
}

/** POST /api/v1/accounts/active. The server refuses REAL (409) while it is unavailable. */
export function useSetActiveMode() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (mode) => api.post('/api/v1/accounts/active', { mode }),
    onSuccess: (data) => qc.setQueryData(['accounts'], data),
    onSettled: () => invalidateAccountScoped(qc),
  });
}

/** POST /api/v1/accounts/demo/reset. Only call after the user confirmed the dialog. */
export function useDemoReset() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api.post('/api/v1/accounts/demo/reset', { confirm: true }),
    onSettled: () => invalidateAccountScoped(qc),
  });
}

export function useDemoLedger(limit = 50) {
  return useQuery({ queryKey: ['demo-ledger', limit], queryFn: () => api.get(`/api/v1/accounts/demo/ledger?limit=${limit}`) });
}

function optionFor(acc) {
  if (acc.mode === 'DEMO') {
    return { mode: 'DEMO', available: acc.available, reason: 'Virtual money. Starting balance ₹10,000.' };
  }
  const why = acc.unavailable_reasons?.[0];
  return { mode: acc.mode, available: acc.available, reason: acc.available ? null : `Not available: ${why ?? 'no verified broker integration is connected.'}` };
}

/**
 * Active account mode and the modes the user can pick, from the server.
 * Shape: { mode, options: [{ mode, available, reason }], setMode, accounts, query }.
 * Until the server answers, DEMO is shown (it is the default and REAL is never assumed).
 */
export function useAccountMode() {
  const query = useAccounts();
  const setActive = useSetActiveMode();
  const accounts = query.data?.accounts ?? [];
  const options = accounts.length
    ? accounts.map(optionFor)
    : [
        { mode: 'DEMO', available: true, reason: 'Virtual money. Starting balance ₹10,000.' },
        { mode: 'REAL', available: false, reason: 'Not available: checking account status…' },
      ];
  return {
    mode: query.data?.active_mode ?? 'DEMO',
    options,
    accounts,
    query,
    error: setActive.error,
    setMode: (mode) => {
      const opt = options.find((o) => o.mode === mode);
      if (opt?.available) setActive.mutate(mode);
    },
  };
}

export const DEMO_RESET_MESSAGE =
  'This closes every open demo position at its last known price, cancels every pending demo order and restores the virtual balance to ₹10,000. Demo history stays in your reports. Your real account is never affected.';
