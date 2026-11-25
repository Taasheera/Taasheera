import { useEffect, useState } from 'react'
import { Link, useOutletContext, useParams } from 'react-router'
import type { LayoutContext } from '../Layout'
import { useFeature } from '../features'
import { countries } from '../countries'
import { ArrowIcon } from '../icons'
import { createAccountPath } from '../paths'
import NotFound from '../pages/NotFound'
import { checkedDate, GuideError, loadGuide, type Link as SourceLink, type VisaGuide } from './api'
import './visaGuides.css'

function External({ link }: { link: SourceLink }) {
  return <a href={link.url} target="_blank" rel="noopener noreferrer">{link.label}<span className="visually-hidden"> (opens in a new tab)</span></a>
}

// Show the general rule for checked nationalities and the official checker for every nationality.
function NeedVisa({ guide }: { guide: VisaGuide }) {
  const [nationality, setNationality] = useState('')
  const country = countries.find(item => item.code === nationality)
  const answer = nationality ? guide.need.checked_nationalities[nationality] : undefined

  return (
    <section className="guide-section" aria-labelledby="need-heading">
      <h2 id="need-heading">Do I need this visa?</h2>
      <p>{guide.need.rule}</p>
      {guide.need.notes.map(note => <p key={note}>{note}</p>)}
      <label className="guide-field">
        <span>Your nationality, as on your passport</span>
        <select value={nationality} onChange={event => setNationality(event.target.value)}
          data-activity-id="visa-guide-nationality">
          <option value="">Choose your nationality</option>
          {countries.map(item => <option key={item.code} value={item.code}>{item.name}</option>)}
        </select>
      </label>
      {country && <div className="guide-answer" role="status">
        {answer === 'visa' && <p>Travelers using a passport from {country.name} generally need a visa for this type of visit.</p>}
        {answer === 'no_visa' && <p>Travelers using a passport from {country.name} can usually make short visits without a visa.</p>}
        {answer === undefined && <p>We haven't checked the rules for citizens of {country.name} ourselves yet.</p>}
        <p>{answer ? 'Confirm it' : 'Find out'} with the official checker: <External link={guide.need.checker} />.</p>
      </div>}
    </section>
  )
}

function StartButton() {
  const traveler = useOutletContext<LayoutContext | undefined>()?.traveler
  const label = traveler ? 'Start an application' : 'Create a free account'
  const content = <><span className="pill-icon"><ArrowIcon /></span><span className="pill-label">{label}</span></>
  return traveler
    ? <Link to="/apply" className="pill-button pill-primary guide-start" data-activity-id="start-application-link">{content}</Link>
    : <Link to={createAccountPath} reloadDocument className="pill-button pill-primary guide-start" data-activity-id="get-started-link">{content}</Link>
}

export default function VisaGuidePage() {
  const { guideId = '' } = useParams()
  return <GuideDetail key={guideId} guideId={guideId} />
}

function GuideDetail({ guideId }: { guideId: string }) {
  const pdfAvailable = useFeature('embassy_pdf')
  const [guide, setGuide] = useState<VisaGuide | null>(null)
  const [error, setError] = useState<GuideError | null>(null)

  useEffect(() => {
    let active = true
    loadGuide(guideId).then(loaded => {
      if (!active) return
      setGuide(loaded)
      document.title = `${loaded.title} | Taasheera`
    }, problem => { if (active) setError(problem) })
    return () => { active = false }
  }, [guideId])

  if (error?.notFound) return <NotFound />
  return (
    <section className="section page" aria-labelledby="guide-heading">
      <div className="container narrow">
        <p><Link to="/visas" data-activity-id="visa-guides-link">← All visa guides</Link></p>
        {error && <p className="guide-error" role="alert">{error.message}</p>}
        {!guide && !error && <p role="status">Loading the guide…</p>}
        {guide && <>
          <header className="section-header guide-header">
            <p className="badge">{guide.destination_label}</p>
            <h1 id="guide-heading">{guide.title}</h1>
            <p>{guide.visa_description}</p>
          </header>

          <NeedVisa guide={guide} />

          {guide.not_for.length > 0 && <section className="guide-section" aria-labelledby="not-for-heading">
            <h2 id="not-for-heading">When this visa is not the right one</h2>
            <ul>{guide.not_for.map(reason => <li key={reason}>{reason}</li>)}</ul>
          </section>}

          <section className="guide-section" aria-labelledby="documents-heading">
            <h2 id="documents-heading">Documents to prepare</h2>
            <p>Besides your passport. Some depend on your answers, so the application shows you exactly which ones apply.</p>
            <ul className="guide-documents">
              {guide.documents.map(document => (
                <li key={document.label}>
                  <strong>{document.label}</strong>{!document.required && <span className="guide-optional"> (optional)</span>}
                  {document.help && <span className="guide-help">{document.help}</span>}
                  {document.only_if && <span className="guide-only-if">{document.only_if}</span>}
                </li>
              ))}
            </ul>
          </section>

          <section className="guide-section" aria-labelledby="fees-heading">
            <h2 id="fees-heading">How much it costs</h2>
            <table className="guide-fees">
              <caption className="visually-hidden">Official fees</caption>
              <tbody>
                {guide.fees.items.map(fee => <tr key={fee.label}><th scope="row">{fee.label}</th><td>{fee.amount}</td></tr>)}
              </tbody>
            </table>
            {guide.fees.note && <p>{guide.fees.note}</p>}
            <p className="guide-source">Source: <External link={guide.fees.source} /></p>
          </section>

          <section className="guide-section" aria-labelledby="time-heading">
            <h2 id="time-heading">How long it takes</h2>
            <p>{guide.time.text}</p>
            <p className="guide-source">Source: <External link={guide.time.source} /></p>
          </section>

          <section className="guide-section" aria-labelledby="coverage-heading">
            <h2 id="coverage-heading">What Ta'asheera does for you</h2>
            <p><span className="guide-level">{guide.coverage.level === 'filled_for_you' && pdfAvailable ? 'PDF form available' : 'Form guide, you submit'}</span></p>
            <p>{guide.coverage.text}</p>
            <StartButton />
          </section>

          <section className="guide-section" aria-labelledby="sources-heading">
            <h2 id="sources-heading">Official sources</h2>
            <ul>{guide.sources.map(link => <li key={link.url}><External link={link} /></li>)}</ul>
            <p className="guide-checked">
              Checked on {checkedDate(guide.checked_on)}. Rules and fees change, so always confirm on the official
              site before you pay or travel. This is general information, not legal or immigration advice.
            </p>
          </section>
        </>}
      </div>
    </section>
  )
}
