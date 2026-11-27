import { useCallback, useEffect, useRef, useState } from 'react'
import { toast } from 'sonner'
import { formatDateTime } from '../dashboard/helpers'
import { downloadReceipt, formatMoney, getPaymentState, pay, type Payment, type PaymentState, type QuoteLine } from './api'
import './payments.css'

export default function PaymentStep({ applicationId, version, onPaid }:
  { applicationId: string; version?: string; onPaid?: () => void }) {
  const [state, setState] = useState<PaymentState | Error | null>(null)
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState('')
  const checkout = useRef<string | null>(null)
  const paying = useRef(false)

  const load = useCallback(() => getPaymentState(applicationId)
    .then(setState)
    .catch(failure => setState(failure instanceof Error ? failure : new Error('Could not load the price for this application.'))), [applicationId])

  useEffect(() => { void load() }, [load, version])

  async function payNow() {
    if (paying.current) return
    paying.current = true
    setBusy(true)
    setProblem('')
    checkout.current ??= crypto.randomUUID()
    try {
      const payment = await pay(applicationId, checkout.current)
      if (payment.status !== 'paid') throw new Error('The payment didn’t go through and nothing was charged. Please try again.')
      toast.success('Payment recorded. The agent can now fill the embassy form.')
      onPaid?.()
      await load()
    } catch (failure) {
      checkout.current = null
      setProblem(failure instanceof Error ? failure.message : 'Something went wrong. Please try again.')
    } finally {
      paying.current = false
      setBusy(false)
    }
  }

  if (state === null) return null
  if (state instanceof Error) return <section className="payment" aria-labelledby="payment-title">
    <h2 id="payment-title">Payment</h2>
    <p className="payment-error" role="alert">{state.message}</p>
  </section>

  const paid = state.payments[0]
  if (!state.available && !paid) return null
  const { quote } = state

  return <section className="payment" aria-labelledby="payment-title">
    <div className="payment-head">
      <h2 id="payment-title">Payment</h2>
      {state.simulated && <span className="payment-badge">Test mode</span>}
    </div>
    <p className="payment-lede">
      Answering, checks, documents and your print-ready packet are free. Pay when you’re ready for the agent to fill the embassy form.
    </p>
    {state.simulated && <p className="payment-simulated">
      This is a simulated payment: no card is needed, no card details are asked for and nothing is charged.
    </p>}
    {paid
      ? <Breakdown lines={paid.line_items} currency={paid.currency} total={paid.amount_minor} totalLabel="Total paid" />
      : <Breakdown lines={quote.lines} currency={quote.currency} total={quote.total_minor} totalLabel="Total" />}
    {!paid && quote.paid_at_embassy && <p className="payment-note">
      Not included: the embassy fee ({quote.paid_at_embassy}), which you pay at the embassy.
    </p>}
    <p className="payment-note">
      Embassy fee from <a href={quote.fee_source.url} target="_blank" rel="noreferrer">{quote.fee_source.label}</a>, checked {quote.checked_on}.
    </p>
    {paid
      ? <Paid applicationId={applicationId} payment={paid} />
      : <div className="payment-actions">
          <button type="button" className="button button-primary dash-small" disabled={busy || !state.can_pay}
            aria-busy={busy} aria-describedby={state.reason ? 'payment-reason' : undefined}
            onClick={() => void payNow()} data-activity-id="payment-pay">
            {busy ? 'Paying…' : `Pay ${formatMoney(quote.total_minor, quote.currency)}`}
          </button>
          {!state.can_pay && state.reason && <p id="payment-reason" className="payment-note">{state.reason}</p>}
        </div>}
    {problem && <p className="payment-error" role="alert">{problem}</p>}
  </section>
}

function Breakdown({ lines, currency, total, totalLabel }:
  { lines: QuoteLine[]; currency: string; total: number; totalLabel: string }) {
  return <table className="payment-breakdown">
    <caption>Price breakdown ({currency})</caption>
    <tbody>
      {lines.map((line, index) => <tr key={`${line.kind}-${index}`} className={`payment-${line.kind}`}>
        <th scope="row">{line.label}</th>
        <td>{formatMoney(line.amount_minor, currency)}</td>
      </tr>)}
      {!lines.some(line => line.kind === 'discount') && <tr className="payment-discount">
        <th scope="row">Discount</th>
        <td>None</td>
      </tr>}
    </tbody>
    <tfoot>
      <tr>
        <th scope="row">{totalLabel}</th>
        <td>{formatMoney(total, currency)}</td>
      </tr>
    </tfoot>
  </table>
}

function Paid({ applicationId, payment }: { applicationId: string; payment: Payment }) {
  const [busy, setBusy] = useState(false)

  async function save() {
    setBusy(true)
    try {
      const { file, filename } = await downloadReceipt(applicationId, payment.id)
      const url = URL.createObjectURL(file)
      const link = document.createElement('a')
      link.href = url
      link.download = filename
      document.body.append(link)
      link.click()
      link.remove()
      setTimeout(() => URL.revokeObjectURL(url), 1000)
      toast.success(`Downloaded ${filename}.`)
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Something went wrong. Please try again.')
    } finally {
      setBusy(false)
    }
  }

  return <div className="payment-paid" role="status">
    <p>
      <strong>Paid {formatMoney(payment.amount_minor, payment.currency)}</strong>
      {payment.paid_at && <> on {formatDateTime(payment.paid_at)}</>} · Receipt {payment.receipt_number}
    </p>
    <button type="button" className="button button-secondary dash-small" disabled={busy} aria-busy={busy}
      onClick={() => void save()} data-activity-id="payment-receipt">{busy ? 'Preparing…' : 'Download receipt'}</button>
  </div>
}
