import { useState } from 'react';
import { AppText, Button, Card, Screen, TextField } from '../../../components/ui';
import { useChangePassword } from '../../../features/account/api';
import { describeError } from '../../../features/auth/api';
import { passwordProblems } from '../../../features/auth/validation';

export default function Security() {
  const [current, setCurrent] = useState('');
  const [next, setNext] = useState('');
  const change = useChangePassword();
  const problem = next ? passwordProblems(next) : null;
  return (
    <Screen keyboard edges={['bottom']} contentStyle={{ maxWidth: 520 }}>
      <Card style={{ gap: 12 }}>
        <TextField label="Current password" value={current} onChangeText={setCurrent} secure autoComplete="current-password" />
        <TextField label="New password" value={next} onChangeText={setNext} secure autoComplete="new-password" error={problem} hint="Other devices will be signed out." />
        {change.error ? <AppText tone="negative">{describeError(change.error)}</AppText> : null}
        {change.isSuccess ? <AppText tone="positive">{change.data?.message}</AppText> : null}
        <Button
          title="Change password"
          disabled={!current || !next || !!problem}
          loading={change.isPending}
          onPress={() =>
            change.mutate(
              { current_password: current, new_password: next },
              {
                onSuccess: () => {
                  setCurrent('');
                  setNext('');
                },
              },
            )
          }
        />
      </Card>
    </Screen>
  );
}
