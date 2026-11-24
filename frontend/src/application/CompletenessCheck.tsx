import { useEffect, useRef, useState } from 'react'
import { checkApplication, type CheckReport } from './api'

// The completeness check before submission (US 13). The server decides what is missing or
// inconsistent; the links bring the traveler back to the relevant question or document.
// The page keys it by the application's last update, so an old report never sits next to new answers.
// `documents` changes after each document upload or deletion: a report already on screen is re-run.
// With `uploadLinks`, each missing document links to its row in the checklist on the same page.
export default function CompletenessCheck({ applicationId, documents = 0, uploadLinks = false, fixQuestion, fixDocument }:
  { applicationId: string; documents?: number; uploadLinks?: boolean; fixQuestion?: (id: string) => void;
    fixDocument?: (id: string) => void }) {
  const [report, setReport] = useState<CheckReport | null>(null)
  const [checking, setChecking] = useState(false)
  const [error, setError] = useState('')
  const shown = useRef(false)
  useEffect(() => { shown.current = report !== null }, [report])
  useEffect(() => {
    if (documents > 0 && shown.current) void run()
    // run is recreated each render; only a documents change should re-run the check.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [documents])

  async function run() {
    setChecking(true)
    setError('')
    try {
      setReport(await checkApplication(applicationId))
    } catch (failure) {
      setReport(null)
      setError(failure instanceof Error ? failure.message : 'Could not check your application.')
    } finally {
      setChecking(false)
    }
  }

  const blocking = report?.issues.filter(issue => issue.severity === 'blocking') ?? []
  const warnings = report?.issues.filter(issue => issue.severity === 'warning') ?? []
  return <section className="completeness" aria-labelledby="completeness-heading">
    <div className="completeness-head">
      <h2 id="completeness-heading">Check before you submit</h2>
      <button type="button" className="button button-secondary" onClick={run} disabled={checking}
        aria-busy={checking || undefined} data-activity-id="application-check">
        {checking ? 'Checking…' : report ? 'Check again' : 'Check my application'}
      </button>
    </div>
    {error && <p className="answer-error" role="alert">{error}</p>}
    {report && <div role="status">
      {report.readiness_score !== undefined && <p>
        <strong>{report.readiness_score}% complete</strong> · {report.readiness_reason}
      </p>}
      <p className={report.ready ? 'completeness-ok' : 'completeness-bad'}>
        {report.ready
          ? warnings.length ? 'Nothing is missing, but have a look at these first.' : 'Nothing is missing. Your answers are ready for review.'
          : `${blocking.length} ${blocking.length === 1 ? 'thing needs' : 'things need'} fixing before you can submit.`}
      </p>
      {report.issues.length > 0 && <ul className="completeness-issues">
        {report.issues.map((issue, index) => <li key={`${issue.question_id}-${index}`}
          className={`completeness-issue is-${issue.severity}`}>
          <span className="completeness-tag">{issue.severity === 'blocking' ? 'Must fix' : 'Check'}</span> {issue.message}
          {issue.question_id && fixQuestion && <>
            {' '}<button type="button" className="chat-explain" onClick={() => fixQuestion(issue.question_id!)}>Go to question</button></>}
          {issue.document_id && fixDocument && <>
            {' '}<button type="button" className="chat-explain" onClick={() => fixDocument(issue.document_id!)}>Go to document</button></>}
          {!issue.document_id && uploadLinks && issue.kind === 'missing' && issue.question_id === null && <>
            {' '}<a href="#docs-title" className="completeness-fix">Upload</a></>}
        </li>)}
      </ul>}
    </div>}
  </section>
}
