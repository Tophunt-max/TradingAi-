import { useState } from 'react';
import { View } from 'react-native';
import { Link, router } from 'expo-router';
import { AppText, Button, SegmentedControl, Screen, TextField } from '../../components/ui';
import { describeError, useLogin } from '../../features/auth/api';

export default function Login() {
  const [method, setMethod] = useState('email');
  const [identifier, setIdentifier] = useState('');
  const [password, setPassword] = useState('');
  const login = useLogin();
  const error = login.error;

  const submit = () => {
    if (!identifier.trim() || !password) return;
    login.mutate(
      { identifier: identifier.trim(), password },
      {
        onError: (e) => {
          if (e.code === 'VERIFICATION_REQUIRED') {
            router.push({ pathname: '/verify', params: { identifier: identifier.trim(), resend: '1' } });
          }
        },
      },
    );
  };

  return (
    <Screen title="Sign in" subtitle="Welcome back to QuantAI F&O" keyboard edges={['bottom']} contentStyle={{ maxWidth: 480 }}>
      <SegmentedControl
        value={method}
        onChange={(m) => {
          setMethod(m);
          setIdentifier('');
        }}
        options={[
          { value: 'email', label: 'Email' },
          { value: 'phone', label: 'Mobile' },
        ]}
      />
      <TextField
        testID="login-identifier"
        label={method === 'email' ? 'Email' : 'Mobile number'}
        value={identifier}
        onChangeText={setIdentifier}
        placeholder={method === 'email' ? 'you@example.com' : '98765 43210'}
        keyboardType={method === 'email' ? 'email-address' : 'phone-pad'}
        autoCapitalize="none"
        autoComplete={method === 'email' ? 'email' : 'tel'}
        leftIcon={method === 'email' ? 'mail-outline' : 'call-outline'}
      />
      <TextField
        testID="login-password"
        label="Password"
        value={password}
        onChangeText={setPassword}
        secure
        autoComplete="password"
        leftIcon="lock-closed-outline"
        onSubmitEditing={submit}
      />
      {error && error.code !== 'VERIFICATION_REQUIRED' ? (
        <AppText tone="negative" accessibilityRole="alert" testID="login-error">{describeError(error)}</AppText>
      ) : null}
      <Button title="Sign in" fullWidth loading={login.isPending} disabled={!identifier || !password} onPress={submit} testID="login-submit" />
      <View style={{ flexDirection: 'row', justifyContent: 'space-between' }}>
        <Link href="/forgot-password"><AppText tone="primary">Forgot password?</AppText></Link>
        <Link href="/register"><AppText tone="primary">Create account</AppText></Link>
      </View>
    </Screen>
  );
}
