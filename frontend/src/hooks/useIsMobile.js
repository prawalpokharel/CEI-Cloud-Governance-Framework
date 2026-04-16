import { useEffect, useState } from 'react';

/**
 * Returns true when the viewport is narrower than `breakpoint` px.
 * SSR-safe: during server render and the very first client render the
 * value is `false`; it re-renders with the real value after hydration.
 *
 * Used by inline-style pages to conditionally apply mobile overrides
 * without pulling in a full CSS-in-JS solution.
 */
export default function useIsMobile(breakpoint = 720) {
  const [isMobile, setIsMobile] = useState(false);

  useEffect(() => {
    if (typeof window === 'undefined') return undefined;
    const mq = window.matchMedia(`(max-width: ${breakpoint}px)`);
    const update = () => setIsMobile(mq.matches);
    update();
    // addEventListener is the modern API; fall back to deprecated
    // addListener for Safari < 14.
    if (mq.addEventListener) {
      mq.addEventListener('change', update);
      return () => mq.removeEventListener('change', update);
    }
    mq.addListener(update);
    return () => mq.removeListener(update);
  }, [breakpoint]);

  return isMobile;
}
