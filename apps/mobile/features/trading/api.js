import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../../store/auth';

// ---- Bots -----------------------------------------------------------------
export function useBots() {
  return useQuery({ queryKey: ['bots'], queryFn: () => api.get('/api/v1/bots'), refetchInterval: 15_000 });
}

export function useBot(id) {
  return useQuery({
    queryKey: ['bot', id],
    queryFn: () => api.get(`/api/v1/bots/${id}`),
    enabled: !!id,
    // Running bots change as the worker evaluates candles.
    refetchInterval: (q) => (q.state.data?.status === 'ACTIVE' ? 5_000 : false),
  });
}

export function useBotFeed(id, kind) {
  return useQuery({
    queryKey: ['bot', id, kind],
    queryFn: () => api.get(`/api/v1/bots/${id}/${kind}`),
    enabled: !!id,
    refetchInterval: 10_000,
  });
}

export function useStrategies() {
  return useQuery({ queryKey: ['strategies'], queryFn: () => api.get('/api/v1/bots/strategies'), staleTime: 300_000 });
}

export function useAiDraft() {
  return useMutation({ mutationFn: (body) => api.post('/api/v1/bots/ai-draft', body) });
}

export function useSaveBot() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, creation_method, config }) =>
      id ? api.request(`/api/v1/bots/${id}`, { method: 'PUT', body: { config } }) : api.post('/api/v1/bots', { creation_method, config }),
    onSuccess: (bot) => {
      qc.invalidateQueries({ queryKey: ['bots'] });
      qc.setQueryData(['bot', bot.id], bot);
    },
  });
}

export function useBotAction() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, action }) => {
      if (action === 'delete') return api.del(`/api/v1/bots/${id}?confirm=true`);
      return api.post(`/api/v1/bots/${id}/${action}`);
    },
    onSettled: (_d, _e, { id }) => {
      qc.invalidateQueries({ queryKey: ['bots'] });
      qc.invalidateQueries({ queryKey: ['bot', id] });
      qc.invalidateQueries({ queryKey: ['alerts'] });
      qc.invalidateQueries({ queryKey: ['risk'] });
    },
  });
}

export function useValidateBot() {
  return useMutation({ mutationFn: (id) => api.post(`/api/v1/bots/${id}/validate`) });
}

// ---- Risk -----------------------------------------------------------------
export function useRisk() {
  return useQuery({ queryKey: ['risk'], queryFn: () => api.get('/api/v1/risk'), refetchInterval: 10_000 });
}

export function useUpdateRiskLimits() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (patch) => api.patch('/api/v1/risk/limits', patch),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['risk'] });
      qc.invalidateQueries({ queryKey: ['portfolio'] });
    },
  });
}

export function useKillSwitch() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (engaged) => api.post('/api/v1/risk/kill-switch', { engaged, confirm: true }),
    onSuccess: (status) => qc.setQueryData(['risk'], status),
    onSettled: () => invalidateTrading(qc),
  });
}

// ---- Portfolio & paper execution -------------------------------------------
// Paper positions are marked to market by the execution worker every few seconds.
const LIVE_REFRESH = 5_000;

function qs(params) {
  const p = Object.entries(params).filter(([, v]) => v != null && v !== '');
  return p.length ? `?${p.map(([k, v]) => `${k}=${encodeURIComponent(v)}`).join('&')}` : '';
}

export function usePortfolio() {
  return useQuery({ queryKey: ['portfolio'], queryFn: () => api.get('/api/v1/portfolio'), refetchInterval: LIVE_REFRESH });
}

export function useOrders(status, botId) {
  return useQuery({
    queryKey: ['orders', status ?? 'ALL', botId ?? null],
    queryFn: () => api.get(`/api/v1/orders${qs({ status, bot_id: botId })}`),
    refetchInterval: status === 'PENDING' ? LIVE_REFRESH : 15_000,
  });
}

export function useOrder(id) {
  return useQuery({
    queryKey: ['order', id],
    queryFn: () => api.get(`/api/v1/orders/${id}`),
    enabled: !!id,
    refetchInterval: (q) => (q.state.data && ['FILLED', 'REJECTED', 'CANCELLED'].includes(q.state.data.status) ? false : LIVE_REFRESH),
  });
}

export function usePositions(state = 'OPEN', botId) {
  return useQuery({
    queryKey: ['positions', state, botId ?? null],
    queryFn: () => api.get(`/api/v1/positions${qs({ state, bot_id: botId })}`),
    refetchInterval: state === 'OPEN' ? LIVE_REFRESH : 30_000,
  });
}

function invalidateTrading(qc) {
  ['portfolio', 'orders', 'order', 'positions', 'risk', 'alerts', 'bots', 'bot'].forEach((k) => qc.invalidateQueries({ queryKey: [k] }));
}

/** Manual paper BUY. The idempotency key makes a retried tap return the same order. */
export function usePaperEntry() {
  const qc = useQueryClient();
  return useMutation({ mutationFn: (body) => api.post('/api/v1/orders/paper', body), onSettled: () => invalidateTrading(qc) });
}

export function useCancelOrder() {
  const qc = useQueryClient();
  return useMutation({ mutationFn: (id) => api.post(`/api/v1/orders/${id}/cancel`), onSettled: () => invalidateTrading(qc) });
}

export function useExitPosition() {
  const qc = useQueryClient();
  return useMutation({ mutationFn: (id) => api.post(`/api/v1/positions/${id}/exit`), onSettled: () => invalidateTrading(qc) });
}

export function useChargesEstimate() {
  return useMutation({ mutationFn: (body) => api.post('/api/v1/charges/estimate', body) });
}

export function newIdempotencyKey() {
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

// ---- Alerts & brokers -----------------------------------------------------
export function useAlerts(category) {
  return useQuery({ queryKey: ['alerts', category], queryFn: () => api.get(`/api/v1/alerts${category ? `?category=${category}` : ''}`) });
}

export function useBrokers() {
  return useQuery({ queryKey: ['brokers'], queryFn: () => api.get('/api/v1/brokers') });
}
