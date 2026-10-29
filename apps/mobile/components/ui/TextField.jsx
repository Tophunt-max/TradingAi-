import { forwardRef, useState } from 'react';
import { Pressable, StyleSheet, TextInput, View } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { useTheme } from '../theme/ThemeProvider';
import { AppText } from './AppText';

export const TextField = forwardRef(function TextField(
  { label, error, hint, secure = false, leftIcon, style, testID, ...inputProps },
  ref,
) {
  const theme = useTheme();
  const [focused, setFocused] = useState(false);
  const [hidden, setHidden] = useState(secure);
  const c = theme.colors;
  const borderColor = error ? c.negative : focused ? c.primary : c.border;

  return (
    <View style={[{ gap: 6 }, style]}>
      {label ? <AppText variant="label" tone="muted">{label.toUpperCase()}</AppText> : null}
      <View style={[s.box, { borderColor, backgroundColor: c.surface, borderRadius: theme.radii.md }]}>
        {leftIcon ? <Ionicons name={leftIcon} size={18} color={c.textMuted} /> : null}
        <TextInput
          ref={ref}
          testID={testID}
          accessibilityLabel={label}
          placeholderTextColor={c.textFaint}
          secureTextEntry={hidden}
          onFocus={(e) => {
            setFocused(true);
            inputProps.onFocus?.(e);
          }}
          onBlur={(e) => {
            setFocused(false);
            inputProps.onBlur?.(e);
          }}
          style={[s.input, theme.typography.body, { color: c.text }]}
          {...inputProps}
        />
        {secure ? (
          <Pressable
            accessibilityRole="button"
            accessibilityLabel={hidden ? 'Show password' : 'Hide password'}
            onPress={() => setHidden((h) => !h)}
            hitSlop={10}
          >
            <Ionicons name={hidden ? 'eye-outline' : 'eye-off-outline'} size={18} color={c.textMuted} />
          </Pressable>
        ) : null}
      </View>
      {error ? (
        <AppText variant="caption" tone="negative" accessibilityLiveRegion="polite">{error}</AppText>
      ) : hint ? (
        <AppText variant="caption" tone="faint">{hint}</AppText>
      ) : null}
    </View>
  );
});

const s = StyleSheet.create({
  box: { flexDirection: 'row', alignItems: 'center', borderWidth: 1, paddingHorizontal: 12, gap: 8, minHeight: 48 },
  input: { flex: 1, paddingVertical: 12, outlineStyle: 'none' },
});
