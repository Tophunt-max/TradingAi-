import { Stack, useLocalSearchParams } from 'expo-router';
import { Screen, StateView } from '../../../components/ui';
import { PlannedFeature } from '../../../components/PlannedFeature';
import { FEATURES } from '../../../features/roadmap';

export default function FeatureScreen() {
  const { id } = useLocalSearchParams();
  const feature = FEATURES[id];
  return (
    <Screen edges={['bottom']}>
      <Stack.Screen options={{ title: feature?.title ?? 'Not found' }} />
      {feature ? <PlannedFeature id={id} /> : <StateView kind="empty" title="Page not found" />}
    </Screen>
  );
}
