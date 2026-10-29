import { ApiError, createApiClient } from '../services/api';

function jsonResponse(status, body) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
    headers: { get: () => 'req-123' },
  };
}

function makeTokens(initial = { access: 'old-access', refresh: 'old-refresh' }) {
  const state = { ...initial, expired: 0 };
  return {
    state,
    getAccess: () => state.access,
    getRefresh: async () => state.refresh,
    setTokens: async (pair) => {
      state.access = pair.access_token;
      state.refresh = pair.refresh_token;
    },
    onSessionExpired: async () => {
      state.expired += 1;
    },
  };
}

describe('api client', () => {
  it('normalises the backend error envelope', async () => {
    const fetchImpl = jest.fn(async () =>
      jsonResponse(409, { error: { code: 'ACCOUNT_EXISTS', message: 'exists', details: null, request_id: 'r1' } }),
    );
    const api = createApiClient({ baseUrl: 'http://x', fetchImpl });
    await expect(api.post('/a', {}, { auth: false })).rejects.toMatchObject({
      status: 409,
      code: 'ACCOUNT_EXISTS',
      message: 'exists',
      requestId: 'r1',
    });
  });

  it('reports network failures as offline errors', async () => {
    const api = createApiClient({ baseUrl: 'http://x', fetchImpl: jest.fn(async () => { throw new TypeError('fail'); }) });
    const err = await api.get('/a', { auth: false }).catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.isNetwork).toBe(true);
    expect(err.code).toBe('NETWORK_ERROR');
  });

  it('refreshes once for concurrent 401s and retries with the new token', async () => {
    const tokens = makeTokens();
    let refreshCalls = 0;
    const fetchImpl = jest.fn(async (url, init) => {
      if (url.endsWith('/api/v1/auth/refresh')) {
        refreshCalls += 1;
        expect(JSON.parse(init.body).refresh_token).toBe('old-refresh');
        await new Promise((r) => setTimeout(r, 5));
        return jsonResponse(200, { access_token: 'new-access', refresh_token: 'new-refresh', user: {} });
      }
      return init.headers.Authorization === 'Bearer new-access'
        ? jsonResponse(200, { ok: url })
        : jsonResponse(401, { error: { code: 'UNAUTHENTICATED', message: 'no' } });
    });
    const api = createApiClient({ baseUrl: 'http://x', fetchImpl, tokens });
    const [a, b] = await Promise.all([api.get('/one'), api.get('/two')]);
    expect(a.ok).toBe('http://x/one');
    expect(b.ok).toBe('http://x/two');
    expect(refreshCalls).toBe(1);
    expect(tokens.state.refresh).toBe('new-refresh');
  });

  it('signals session expiry when refresh fails', async () => {
    const tokens = makeTokens();
    const fetchImpl = jest.fn(async () => jsonResponse(401, { error: { code: 'INVALID_REFRESH_TOKEN', message: 'expired' } }));
    const api = createApiClient({ baseUrl: 'http://x', fetchImpl, tokens });
    await expect(api.get('/me')).rejects.toMatchObject({ code: 'SESSION_EXPIRED', status: 401 });
    expect(tokens.state.expired).toBe(1);
  });

  it('does not attempt refresh for unauthenticated endpoints', async () => {
    const tokens = makeTokens();
    const fetchImpl = jest.fn(async () => jsonResponse(401, { error: { code: 'INVALID_CREDENTIALS', message: 'bad' } }));
    const api = createApiClient({ baseUrl: 'http://x', fetchImpl, tokens });
    await expect(api.post('/api/v1/auth/login', {}, { auth: false })).rejects.toMatchObject({ code: 'INVALID_CREDENTIALS' });
    expect(fetchImpl).toHaveBeenCalledTimes(1);
  });
});
