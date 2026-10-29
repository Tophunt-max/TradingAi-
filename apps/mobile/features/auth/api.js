import { useMutation } from '@tanstack/react-query';
import { api, useAuthStore } from '../../store/auth';

const deviceName = 'QuantAI mobile';

export function useLogin() {
  const applyTokens = useAuthStore((s) => s.applyTokens);
  return useMutation({
    mutationFn: (body) => api.post('/api/v1/auth/login', { ...body, device_name: deviceName }, { auth: false }),
    onSuccess: (pair) => applyTokens(pair),
  });
}

export function useRegister() {
  return useMutation({
    mutationFn: (body) => api.post('/api/v1/auth/register', body, { auth: false }),
  });
}

export function useVerify() {
  const applyTokens = useAuthStore((s) => s.applyTokens);
  return useMutation({
    mutationFn: (body) => api.post('/api/v1/auth/verify', { ...body, device_name: deviceName }, { auth: false }),
    onSuccess: (pair) => applyTokens(pair),
  });
}

export function useResendCode() {
  return useMutation({
    mutationFn: (identifier) => api.post('/api/v1/auth/verify/resend', { identifier }, { auth: false }),
  });
}

export function useForgotPassword() {
  return useMutation({
    mutationFn: (identifier) => api.post('/api/v1/auth/password/forgot', { identifier }, { auth: false }),
  });
}

export function useResetPassword() {
  return useMutation({
    mutationFn: (body) => api.post('/api/v1/auth/password/reset', body, { auth: false }),
  });
}

/** Turn backend validation details into a readable message. */
export function describeError(error) {
  if (!error) return null;
  if (error.code === 'VALIDATION_ERROR' && Array.isArray(error.details) && error.details[0]?.msg) {
    return error.details[0].msg.replace(/^Value error, /, '');
  }
  return error.message;
}
