import { Redirect } from 'expo-router';
import { useAuthStore } from '../../store/auth';

export default function AuthEntry() {
  const seen = useAuthStore((s) => s.onboardingSeen);
  return <Redirect href={seen ? '/welcome' : '/onboarding'} />;
}
