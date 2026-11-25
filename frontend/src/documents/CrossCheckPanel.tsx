import { useEffect, useState } from 'react'
import { getCrossCheck, type CrossCheck } from './api'

const FIELD_LABELS = { name: 'Name', birth_date: 'Date of birth', document_number: 'Passport number', travel_dates: 'Travel dates' } as const

// Where the traveler's name, date of birth or passport number disagrees with their passport (CMPS271-50).
// The server reads the documents and decides; `version` changes whenever a file or answer changes.
export default function CrossCheckPanel({ applicationId, version }: { applicationId: string; version: string }) {
  const [report, setReport] = useState<CrossCheck | Error | null>(null)

  useEffect(() => {
    let active = true
    getCrossCheck(applicationId)
      .then(result => { if (active) setReport(result) })
      .catch(failure => { if (active) setReport(failure instanceof Error ? failure : new Error('Could not cross-check your documents.')) })
    return () => { active = false }
  }, [applicationId, version])

  if (report === null) return <div className="xcheck" role="status"><p className="docs-note">Cross-checking your documents…</p></div>
  if (report instanceof Error) return <div className="xcheck" role="alert"><p className="docs-error">{report.message}</p></div>
  if (report.reference === null) return <div className="xcheck">
    <h3>Consistency check</h3>
    <p className="docs-note">Add your passport to the application, and we'll check your documents against it.</p>
  </div>

  const ok = report.findings.length === 0
  return <div className={`xcheck${ok ? ' is-ok' : ' is-warn'}`} aria-labelledby="xcheck-title">
    <h3 id="xcheck-title">Consistency check</h3>
    <p className={`xcheck-summary status-dot status-dot--${ok ? 'ok' : 'warn'}`} role="status">
      {ok
        ? `Your name, date of birth and passport number match across ${report.checked.length === 0 ? 'your passport' : `${report.checked.length} ${report.checked.length === 1 ? 'source' : 'sources'}`}.`
        : `${report.findings.length} ${report.findings.length === 1 ? 'thing doesn\'t' : 'things don\'t'} match your passport. Embassies expect every document to match it exactly.`}
    </p>
    {!ok && <ul className="xcheck-list">
      {report.findings.map((finding, index) => <li key={`${finding.where}-${finding.field}-${index}`}>
        <span className="xcheck-field">{FIELD_LABELS[finding.field]}</span>
        <span className="xcheck-message">{finding.message}</span>
      </li>)}
    </ul>}
    {report.unreadable.length > 0 && <p className="docs-note xcheck-unreadable">
      We couldn't read the text of {report.unreadable.join(', ')}, so {report.unreadable.length === 1 ? 'it wasn\'t' : 'they weren\'t'} checked.
      Check {report.unreadable.length === 1 ? 'it' : 'them'} yourself, or upload a clearer scan.
    </p>}
    <p className="docs-note xcheck-disclaimer">An automatic check of the text we could read. It can miss things; it isn't legal or immigration advice.</p>
  </div>
}
