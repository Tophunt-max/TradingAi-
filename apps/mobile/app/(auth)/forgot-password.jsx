import { useState } from 'react';
import { router } from 'expo-router';
import { AppText, Button, Screen, TextField } from '../../components/ui';
import { describeError, useForgotPassword } from '../../features/auth/api';

export default function ForgotPassword() {
  const [identifier, setIdentifier] = useState('');
  const forgot = useForgotPassword();

  const submit = () => {
    if (!identifier.trim()) return;
    forgot.mutate(identifier.trim(), {
      onSuccess: () => router.push({ pathname: '/reset-password', params: { identifier: identifier.trim() } }),
    });
  };

  return (
    <Screen title="Forgot password" subtitle="We'll send a reset code to your email or mobile" keyboard edges={['bottom']} contentStyle={{ maxWidth: 480 }}>
      <TextField
        label="Email or mobile"
        value={identifier}
        onChangeText={setIdentifier}
        autoCapitalize="none"
        keyboardType="email-address"
        leftIcon="person-circle-outline"
        onSubmitEditing={submit}
      />
      {forgot.error ? <AppText tone="negative" accessibilityRole="alert">{describeError(forgot.error)}</AppText> : null}
      <Button title="Send reset code" fullWidth loading={forgot.isPending} disabled={!identifier.trim()} onPress={submit} />
    </Screen>
  );
}
