import { useQuery } from '@tanstack/react-query';
import { Platform } from 'react-native';
import { api } from '../../store/auth';
import { exportPath } from './format';

export function useDailyReport(day, mode) {
  return useQuery({
    queryKey: ['report-daily', day, mode],
    queryFn: () => api.get(`/api/v1/reports/daily?day=${day}&account_mode=${mode}`),
    enabled: !!day && !!mode,
  });
}

export function usePnl(from, to, mode) {
  return useQuery({
    queryKey: ['report-pnl', from, to, mode],
    queryFn: () => api.get(`/api/v1/reports/pnl?from=${from}&to=${to}&account_mode=${mode}`),
    enabled: !!from && !!to && !!mode,
  });
}

/** Exports need the auth header, so a plain link cannot be used. On web the CSV is fetched
 * with the session and saved through a Blob; native file sharing is not installed. */
export const CAN_EXPORT = Platform.OS === 'web';

export async function downloadCsv(id, mode) {
  if (!CAN_EXPORT) throw new Error('Export is available on the web app.');
  const text = await api.getText(exportPath(id, mode));
  const blob = new Blob([text], { type: 'text/csv' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `quantai-${mode.toLowerCase()}-${id}.csv`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}
