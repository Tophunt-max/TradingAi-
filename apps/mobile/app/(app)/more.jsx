import { router } from 'expo-router';
import { Badge, Card, CardHeader, ListRow, Screen } from '../../components/ui';
import { FEATURES, MORE_SECTIONS, featureHref } from '../../features/roadmap';

export default function More() {
  return (
    <Screen edges={['bottom']}>
      {MORE_SECTIONS.map((section) => (
        <Card key={section.title}>
          <CardHeader title={section.title} />
          {section.items.map((id) => {
            const f = FEATURES[id];
            return (
              <ListRow
                key={id}
                testID={`more-${id}`}
                title={f.title}
                subtitle={f.summary}
                icon={f.icon}
                right={f.route ? null : <Badge label={`PHASE ${f.phase}`} />}
                onPress={() => router.push(featureHref(id))}
              />
            );
          })}
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
