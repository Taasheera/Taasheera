import { protectedFetch } from '../auth'

export type GuideLink = { label: string; url: string }
export type GuideField = { label: string; value: string | null; kind: 'text' | 'country' | 'address'; source: string | null; note: string | null }
export type PortalGuideData = {
  title: string
  portal: GuideLink
  before: string[]
  sections: { title: string; intro: string | null; fields: GuideField[]; yourself: string[] }[]
  after: string[]
  sources: GuideLink[]
  filled: number
  total: number
}

export async function getPortalGuide(applicationId: string): Promise<PortalGuideData | null> {
  const response = await protectedFetch(`/applications/${applicationId}/portal-guide`, { cache: 'no-store', silent: true })
  if (response.status === 404) return null
  if (!response.ok) throw new Error('Could not load the form guide.')
  return await response.json() as PortalGuideData
}

