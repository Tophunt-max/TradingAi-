import { View } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { AppText, Badge, Card, NoticeBanner, QueryState, Screen } from '../../components/ui';
import { useTheme } from '../../components/theme/ThemeProvider';
import { useBrokers } from '../../features/trading/api';

export default function Brokers() {
  const theme = useTheme();
  const q = useBrokers();
  return (
    <Screen edges={['bottom']}>
      <QueryState query={q}>
        {(rows) =>
          rows.map((b) => (
            <Card key={b.id} testID={`broker-${b.id}`}>
              <View style={{ flexDirection: 'row', gap: 12, alignItems: 'center' }}>
                <View style={{ width: 40, height: 40, borderRadius: 12, backgroundColor: theme.colors.surfaceRaised, alignItems: 'center', justifyContent: 'center' }}>
                  <Ionicons name={b.kind === 'PAPER' ? 'flask-outline' : 'business-outline'} size={20} color={b.kind === 'PAPER' ? theme.colors.primary : theme.colors.textMuted} />
                </View>
                <View style={{ flex: 1 }}>
                  <AppText variant="bodyStrong">{b.name}</AppText>
                  <AppText variant="caption" tone="muted">{b.note}</AppText>
                </View>
                <Badge label={b.status === 'CONNECTED' ? 'CONNECTED' : 'NOT INTEGRATED'} tone={b.status === 'CONNECTED' ? 'positive' : 'neutral'} />
              </View>
            </Card>
          ))
        }
      </QueryState>
      <NoticeBanner>
        Broker credentials will be stored only on the server, never in the app. Live trading stays locked until a verified adapter, risk checks and your
        explicit authorisation are all in place.
      </NoticeBanner>
    </Screen>
  );
}
