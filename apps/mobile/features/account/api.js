import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, useAuthStore } from '../../store/auth';
import { usePreferencesStore } from '../../store/preferences';

export function usePreferences() {
  const hydrate = usePreferencesStore((s) => s.hydrate);
  return useQuery({
    queryKey: ['preferences'],
    queryFn: async () => {
      const prefs = await api.get('/api/v1/users/me/preferences');
      hydrate(prefs);
      return prefs;
    },
  });
}

export function useUpdatePreferences() {
  const qc = useQueryClient();
  const hydrate = usePreferencesStore((s) => s.hydrate);
  return useMutation({
    mutationFn: (patch) => api.patch('/api/v1/users/me/preferences', patch),
    onSuccess: (prefs) => {
      hydrate(prefs);
      qc.setQueryData(['preferences'], prefs);
    },
  });
}

export function useUpdateProfile() {
  const setUser = useAuthStore((s) => s.setUser);
  return useMutation({
    mutationFn: (patch) => api.patch('/api/v1/users/me', patch),
    onSuccess: (user) => setUser(user),
  });
}

export function useChangePassword() {
  return useMutation({ mutationFn: (body) => api.post('/api/v1/users/me/password', body) });
}

export function useSessions() {
  return useQuery({ queryKey: ['sessions'], queryFn: () => api.get('/api/v1/users/me/sessions') });
}

export function useRevokeSession() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id) => api.del(`/api/v1/users/me/sessions/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['sessions'] }),
  });
}

export function useAuditLogs(limit = 50) {
  return useQuery({ queryKey: ['audit', limit], queryFn: () => api.get(`/api/v1/users/me/audit-logs?limit=${limit}`) });
}
