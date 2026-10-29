// Clearly labelled SAMPLE data used only for the "Preview sample layout"
// toggle. It is never mixed with the user's paper or live records.
// Quantities are in lots; real lot sizes come from the instrument master (Phase 3).
export const SAMPLE_ORDERS = [
  { id: 'sample-1', broker_order_id: null, instrument: 'NIFTY 24700 CE', side: 'BUY', quantity: '1', order_type: 'LIMIT', status: 'PENDING', avg_fill_price: null, created_at: '2026-10-29T04:15:00Z' },
  { id: 'sample-2', broker_order_id: null, instrument: 'BANKNIFTY 52500 PE', side: 'SELL', quantity: '1', order_type: 'MARKET', status: 'CANCELLED', avg_fill_price: null, created_at: '2026-10-29T04:02:00Z' },
  { id: 'sample-3', broker_order_id: null, instrument: 'NIFTY 24600 CE', side: 'BUY', quantity: '1', order_type: 'LIMIT', status: 'EXECUTED', avg_fill_price: '165.20', created_at: '2026-10-29T03:50:00Z' },
];

export const SAMPLE_POSITIONS = [
  { id: 'sample-p1', instrument: 'NIFTY 24600 CE', quantity: '1', avg_price: '165.20', ltp: '178.60', unrealized_pnl: null, stop_loss: '132.00', target: '223.00', opened_at: '2026-10-29T03:50:00Z' },
];
