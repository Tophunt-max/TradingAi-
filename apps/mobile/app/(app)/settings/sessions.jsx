import { useState } from 'react';
import { AppText, Badge, Button, Card, ConfirmDialog, ListRow, QueryState, Screen } from '../../../components/ui';
import { useRevokeSession, useSessions } from '../../../features/account/api';
import { formatIST } from '../../../services/format';

export default function Sessions() {
  const query = useSessions();
  const revoke = useRevokeSession();
  const [target, setTarget] = useState(null);
  return (
    <Screen edges={['bottom']} refreshing={query.isRefetching} onRefresh={query.refetch}>
      <QueryState query={query} isEmpty={(d) => !d?.length} emptyMessage="No active sessions.">
        {(sessions) => (
          <Card>
            {sessions.map((s) => (
              <ListRow
                key={s.id}
                icon="phone-portrait-outline"
                title={s.device_name || s.user_agent || 'Unknown device'}
                subtitle={`Last active ${formatIST(s.last_used_at ?? s.created_at, { withDate: true })} IST · ${s.ip_address ?? 'unknown IP'}`}
                right={s.current ? <Badge label="THIS DEVICE" tone="primary" /> : <Button title="Revoke" size="sm" variant="danger" onPress={() => setTarget(s)} />}
              />
            ))}
          </Card>
        )}
      </QueryState>
      {revoke.error ? <AppText tone="negative">{revoke.error.message}</AppText> : null}
      <ConfirmDialog
        visible={!!target}
        title="Revoke session?"
        message="That device will be signed out immediately."
        confirmLabel="Revoke"
        destructive
        loading={revoke.isPending}
        onCancel={() => setTarget(null)}
        onConfirm={() => revoke.mutate(target.id, { onSettled: () => setTarget(null) })}
      />
    </Screen>
  );
}
