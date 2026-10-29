import { useWindowDimensions } from 'react-native';
import { breakpoints } from '../constants/theme';

export function useResponsive() {
  const { width, height } = useWindowDimensions();
  return {
    width,
    height,
    isSmall: width < 360,
    isTablet: width >= breakpoints.tablet,
    isDesktop: width >= breakpoints.desktop,
    // Content is centred and width-capped on tablets / web.
    contentMaxWidth: width >= breakpoints.desktop ? 1040 : width >= breakpoints.tablet ? 760 : undefined,
    columns: width >= breakpoints.desktop ? 3 : width >= breakpoints.tablet ? 2 : 1,
  };
}
