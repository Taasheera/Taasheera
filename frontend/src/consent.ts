import { protectedFetch } from './auth'

// Privacy policy acceptance (CMPS271-115). The server refuses passport and document uploads until the
// signed-in traveler accepts the current policy version, and records the version and time they did.
export type ConsentStatus = { policy_version: string; accepted: boolean; accepted_at: string | null }

// Fired when an upload was refused for this reason, so the policy prompt opens again.
export const CONSENT_REQUIRED_EVENT = 'taasheera-privacy-consent-required'

export async function loadConsent(): Promise<ConsentStatus> {
  const response = await protectedFetch('/profile/privacy-consent', { silent: true, cache: 'no-store' })
  if (!response.ok) throw new Error('Could not check the privacy policy.')
  return response.json()
}

export async function acceptPolicy(policyVersion: string): Promise<ConsentStatus> {
  const response = await protectedFetch('/profile/privacy-consent', {
    method: 'POST', silent: true, cache: 'no-store',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ policy_version: policyVersion }),
  })
  if (response.status === 409) throw new Error('The privacy policy has just changed. Reload the page to read the new version.')
  if (!response.ok) throw new Error('Could not save your answer. Check your connection and try again.')
  return response.json()
}

// True when the server refused an upload because the policy is not accepted yet; opens the prompt.
export function reportConsentRequired(response: Response): boolean {
  if (response.status !== 403 || response.headers.get('X-Privacy-Consent') !== 'required') return false
  window.dispatchEvent(new window.Event(CONSENT_REQUIRED_EVENT))
  return true
}
