// Official sites the assistant may cite (the server resolves citations to these). Anything else stays plain text.
export const OFFICIAL_SOURCES: Record<string, string> = {
  'home-affairs.ec.europa.eu': 'European Commission',
  'www.gov.uk': 'GOV.UK',
  'travel.state.gov': 'U.S. Department of State',
  'www.canada.ca': 'Government of Canada',
  'www.evisa.gov.tr': 'Republic of Türkiye e-Visa',
  'indianvisaonline.gov.in': 'Government of India',
  'www.visaforchina.cn': 'Chinese Visa Application Service Center',
  'www.mofa.go.jp': 'Ministry of Foreign Affairs of Japan',
  'immi.homeaffairs.gov.au': 'Australian Department of Home Affairs',
  'visa2egypt.gov.eg': 'Egypt e-Visa portal',
}

const URL_PATTERN = /https:\/\/[^\s<>\])]+/g

function official(raw: string): string | null {
  const url = raw.replace(/[.,;:]+$/, '')
  try {
    return OFFICIAL_SOURCES[new URL(url).hostname] ? url : null
  } catch {
    return null
  }
}

// Official links are listed once as sources under the reply instead of interrupting its sentences.
export function splitCitations(text: string): { body: string; sources: string[] } {
  const sources: string[] = []
  const body = text.replace(URL_PATTERN, raw => {
    const url = official(raw)
    if (!url) return raw
    if (!sources.includes(url)) sources.push(url)
    return raw.slice(url.length)
  }).replace(/[ \t]+([.,;:])/g, '$1').replace(/\(\s*\)/g, '').replace(/[ \t]{2,}/g, ' ').trim()
  return { body, sources }
}
