import { useEffect } from 'react'
import { Link, useOutletContext } from 'react-router'
import type { LayoutContext } from '../Layout'
import { destinations } from '../content'
import { useText } from '../i18n/language'
import { ArrowIcon, CheckIcon } from '../icons'
import Feature from '../Feature'
import { useHelpEntries } from '../help/helpContent'
import { createAccountPath } from '../paths'
import FaqList from './FaqList'

// Signed in, the main buttons start an application (US 4); signed out, they create an account.
function MainButton() {
  const { home } = useText()
  const traveler = useOutletContext<LayoutContext | undefined>()?.traveler
  return traveler
    ? <Link to="/apply" className="pill-button pill-primary" data-activity-id="start-application-link">
        <span className="pill-label">{home.startApplication}</span>
        <span className="pill-icon"><ArrowIcon /></span>
      </Link>
    : <Link to={createAccountPath} reloadDocument className="pill-button pill-primary landing-start" data-activity-id="get-started-link">
        <span className="visually-hidden">{home.getStarted}</span>
        <span className="pill-label" aria-hidden="true" data-label={home.startYours} />
        <span className="pill-icon"><ArrowIcon /></span>
      </Link>
}

export default function Home() {
  const text = useText()
  const { home } = text
  const faqs = useHelpEntries()
  useEffect(() => {
    document.title = home.title
  }, [home.title])

  return (
    <>
      <section className="hero" aria-labelledby="hero-heading">
        <div className="container hero-inner">
          <p className="eyebrow status-dot status-dot--ok">{home.places}</p>
          <h1 id="hero-heading" className="hero-title" aria-label={home.heroLabel}>
            {home.heroLine1}<br className="hero-break" /> {home.heroLine2}
            <span className="visually-hidden"> {home.heroLabel}</span>
          </h1>
          <p className="hero-sub">{home.heroSub}</p>
          <div className="hero-buttons">
            <MainButton />
            <a href="#how-it-works" className="pill-button pill-secondary" data-activity-id="how-it-works-link">
              <span className="pill-label">{home.seeHow}</span>
            </a>
          </div>
          <div className="hero-preview" role="group" aria-label={home.preview}>
            <div className="hero-preview-grid">
              <div className="preview-pane">
                <div className="preview-top"><span className="eyebrow">{home.previewPassport}</span>
                  <span className="status-dot status-dot--ok">{home.checkDigits}</span>
                </div>
                <dl className="preview-fields">
                  <dt>{home.surname}</dt><dd lang="en" dir="ltr">ERIKSSON</dd>
                  <dt>{home.given}</dt><dd lang="en" dir="ltr">ANNA MARIA</dd>
                  <dt>{home.number}</dt><dd lang="en" dir="ltr">L898902C3</dd>
                  <dt>{home.born}</dt><dd lang="en" dir="ltr">12 AUG 1974</dd>
                </dl>
                <p className="preview-mrz" lang="en" dir="ltr">P&lt;UTOERIKSSON&lt;&lt;ANNA&lt;MARIA&lt;&lt;&lt;&lt;&lt;&lt;&lt;&lt;</p>
              </div>
              <div className="preview-pane">
                <span className="eyebrow">{home.previewQuestion}</span>
                <p className="preview-question">{home.stayQuestion}</p>
                <span className="preview-choice is-selected">{home.hotel}</span>
                <span className="preview-choice">{home.host}</span>
              </div>
              <div className="preview-pane preview-check">
                <span className="eyebrow">{home.previewCheck}</span>
                <p className="preview-count"><strong>35</strong> {home.boxesMatch}</p>
                <ul>
                  <li className="status-dot status-dot--warn">{home.checkoutLate}</li>
                  <li className="status-dot status-dot--danger">{home.noInsurance}</li>
                </ul>
              </div>
            </div>
            <p className="preview-caption">{home.previewCaption}</p>
          </div>
        </div>
      </section>

      <section id="how-it-works" className="section" aria-labelledby="how-heading">
        <div className="container">
          <header className="section-header">
            <h2 id="how-heading">{home.stepsHeading}</h2>
          </header>
          <ol className="steps">
            {text.steps.map((step, index) => (
              <li key={step.title} className="card">
                <span className="step-number" aria-hidden="true">{String(index + 1).padStart(2, '0')}</span>
                <h3>{step.title}</h3>
                <p>{step.body}</p>
              </li>
            ))}
          </ol>
        </div>
      </section>

      <section id="features" className="section" aria-labelledby="what-heading">
        <div className="container">
          <header className="section-header">
            <h2 id="what-heading">{home.featuresHeading}</h2>
          </header>
          <div className="feature-grid">
            <article className="card feature-card feature-passport">
              <div className="feature-mrz" lang="en" dir="ltr" aria-hidden="true">
                P&lt;UTOERIKSSON&lt;&lt;ANNA&lt;MARIA&lt;&lt;&lt;&lt;<br />
                L898902C<mark>3</mark>6UTO7408122F1204159
              </div>
              <div><h3>{home.passportTitle}</h3>
                <p>{home.passportBody}</p></div>
            </article>
            <article className="card feature-card">
              <div className="feature-question"><strong>22</strong> {home.questionsCount}</div>
              <div><h3>{home.onlyTitle}</h3>
                <p>{home.onlyBody}</p></div>
            </article>
            <article className="card feature-card">
              <ul className="feature-checks" aria-label={home.checksLabel}>
                <li className="status-dot status-dot--ok">{home.nameMatches}</li>
                <li className="status-dot status-dot--warn">{home.bookingEnds}</li>
                <li className="status-dot status-dot--danger">{home.insuranceMissing}</li>
              </ul>
              <div><h3>{home.crossTitle}</h3>
                <p>{home.crossBody}</p></div>
            </article>
            <article className="card feature-card">
              <div className="feature-chat" aria-label={home.chatLabel}>
                <p className="bubble bubble-user">{home.chatQuestion}</p>
                <p className="bubble bubble-assistant">{home.chatAnswer}</p>
              </div>
              <div><h3>{home.assistantTitle}</h3>
                <p>{home.assistantBody}</p></div>
            </article>
            <div id="documents" className="card list-card feature-card">
              <div><h3>{home.readyTitle}</h3>
                <p className="list-note">{home.readyNote}</p></div>
              <ul className="check-list">
                {text.requiredDocuments.map(item => <li key={item}><CheckIcon />{item}</li>)}
              </ul>
            </div>
          </div>
        </div>
      </section>

      <section id="destinations" className="section" aria-labelledby="destinations-heading">
        <div className="container">
          <header className="section-header">
            <p className="badge">{home.destinationsBadge}</p>
            <h2 id="destinations-heading">{home.destinationsHeading}</h2>
          </header>
          <ul className="destinations">
            {destinations.map(item => (
              <li key={item.id} className="card destination">
                <img src={item.flag} alt="" width="40" height="30" />
                <div>
                  <h3>{text.destinations[item.id].name}</h3>
                  <p>{text.destinations[item.id].visa}</p>
                  <Feature name="visa_guide">
                    <p><Link to={`/visas/${item.guide}`} className="destination-guide" data-activity-id={`visa-guide-${item.guide}`}>
                      {home.readGuide}<span className="visually-hidden">{home.guideFor(text.destinations[item.id].name)}</span></Link></p>
                  </Feature>
                </div>
              </li>
            ))}
          </ul>
        </div>
      </section>

      <section id="privacy" className="section section-dark" aria-labelledby="privacy-heading">
        <div className="container">
          <header className="section-header">
            <h2 id="privacy-heading">{home.privacyHeading}</h2>
          </header>
          <ul className="privacy">
            {text.privacyPoints.map(point => (
              <li key={point.title} className="card">
                <h3>{point.title}</h3>
                <p>{point.body}</p>
              </li>
            ))}
          </ul>
        </div>
      </section>

      <section className="section" aria-labelledby="faq-heading">
        <div className="container narrow">
          <header className="section-header">
            <p className="badge">{home.faqBadge}</p>
            <h2 id="faq-heading">{home.faqHeading}</h2>
          </header>
          <FaqList items={faqs.slice(0, 5)} />
          <p className="more-link"><Link to="/faq" data-activity-id="faq-link">{home.allQuestions}</Link></p>
        </div>
      </section>

      <section className="section" aria-labelledby="cta-heading">
        <div className="container final-cta">
          <h2 id="cta-heading">{home.nextHeading}</h2>
          <MainButton />
        </div>
      </section>
    </>
  )
}
