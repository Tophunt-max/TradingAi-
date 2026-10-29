// Display mapping for backend bot health values.
export const HEALTH = {
  OK: { label: 'HEALTHY', tone: 'positive', icon: 'pulse' },
  WAITING_FOR_WORKER: { label: 'WAITING FOR WORKER', tone: 'warning', icon: 'hourglass-outline' },
  STALE: { label: 'WORKER STALE', tone: 'warning', icon: 'time-outline' },
  ERROR: { label: 'ERROR', tone: 'negative', icon: 'alert-circle-outline' },
  BLOCKED: { label: 'BLOCKED', tone: 'negative', icon: 'lock-closed-outline' },
  PAUSED: { label: 'PAUSED', tone: 'warning', icon: 'pause-circle-outline' },
  NOT_RUNNING: { label: 'NOT RUNNING', tone: 'neutral', icon: 'stop-circle-outline' },
};

export const STATUS_TONE = { DRAFT: 'neutral', ACTIVE: 'positive', PAUSED: 'warning', STOPPED: 'negative' };

/** Lifecycle actions available in each backend state. */
export function actionsFor(status) {
  switch (status) {
    case 'DRAFT':
    case 'STOPPED':
      return ['start'];
    case 'ACTIVE':
      return ['pause', 'stop'];
    case 'PAUSED':
      return ['resume', 'stop'];
    default:
      return [];
  }
}

export function isEditable(status) {
  return status === 'DRAFT' || status === 'STOPPED';
}
