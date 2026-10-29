import { render, screen } from '@testing-library/react-native';
import { ThemeProvider } from '../components/theme/ThemeProvider';
import { BotCard } from '../features/bots/BotCard';
import { actionsFor, isEditable } from '../features/bots/health';

describe('bot lifecycle UI', () => {
  it('offers only the transitions the backend allows', () => {
    expect(actionsFor('DRAFT')).toEqual(['start']);
    expect(actionsFor('ACTIVE')).toEqual(['pause', 'stop']);
    expect(actionsFor('PAUSED')).toEqual(['resume', 'stop']);
    expect(actionsFor('STOPPED')).toEqual(['start']);
    expect(isEditable('ACTIVE')).toBe(false);
    expect(isEditable('STOPPED')).toBe(true);
  });

  const bot = {
    id: 'b1', name: 'Nifty Momentum', underlying: 'NIFTY', strategy_name: 'Momentum', timeframe: '5m',
    status: 'ACTIVE', mode: 'PAPER', creation_method: 'AI', today_pnl: null, signals_today: 1, trade_count: 0,
    health: 'OK', health_detail: 'Evaluating', last_signal: 'CE at 24001.00', last_signal_at: '2026-10-28T05:00:00Z',
    run: { mode: 'SIGNAL_ONLY' },
  };

  it('shows backend health and signal-only mode on the card', async () => {
    await render(<ThemeProvider><BotCard bot={bot} /></ThemeProvider>);
    expect(screen.getByText('HEALTHY')).toBeTruthy();
    expect(screen.getByText('SIGNAL-ONLY')).toBeTruthy();
    expect(screen.getByText(/Last signal CE at 24001.00/)).toBeTruthy();
  });

  it('shows real paper P&L and no signal-only badge for a paper run', async () => {
    await render(<ThemeProvider><BotCard bot={{ ...bot, run: { mode: 'PAPER' }, today_pnl: '-125.40', trade_count: 2 }} /></ThemeProvider>);
    expect(screen.queryByText('SIGNAL-ONLY')).toBeNull();
    expect(screen.getByText('₹-125.40')).toBeTruthy();
    expect(screen.getByText('2')).toBeTruthy();
  });
});
