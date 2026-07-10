import { useEffect, useState } from 'react'

// Shared live-search debounce (originally written for Datasets.tsx's search
// box, UX package — extracted here so the experiments list's tag-aware
// search reuses the same pattern instead of a second copy).
export function useDebouncedValue<T>(value: T, delayMs: number): T {
  const [debounced, setDebounced] = useState(value)
  useEffect(() => {
    const t = setTimeout(() => setDebounced(value), delayMs)
    return () => clearTimeout(t)
  }, [value, delayMs])
  return debounced
}
