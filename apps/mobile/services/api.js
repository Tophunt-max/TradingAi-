import { API_URL, REQUEST_TIMEOUT_MS } from './config';

/** Normalised API error matching the backend error envelope. */
export class ApiError extends Error {
  constructor({ status, code, message, details, requestId, isNetwork = false }) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
    this.details = details;
    this.requestId = requestId;
    this.isNetwork = isNetwork;
  }
}

/**
 * Creates an API client. Token handling is injected so the client stays
 * testable and independent of the auth store.
 *
 * tokens: { getAccess(), getRefresh(), setTokens(pair), onSessionExpired() }
 */
export function createApiClient({ baseUrl = API_URL, fetchImpl = globalThis.fetch, tokens, timeoutMs = REQUEST_TIMEOUT_MS } = {}) {
  let refreshing = null; // single-flight refresh promise

  async function rawRequest(path, { method = 'GET', body, auth = true, headers = {}, signal } = {}) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    if (signal) signal.addEventListener?.('abort', () => controller.abort());
    const h = { Accept: 'application/json', ...headers };
    if (body !== undefined) h['Content-Type'] = 'application/json';
    if (auth && tokens?.getAccess()) h.Authorization = `Bearer ${tokens.getAccess()}`;

    let res;
    try {
      res = await fetchImpl(`${baseUrl}${path}`, {
        method,
        headers: h,
        body: body !== undefined ? JSON.stringify(body) : undefined,
        signal: controller.signal,
      });
    } catch (e) {
      throw new ApiError({
        status: 0,
        code: e?.name === 'AbortError' ? 'TIMEOUT' : 'NETWORK_ERROR',
        message: e?.name === 'AbortError' ? 'The request timed out.' : 'Unable to reach the QuantAI server.',
        isNetwork: true,
      });
    } finally {
      clearTimeout(timer);
    }

    if (res.status === 204) return { res, data: null };
    let data = null;
    try {
      data = await res.json();
    } catch {
      data = null;
    }
    if (!res.ok) {
      const err = data?.error ?? {};
      throw new ApiError({
        status: res.status,
        code: err.code ?? 'HTTP_ERROR',
        message: err.message ?? `Request failed (${res.status}).`,
        details: err.details,
        requestId: err.request_id ?? res.headers?.get?.('x-request-id'),
      });
    }
    return { res, data };
  }

  async function refreshTokens() {
    if (!refreshing) {
      refreshing = (async () => {
        const refresh_token = await tokens.getRefresh();
        if (!refresh_token) throw new ApiError({ status: 401, code: 'NO_SESSION', message: 'Please sign in.' });
        const { data } = await rawRequest('/api/v1/auth/refresh', { method: 'POST', body: { refresh_token }, auth: false });
        await tokens.setTokens(data);
        return data;
      })().finally(() => {
        refreshing = null;
      });
    }
    return refreshing;
  }

  async function request(path, options = {}) {
    try {
      const { data } = await rawRequest(path, options);
      return data;
    } catch (e) {
      const canRefresh = options.auth !== false && e instanceof ApiError && e.status === 401 && tokens;
      if (!canRefresh) throw e;
      try {
        await refreshTokens();
      } catch (refreshErr) {
        await tokens.onSessionExpired?.();
        throw refreshErr instanceof ApiError && refreshErr.status === 401
          ? new ApiError({ status: 401, code: 'SESSION_EXPIRED', message: 'Your session has expired. Please sign in again.' })
          : refreshErr;
      }
      const { data } = await rawRequest(path, options);
      return data;
    }
  }

  return {
    request,
    get: (path, opts) => request(path, { ...opts, method: 'GET' }),
    post: (path, body, opts) => request(path, { ...opts, method: 'POST', body }),
    patch: (path, body, opts) => request(path, { ...opts, method: 'PATCH', body }),
    del: (path, opts) => request(path, { ...opts, method: 'DELETE' }),
  };
}
