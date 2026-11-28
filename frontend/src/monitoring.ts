import { browserTracingIntegration, captureMessage, init } from '@sentry/browser'

// A strict allowlist keeps passport data, URLs, tokens and form inputs out of Sentry. Traces (off
// unless VITE_SENTRY_TRACES_SAMPLE_RATE is above 0) keep route templates, timings and Web Vitals only.
type Payload = Record<string, unknown>

const TRACE_KEYS = ['trace_id', 'span_id', 'parent_span_id', 'op', 'status', 'origin']
const SPAN_KEYS = ['span_id', 'parent_span_id', 'trace_id', 'op', 'start_timestamp', 'timestamp', 'status', 'origin', 'measurements']
// Upload, application, user and session IDs: long hex, numbers and UUIDs.
const ID_SEGMENT = /\/(?:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|[0-9a-f]{16,}|[0-9]+)(?=\/|$)/gi

function pick(source: unknown, keys: string[]): Payload {
  if (!source || typeof source !== 'object') return {}
  const record = source as Payload
  return Object.fromEntries(keys.filter(key => key in record).map(key => [key, record[key]]))
}

/** "/dashboard/3f9a…?tab=x#y" becomes "/dashboard/:id": no IDs, queries or fragments. */
export function routeTemplate(path: string): string {
  const bare = path.replace(/^[a-z]+:\/\/[^/]+/i, '').split(/[?#]/)[0]
  return bare.replace(ID_SEGMENT, '/:id') || '/'
}

/** Span descriptions such as "GET https://site/applications/abc?x=1": origins, queries and IDs removed. */
export function safeDescription(description: string): string {
  return description.replace(/[a-z]+:\/\/[^/\s]+/gi, '').replace(/[?#]\S*/g, '')
    .replace(ID_SEGMENT, '/:id').slice(0, 200)
}

function currentRoute(): string {
  return typeof window === 'undefined' ? '/' : routeTemplate(window.location.pathname)
}

export function scrubBrowserEvent(event: Record<string, unknown>) {
  const trace = pick((event.contexts as Payload | undefined)?.trace, ['trace_id', 'span_id'])
  return { ...(typeof event.event_id === 'string' ? { event_id: event.event_id } : {}),
    ...pick(event, ['timestamp', 'environment', 'release']),
    ...(trace.trace_id ? { contexts: { trace } } : {}),
    type: undefined, platform: 'javascript', level: 'error' as const, message: 'Taasheera page error',
    tags: { component: 'frontend', route: currentRoute() } }
}

export function scrubBrowserTransaction(event: Record<string, unknown>) {
  const spans = Array.isArray(event.spans) ? event.spans : []
  return { ...pick(event, ['event_id', 'type', 'timestamp', 'start_timestamp', 'platform', 'environment', 'release', 'measurements']),
    transaction: routeTemplate(String(event.transaction ?? '/')), transaction_info: { source: 'route' },
    contexts: { trace: pick((event.contexts as Payload | undefined)?.trace, TRACE_KEYS) },
    tags: { component: 'frontend' },
    spans: spans.map(span => {
      const kept = pick(span, SPAN_KEYS)
      const description = (span as Payload).description
      return typeof description === 'string' ? { ...kept, description: safeDescription(description) } : kept
    }) }
}

/** Standalone spans (Interaction to Next Paint): timings and Sentry's own keys only. */
export function scrubBrowserSpan<T extends Record<string, unknown>>(span: T): T {
  const data = (span.data ?? {}) as Payload
  const safeData = Object.fromEntries(Object.entries(data).filter(([key]) => key.startsWith('sentry.') && key !== 'sentry.replay_id'))
  if (typeof data.transaction === 'string') safeData.transaction = routeTemplate(data.transaction)
  const kept = pick(span, SPAN_KEYS)
  return { ...kept, data: safeData,
    ...(typeof span.description === 'string' ? { description: safeDescription(span.description) } : {}) } as unknown as T
}

function sampleRate(): number {
  const rate = Number(import.meta.env?.VITE_SENTRY_TRACES_SAMPLE_RATE ?? 0)
  return Number.isFinite(rate) && rate > 0 && rate <= 1 ? rate : 0
}

export function configureBrowserMonitoring() {
  const dsn = import.meta.env?.VITE_SENTRY_DSN?.trim()
  if (!dsn) return
  const rate = sampleRate()
  init({ dsn, sendDefaultPii: false, defaultIntegrations: false,
    environment: import.meta.env?.VITE_SENTRY_ENVIRONMENT?.trim() || 'production',
    release: import.meta.env?.VITE_SENTRY_RELEASE?.trim() || undefined,
    // Page loads, navigations and Web Vitals; the API continues the same trace (same origin only).
    integrations: rate > 0 ? [browserTracingIntegration()] : [],
    tracesSampleRate: rate, tracePropagationTargets: [/^\/(?!\/)/], maxBreadcrumbs: 0,
    beforeSend: event => scrubBrowserEvent(event as unknown as Record<string, unknown>) as never,
    beforeSendTransaction: event => scrubBrowserTransaction(event as unknown as Record<string, unknown>) as never,
    beforeSendSpan: span => scrubBrowserSpan(span as unknown as Record<string, unknown>) as never })
  window.addEventListener('error', reportPageError)
  window.addEventListener('unhandledrejection', reportPageError)
}

export function reportPageError() {
  if (import.meta.env?.VITE_SENTRY_DSN) captureMessage('Taasheera page error', 'error')
}
