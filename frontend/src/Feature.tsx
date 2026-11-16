import type { ReactNode } from 'react'
import { useFeature, type FeatureName } from './features'

type FeatureProps = {
  name: FeatureName
  children: ReactNode
  // Shown when the flag is off; a flagged page can pass <NotFound /> so its URL looks unknown.
  fallback?: ReactNode
}

// Renders its children only when the server has the flag on. Nothing renders while the
// flags load, so a hidden feature never flashes on the live site.
export default function Feature({ name, children, fallback = null }: FeatureProps) {
  const on = useFeature(name)
  if (on === undefined) return null
  return on ? children : fallback
}
