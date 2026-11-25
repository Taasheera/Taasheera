import { useEffect, useState } from 'react'
import { Link } from 'react-router'
import { useFeature } from '../features'
import { checkedDate, loadGuides, type VisaGuide } from './api'
import './visaGuides.css'

// Every supported visa, before signing in or starting an application.
export default function VisaGuides() {
  const pdfAvailable = useFeature('embassy_pdf')
  const [guides, setGuides] = useState<VisaGuide[] | null>(null)
  const [error, setError] = useState('')

  useEffect(() => {
    document.title = 'Visa guides | Taasheera'
    let active = true
    loadGuides().then(loaded => { if (active) setGuides(loaded) },
      problem => { if (active) setError(problem.message) })
    return () => { active = false }
  }, [])

  return (
    <section className="section page" aria-labelledby="visa-guides-heading">
      <div className="container narrow">
        <header className="section-header">
          <p className="badge">Visa guides</p>
          <h1 id="visa-guides-heading">What each visa needs</h1>
          <p>Who needs it, which documents to prepare, what it costs and how long it takes, before you start.</p>
        </header>
        {error && <p className="guide-error" role="alert">{error}</p>}
        {!guides && !error && <p role="status">Loading the guides…</p>}
        {guides && <ul className="guide-list">
          {guides.map(guide => (
            <li key={guide.id} className="card guide-card">
              <h2><Link to={`/visas/${guide.id}`} data-activity-id={`visa-guide-${guide.id}`}>{guide.title}</Link></h2>
              <p>{guide.destination_label}: {guide.destination_description}</p>
              <p className="guide-card-facts">
                <span>From {guide.fees.items[0].amount}</span>
                <span>{guide.coverage.level === 'filled_for_you' && pdfAvailable ? 'PDF form available' : 'Guided, you submit'}</span>
              </p>
            </li>
          ))}
        </ul>}
        {guides && guides.length > 0 && <p className="guide-checked">
          Fees and rules checked on official government sites on {checkedDate(guides[0].checked_on)}.
        </p>}
      </div>
    </section>
  )
}
