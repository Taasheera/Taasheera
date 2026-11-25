// Public visa guides (CMPS271-19, 20, 21, 22, 24, 25): no sign-in needed.
export type Link = { label: string; url: string }
export type VisaGuide = {
  id: string
  title: string
  destination_label: string
  destination_description: string
  visa_label: string
  visa_description: string
  official_page: Link
  need: { rule: string; notes: string[]; checker: Link; checked_nationalities: Record<string, 'visa' | 'no_visa'> }
  not_for: string[]
  documents: { label: string; help: string | null; required: boolean; only_if: string | null }[]
  fees: { items: { label: string; amount: string }[]; note: string; source: Link }
  time: { text: string; source: Link }
  coverage: { level: 'filled_for_you' | 'guided'; text: string }
  sources: Link[]
  checked_on: string
}

export class GuideError extends Error {
  readonly notFound: boolean
  constructor(message: string, notFound = false) {
    super(message)
    this.notFound = notFound
  }
}

async function read<T>(path: string): Promise<T> {
  let response: Response
  try {
    response = await fetch(path, { headers: { Accept: 'application/json' } })
  } catch {
    throw new GuideError('We could not load the visa guide. Check your connection and try again.')
  }
  if (response.status === 404) throw new GuideError("We don't have a guide for that visa.", true)
  if (!response.ok) throw new GuideError('We could not load the visa guide. Please try again.')
  return response.json()
}

export const loadGuides = () => read<VisaGuide[]>('/visa-guides')
export const loadGuide = (id: string) => read<VisaGuide>(`/visa-guides/${encodeURIComponent(id)}`)

// "2026-11-25" as "25 November 2026", whatever the browser's time zone.
export function checkedDate(iso: string): string {
  const [year, month, day] = iso.split('-').map(Number)
  return new Date(Date.UTC(year, month - 1, day)).toLocaleDateString('en-GB', {
    day: 'numeric', month: 'long', year: 'numeric', timeZone: 'UTC',
  })
}
