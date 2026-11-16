import { protectedFetch } from '../auth'

export type MfaStatus = { enabled: boolean; backup_codes_remaining: number }
export type Enrollment = { secret: string; provisioning_uri: string; qr_data_url: string; expires_in: number }

async function request<T>(path = '', body?: object): Promise<T> {
  const response = await protectedFetch(`/auth/mfa${path}`, {
    method: body ? 'POST' : 'GET', cache: 'no-store', silent: true,
    ...(body ? { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) } : {}),
  })
  if (response.status === 403) throw new Error('Check your password, Google identity or current security code. Codes can only be used once.')
  if (response.status === 409) throw new Error('Security settings or your account changed, or setup expired. Refresh and start again.')
  if (response.status === 429) throw new Error('Too many attempts. Please wait 15 minutes before trying again.')
  if (!response.ok) throw new Error('Could not update two-factor sign-in. Please try again.')
  return response.json() as Promise<T>
}

export const mfaStatus = () => request<MfaStatus>()
export const setupMfa = (body: { expected_traveler_id: number; password?: string; google_id_token?: string }) => request<Enrollment>('/setup', body)
export const enableMfa = (expected_traveler_id: number, code: string) =>
  request<{ enabled: boolean; backup_codes: string[] }>('/enable', { expected_traveler_id, code })
export const disableMfa = (expected_traveler_id: number, code: string) =>
  request<{ enabled: boolean }>('/disable', { expected_traveler_id, code })
export const replaceBackupCodes = (expected_traveler_id: number, code: string) =>
  request<{ backup_codes: string[] }>('/backup-codes', { expected_traveler_id, code })
