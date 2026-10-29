import { useSystemStatus } from '../system/api';

/**
 * Active account mode and the modes the user can pick.
 *
 * Phase 1 (v2 plan): only the DEMO account exists on the backend, so DEMO is always
 * active and REAL is listed as unavailable with the server's reasons. Persisted,
 * server-validated mode switching arrives with the account-mode backend (Phase 3),
 * and REAL becomes selectable only with a verified broker adapter (Phase 7).
 */
export function useAccountMode() {
  const status = useSystemStatus();
  const blockers = status.data?.live_trading_blockers ?? [];
  const realReason = blockers.length
    ? `Not available: ${blockers[0]}`
    : 'Not available: no verified broker integration is connected.';
  return {
    mode: 'DEMO',
    options: [
      { mode: 'DEMO', available: true, reason: 'Starting balance ₹10,000 (virtual).' },
      { mode: 'REAL', available: false, reason: realReason },
    ],
    // No-op until the backend can switch modes; unavailable options cannot be pressed.
    setMode: () => {},
  };
}
