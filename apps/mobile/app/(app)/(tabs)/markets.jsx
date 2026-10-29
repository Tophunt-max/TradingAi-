import { router } from 'expo-router';
import { Card, CardHeader, ListRow, Screen } from '../../../components/ui';
import { IndexQuotesCard } from '../../../features/market/IndexQuotes';
import { FEATURES } from '../../../features/roadmap';

const LINKS = ['charts', 'option-chain', 'screener'];

export default function Markets() {
  return (
    <Screen title="Markets" subtitle="NSE / BSE overview">
      <IndexQuotesCard title="Indices" />
      <Card>
        <CardHeader title="Market tools" subtitle="Planned modules" />
        {LINKS.map((id) => (
          <ListRow key={id} title={FEATURES[id].title} subtitle={`Phase ${FEATURES[id].phase}`} icon={FEATURES[id].icon} onPress={() => router.push(`/feature/${id}`)} />
        ))}
      </Card>
    </Screen>
  );
}
