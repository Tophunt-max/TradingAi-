import { router } from 'expo-router';
import { Card, CardHeader, ListRow, Screen } from '../../components/ui';
import { FEATURES, MORE_SECTIONS } from '../../features/roadmap';

export default function More() {
  return (
    <Screen edges={['bottom']}>
      {MORE_SECTIONS.map((section) => (
        <Card key={section.title}>
          <CardHeader title={section.title} />
          {section.items.map((id) => (
            <ListRow
              key={id}
              testID={`more-${id}`}
              title={FEATURES[id].title}
              subtitle={`Phase ${FEATURES[id].phase} · not built yet`}
              icon={FEATURES[id].icon}
              onPress={() => router.push(`/feature/${id}`)}
            />
          ))}
        </Card>
      ))}
      <Card>
        <CardHeader title="Account & system" />
        <ListRow title="Profile & Settings" icon="person-circle-outline" onPress={() => router.push('/settings')} />
        <ListRow title="System Health" icon="pulse-outline" onPress={() => router.push('/system-health')} />
      </Card>
    </Screen>
  );
}
