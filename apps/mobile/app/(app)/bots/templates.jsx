import { useState } from 'react';
import { router } from 'expo-router';
import { Badge, Card, ListRow, QueryState, Screen, SegmentedControl } from '../../../components/ui';
import { useStrategies } from '../../../features/trading/api';

const ICONS = {
  momentum: 'trending-up', scalping: 'flash-outline', breakout: 'arrow-up-circle-outline', reversal: 'repeat-outline',
  smc: 'cube-outline', ai_adaptive: 'sparkles-outline', custom: 'options-outline', dca_grid: 'grid-outline',
};

export default function Templates() {
  const q = useStrategies();
  const [tab, setTab] = useState('popular');
  return (
    <Screen edges={['bottom']}>
      <SegmentedControl value={tab} onChange={setTab} options={[{ value: 'popular', label: 'Popular' }, { value: 'custom', label: 'Custom & research' }]} />
      <QueryState query={q}>
        {(list) => (
          <Card>
            {list
              .filter((s) => (tab === 'popular' ? !['custom', 'dca_grid'].includes(s.id) : ['custom', 'dca_grid'].includes(s.id)))
              .map((s) => (
                <ListRow
                  key={s.id}
                  testID={`template-${s.id}`}
                  icon={ICONS[s.id]}
                  title={s.name}
                  subtitle={`${s.summary} · ${s.timeframes.join(', ')}`}
                  right={s.research_only ? <Badge label="RESEARCH" tone="warning" /> : null}
                  onPress={() => router.push({ pathname: '/bots/builder', params: { template: s.id } })}
                />
              ))}
          </Card>
        )}
      </QueryState>
    </Screen>
  );
}
