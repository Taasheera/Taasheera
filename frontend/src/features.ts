import { useEffect, useState } from 'react'
import { apiFetch } from './api'

// The same names as FLAGS in backend/app/features.py. The server decides which are on:
// every flag in development, none on the live site until FEATURE_<NAME>=true is set there.
export type FeatureName = 'agent_form_filling' | 'supporting_documents' | 'embassy_pdf' | 'passport_scan_quality'
  | 'privacy_consent' | 'email_notifications' | 'visa_guide'
export type FeatureFlags = Partial<Record<FeatureName, boolean>>

const HIDDEN: FeatureFlags = {}
let loading: Promise<FeatureFlags> | null = null
let loaded: FeatureFlags | null = null

// One request per page load, shared by every component that asks.
export function loadFeatureFlags(): Promise<FeatureFlags> {
  loading ??= apiFetch('/features', { silent: true })
    .then(async response => {
      if (!response.ok) throw new Error('Feature flags are unavailable.')
      loaded = await response.json() as FeatureFlags
      return loaded
    })
    .catch(() => {
      // Unreadable flags hide flagged work rather than show it; the next caller asks again.
      loading = null
      return HIDDEN
    })
  return loading
}

// undefined while the flags load, then true only if the server said so.
export function useFeature(name: FeatureName): boolean | undefined {
  const [flags, setFlags] = useState<FeatureFlags | null>(loaded)
  useEffect(() => {
    if (flags) return
    let active = true
    loadFeatureFlags().then(result => { if (active) setFlags(result) })
    return () => { active = false }
  }, [flags])
  return flags ? flags[name] === true : undefined
}

// Tests start each case as a fresh page load.
export function resetFeatureFlags() {
  loading = null
  loaded = null
}
