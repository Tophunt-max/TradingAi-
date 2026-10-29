import { useRef, useState } from 'react';
import { FlatList, View, useWindowDimensions } from 'react-native';
import { router } from 'expo-router';
import { Ionicons } from '@expo/vector-icons';
import { SafeAreaView } from 'react-native-safe-area-context';
import { AppText, Button } from '../../components/ui';
import { useTheme } from '../../components/theme/ThemeProvider';
import { useAuthStore } from '../../store/auth';

const SLIDES = [
  {
    icon: 'analytics-outline',
    title: 'Market analysis you can inspect',
    body: 'Indicators, price structure and AI probabilities with the reasoning shown, including when the answer is NO TRADE.',
  },
  {
    icon: 'shield-checkmark-outline',
    title: 'Risk limits come first',
    body: 'A deterministic risk manager checks every trade idea. AI suggestions can never bypass it.',
  },
  {
    icon: 'flask-outline',
    title: 'Paper trading by default',
    body: 'Practise with simulated orders and realistic costs. Live trading stays locked until every safety check passes and you authorise it.',
  },
  {
    icon: 'warning-outline',
    title: 'No guaranteed profits',
    body: 'Derivatives trading carries substantial risk of loss. Predictions can be wrong and past or paper results do not guarantee future returns.',
  },
];

export default function Onboarding() {
  const theme = useTheme();
  const { width } = useWindowDimensions();
  const [index, setIndex] = useState(0);
  const listRef = useRef(null);
  const markSeen = useAuthStore((s) => s.markOnboardingSeen);
  const last = index === SLIDES.length - 1;

  const finish = async () => {
    await markSeen();
    router.replace('/welcome');
  };

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: theme.colors.background }}>
      <View style={{ alignItems: 'flex-end', padding: 16 }}>
        <Button title="Skip" variant="ghost" size="sm" onPress={finish} />
      </View>
      <FlatList
        ref={listRef}
        data={SLIDES}
        horizontal
        pagingEnabled
        showsHorizontalScrollIndicator={false}
        keyExtractor={(s) => s.title}
        onMomentumScrollEnd={(e) => setIndex(Math.round(e.nativeEvent.contentOffset.x / width))}
        renderItem={({ item }) => (
          <View style={{ width, padding: 32, justifyContent: 'center', alignItems: 'center', gap: 16 }}>
            <View style={{ width: 96, height: 96, borderRadius: 28, backgroundColor: theme.colors.primarySoft, alignItems: 'center', justifyContent: 'center' }}>
              <Ionicons name={item.icon} size={44} color={theme.colors.primary} />
            </View>
            <AppText variant="title" style={{ textAlign: 'center' }}>{item.title}</AppText>
            <AppText tone="muted" style={{ textAlign: 'center', maxWidth: 440 }}>{item.body}</AppText>
          </View>
        )}
      />
      <View style={{ padding: 24, gap: 20, width: '100%', maxWidth: 520, alignSelf: 'center' }}>
        <View style={{ flexDirection: 'row', justifyContent: 'center', gap: 8 }} accessibilityLabel={`Slide ${index + 1} of ${SLIDES.length}`}>
          {SLIDES.map((s, i) => (
            <View key={s.title} style={{ width: i === index ? 22 : 8, height: 8, borderRadius: 4, backgroundColor: i === index ? theme.colors.primary : theme.colors.border }} />
          ))}
        </View>
        <Button
          title={last ? 'Get started' : 'Next'}
          fullWidth
          onPress={() => {
            if (last) return finish();
            const next = index + 1;
            listRef.current?.scrollToIndex({ index: next, animated: true });
            setIndex(next);
          }}
        />
      </View>
    </SafeAreaView>
  );
}
