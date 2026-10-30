import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../../store/auth';

// Broker connections. The access token is sent once to the server (connect) and is never
// stored in the app or returned by the server.

function invalidate(qc) {
  ['brokers', 'broker-permissions', 'broker-health', 'accounts', 'alerts'].forEach((k) => qc.invalidateQueries({ queryKey: [k] }));
}

export function useBrokerList() {
  return useQuery({ queryKey: ['brokers'], queryFn: () => api.get('/api/v1/brokers') });
}

export function useBrokerPermissions() {
  return useQuery({ queryKey: ['broker-permissions'], queryFn: () => api.get('/api/v1/brokers/permissions') });
}

export function useBrokerHealth(id, enabled) {
  return useQuery({ queryKey: ['broker-health', id], queryFn: () => api.get(`/api/v1/brokers/${id}/health`), enabled: !!enabled });
}

export function useConnectBroker() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, token }) => api.post(`/api/v1/brokers/${id}/connect`, { token }),
    onSettled: () => invalidate(qc),
  });
}

export function useDisconnectBroker() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id) => api.post(`/api/v1/brokers/${id}/disconnect`, { confirm: true }),
    onSettled: () => invalidate(qc),
  });
}

export function useSyncBroker() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id) => api.post(`/api/v1/brokers/${id}/sync`),
    onSettled: () => invalidate(qc),
  });
}

export const STATUS_LABEL = {
  CONNECTED: { label: 'CONNECTED', tone: 'positive' },
  NOT_CONNECTED: { label: 'NOT CONNECTED', tone: 'neutral' },
  NOT_ENABLED: { label: 'DISABLED ON SERVER', tone: 'neutral' },
  NOT_INTEGRATED: { label: 'NOT INTEGRATED', tone: 'neutral' },
  EXPIRED: { label: 'TOKEN EXPIRED', tone: 'warning' },
  REVOKED: { label: 'DISCONNECTED', tone: 'neutral' },
  ERROR: { label: 'ERROR', tone: 'negative' },
};

export const TOKEN_WARNING =
  'Paste the access token you generated on the broker’s website. It is sent once to the QuantAI server, validated with a read-only call and stored there encrypted. It never stays on this device and is never shown again. Connecting is read-only: live trading stays locked.';
