import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../../store/auth';

// ---- Bots -----------------------------------------------------------------
export function useBots() {
  return useQuery({ queryKey: ['bots'], queryFn: () => api.get('/api/v1/bots') });
}

export function useBot(id) {
  return useQuery({ queryKey: ['bot', id], queryFn: () => api.get(`/api/v1/bots/${id}`), enabled: !!id });
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
    },
  });
}

export function useValidateBot() {
  return useMutation({ mutationFn: (id) => api.post(`/api/v1/bots/${id}/validate`) });
}

// ---- Risk -----------------------------------------------------------------
export function useRisk() {
  return useQuery({ queryKey: ['risk'], queryFn: () => api.get('/api/v1/risk') });
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
    onSuccess: (status) => {
      qc.setQueryData(['risk'], status);
      qc.invalidateQueries({ queryKey: ['alerts'] });
    },
  });
}

// ---- Portfolio ------------------------------------------------------------
export function usePortfolio() {
  return useQuery({ queryKey: ['portfolio'], queryFn: () => api.get('/api/v1/portfolio') });
}

export function useOrders(status) {
  return useQuery({ queryKey: ['orders', status], queryFn: () => api.get(`/api/v1/orders${status ? `?status=${status}` : ''}`) });
}

export function usePositions(state = 'OPEN') {
  return useQuery({ queryKey: ['positions', state], queryFn: () => api.get(`/api/v1/positions?state=${state}`) });
}

// ---- Alerts & brokers -----------------------------------------------------
export function useAlerts(category) {
  return useQuery({ queryKey: ['alerts', category], queryFn: () => api.get(`/api/v1/alerts${category ? `?category=${category}` : ''}`) });
}

export function useBrokers() {
  return useQuery({ queryKey: ['brokers'], queryFn: () => api.get('/api/v1/brokers') });
}
