import { protectedFetch } from '../auth'

export type QuoteLine = { kind: 'service_fee' | 'embassy_fee' | 'discount'; label: string; amount_minor: number }
export type Quote = {
  currency: string
  lines: QuoteLine[]
  total_minor: number
  paid_at_embassy: string | null
  fee_source: { label: string; url: string }
  checked_on: string
}
export type Payment = {
  id: string
  receipt_number: string
  status: 'pending' | 'paid' | 'failed'
  amount_minor: number
  currency: string
  line_items: QuoteLine[]
  provider: string
  created_at: string
  paid_at: string | null
}
export type PaymentState = {
  quote: Quote
  simulated: boolean
  available: boolean
  can_pay: boolean
  reason: string | null
  payments: Payment[]
}

async function detail(response: Response, fallback: string) {
  const problem = await response.json().catch(() => null) as { detail?: unknown } | null
  return typeof problem?.detail === 'string' ? problem.detail : fallback
}

const base = (applicationId: string) => `/applications/${applicationId}/payments`

export function formatMoney(amountMinor: number, currency: string): string {
  const format = new Intl.NumberFormat('en', { style: 'currency', currency, currencyDisplay: 'code' })
  return format.format(amountMinor / 10 ** (format.resolvedOptions().maximumFractionDigits ?? 2))
}

export async function getPaymentState(applicationId: string): Promise<PaymentState> {
  const response = await protectedFetch(base(applicationId), { cache: 'no-store' })
  if (!response.ok) throw new Error('Could not load the price for this application.')
  return response.json()
}

export async function pay(applicationId: string, idempotencyKey: string): Promise<Payment> {
  const response = await protectedFetch(base(applicationId), {
    method: 'POST', cache: 'no-store', silent: true,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ idempotency_key: idempotencyKey }),
  })
  if (!response.ok) throw new Error(await detail(response, 'The payment didn’t go through. Please try again.'))
  return response.json()
}

export async function downloadReceipt(applicationId: string, paymentId: string): Promise<{ file: Blob; filename: string }> {
  const response = await protectedFetch(`${base(applicationId)}/${paymentId}/receipt`, { cache: 'no-store', silent: true })
  if (!response.ok) throw new Error(await detail(response, 'Could not download the receipt.'))
  const named = /filename="([^"]+)"/.exec(response.headers.get('Content-Disposition') ?? '')
  return { file: await response.blob(), filename: named?.[1] ?? 'taasheera-receipt.pdf' }
}
