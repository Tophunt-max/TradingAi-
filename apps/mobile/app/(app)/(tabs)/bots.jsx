import { useState } from 'react';
import { router } from 'expo-router';
import { Fab, NoticeBanner, QueryState, Screen, SegmentedControl, StateView } from '../../../components/ui';
import { BotCard } from '../../../features/bots/BotCard';
import { useBots } from '../../../features/trading/api';

const TABS = ['ACTIVE', 'PAUSED', 'STOPPED', 'DRAFT'];

export default function Bots() {
  const bots = useBots();
  const [picked, setPicked] = useState(null);
  const counts = Object.fromEntries(TABS.map((t) => [t, (bots.data ?? []).filter((b) => b.status === t).length]));
  const tab = picked ?? (counts.ACTIVE ? 'ACTIVE' : 'DRAFT');
  const setTab = setPicked;
  return (
    <Screen title="My Bots" subtitle="All bots share one global risk budget" refreshing={bots.isRefetching} onRefresh={bots.refetch}>
      <SegmentedControl
        value={tab}
        onChange={setTab}
        options={TABS.map((t) => ({ value: t, label: `${t[0]}${t.slice(1).toLowerCase()} (${counts[t]})` }))}
        testID="bot-tabs"
      />
      <Fab label="Create New Bot" onPress={() => router.push('/bots/new')} testID="create-bot" />
      <QueryState query={bots}>
        {(list) => {
          const rows = list.filter((b) => b.status === tab);
          if (!rows.length) {
            return (
              <StateView
                kind="empty"
                title={tab === 'DRAFT' ? 'No draft bots' : `No ${tab.toLowerCase()} bots`}
                message={tab === 'DRAFT' ? 'Create a bot with AI Auto Create, the manual builder or a template.' : 'Start a validated bot from its details screen.'}
              />
            );
          }
          return rows.map((b) => <BotCard key={b.id} bot={b} />);
        }}
      </QueryState>
      <NoticeBanner tone="info">
        Status and health come from the backend worker. Running bots evaluate each closed candle; every signal goes through the global risk manager and, if allowed, becomes a simulated paper order. Live trading is disabled.
      </NoticeBanner>
    </Screen>
  );
}
