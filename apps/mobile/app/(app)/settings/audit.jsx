import { Badge, Card, ListRow, QueryState, Screen } from '../../../components/ui';
import { useAuditLogs } from '../../../features/account/api';
import { formatIST } from '../../../services/format';

const LABELS = {
  'auth.register': 'Account created',
  'auth.verify': 'Account verified',
  'auth.login': 'Sign-in attempt',
  'auth.logout': 'Signed out',
  'auth.session_created': 'New session',
  'auth.session_revoked': 'Session revoked',
  'auth.lockout': 'Account temporarily locked',
  'auth.refresh_reuse_detected': 'Suspicious token reuse',
  'auth.password_reset': 'Password reset',
  'auth.password_reset_requested': 'Password reset requested',
  'user.password_change': 'Password change',
  'user.profile_updated': 'Profile updated',
};

export default function Audit() {
  const query = useAuditLogs(50);
  return (
    <Screen edges={['bottom']} refreshing={query.isRefetching} onRefresh={query.refetch}>
      <QueryState query={query} isEmpty={(d) => !d?.items?.length} emptyMessage="No activity recorded yet.">
        {(page) => (
          <Card>
            {page.items.map((e) => (
              <ListRow
                key={e.id}
                title={LABELS[e.action] ?? e.action}
                subtitle={`${formatIST(e.created_at, { withDate: true })} IST · ${e.ip_address ?? '—'}`}
                right={<Badge label={e.outcome} tone={e.outcome === 'SUCCESS' ? 'positive' : 'negative'} />}
              />
            ))}
          </Card>
        )}
      </QueryState>
    </Screen>
  );
}
