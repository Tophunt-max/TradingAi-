import { useQuery } from '@tanstack/react-query';
import { api } from '../../store/auth';

/** GET /api/v1/intelligence/dashboard: read-only sections, each with status OK / NO_DATA / UNAVAILABLE. */
export function useIntelligenceDashboard(underlying, timeframe) {
  return useQuery({
    queryKey: ['intelligence', underlying, timeframe],
    queryFn: () => api.get(`/api/v1/intelligence/dashboard?underlying=${underlying}&timeframe=${timeframe}`),
    refetchInterval: 60_000,
  });
}

/** GET /api/v1/decision/{underlying}: the multi-factor DecisionReport for the active account. */
export function useDecisionReport(underlying, timeframe, side = 'auto') {
  return useQuery({
    queryKey: ['decision', underlying, timeframe, side],
    queryFn: () => api.get(`/api/v1/decision/${underlying}?timeframe=${timeframe}&side=${side}`),
    refetchInterval: 60_000,
  });
}

export const DECISION_TONE = { TRADE_PROPOSAL: 'positive', WATCH: 'warning', NO_TRADE: 'negative' };
export const DECISION_LABEL = { TRADE_PROPOSAL: 'TRADE PROPOSAL', WATCH: 'WATCH', NO_TRADE: 'NO TRADE' };
export const STATUS_TONE = { OK: 'positive', NO_DATA: 'neutral', UNAVAILABLE: 'warning' };

/** A paper order can be reviewed only for a TRADE_PROPOSAL in the DEMO account. */
export function canReviewPaperOrder(report) {
  return report?.decision === 'TRADE_PROPOSAL' && report?.account_mode === 'DEMO' && !!report?.proposal?.paper_order_request && report?.execution?.available === true;
}

/** Body for POST /api/v1/orders/paper: the proposal's request plus a fresh idempotency key. */
export function paperOrderBody(report, idempotencyKey) {
  const req = report.proposal.paper_order_request;
  const body = { ...req, idempotency_key: idempotencyKey };
  Object.keys(body).forEach((k) => body[k] == null && delete body[k]);
  return body;
}
