import { router } from 'expo-router';
import { Button, Screen, StateView } from '../components/ui';

export default function NotFound() {
  return (
    <Screen>
      <StateView kind="empty" title="Page not found" message="This screen does not exist." />
      <Button title="Go home" onPress={() => router.replace('/')} />
    </Screen>
  );
}
