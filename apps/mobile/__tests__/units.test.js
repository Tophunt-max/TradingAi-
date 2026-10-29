import { isSixDigitCode, passwordProblems } from '../features/auth/validation';
import { isStale, STALE_AFTER_MS } from '../features/system/api';
import { formatINR, formatIST } from '../services/format';

describe('validation', () => {
  it('mirrors the backend password policy', () => {
    expect(passwordProblems('short1')).toMatch(/10 characters/);
    expect(passwordProblems('onlyletterslong')).toMatch(/letter and one number/);
    expect(passwordProblems('Str0ngPassw0rd!')).toBeNull();
  });

  it('accepts only six-digit codes', () => {
    expect(isSixDigitCode('123456')).toBe(true);
    expect(isSixDigitCode('12345')).toBe(false);
    expect(isSixDigitCode('12a456')).toBe(false);
  });
});

describe('data freshness', () => {
  it('flags data older than the threshold as stale', () => {
    const now = Date.parse('2026-10-29T05:00:00Z');
    expect(isStale(new Date(now - 1000).toISOString(), now)).toBe(false);
    expect(isStale(new Date(now - STALE_AFTER_MS - 1).toISOString(), now)).toBe(true);
    expect(isStale(null, now)).toBe(true);
  });
});

describe('formatting', () => {
  it('displays UTC timestamps in IST', () => {
    expect(formatIST('2026-01-01T03:45:00Z')).toBe('09:15:00');
  });

  it('formats rupees with Indian grouping', () => {
    expect(formatINR(1234567.5)).toBe('₹12,34,567.50');
    expect(formatINR('x')).toBe('—');
  });
});
