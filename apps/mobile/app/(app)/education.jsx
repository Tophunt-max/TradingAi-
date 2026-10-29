import { useState } from 'react';
import { View } from 'react-native';
import { AppText, BottomSheet, Card, ChipRow, ListRow, Screen } from '../../components/ui';
import { LESSONS } from '../../features/education/content';

const SECTIONS = ['Courses', 'Indicators', 'Methodology', 'Glossary'];

export default function Education() {
  const [section, setSection] = useState('Courses');
  const [open, setOpen] = useState(null);
  return (
    <Screen edges={['bottom']}>
      <ChipRow value={section} onChange={setSection} options={SECTIONS.map((s) => ({ value: s, label: s }))} />
      <Card>
        {LESSONS.filter((l) => l.section === section).map((l) => (
          <ListRow key={l.id} testID={`lesson-${l.id}`} title={l.title} subtitle={`${l.minutes} min read`} icon="book-outline" onPress={() => setOpen(l)} />
        ))}
      </Card>
      <BottomSheet visible={!!open} onClose={() => setOpen(null)} title={open?.title}>
        <View style={{ gap: 10 }}>
          {open?.body.map((p) => <AppText key={p} tone="muted">{p}</AppText>)}
        </View>
      </BottomSheet>
    </Screen>
  );
}
