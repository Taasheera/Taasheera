import { useEffect } from 'react'
import { Link, useOutletContext } from 'react-router'
import type { LayoutContext } from '../Layout'
import { destinations, faqs, privacyPoints, requiredDocuments, steps } from '../content'
import { ArrowIcon, CheckIcon } from '../icons'
import Feature from '../Feature'
import { createAccountPath } from '../paths'
import FaqList from './FaqList'

// Signed in, the main buttons start an application (US 4); signed out, they create an account.
function MainButton() {
  const traveler = useOutletContext<LayoutContext | undefined>()?.traveler
  return traveler
    ? <Link to="/apply" className="pill-button pill-primary" data-activity-id="start-application-link">
        <span className="pill-label">Start an application</span>
        <span className="pill-icon"><ArrowIcon /></span>
      </Link>
    : <Link to={createAccountPath} reloadDocument className="pill-button pill-primary landing-start" data-activity-id="get-started-link">
        <span className="visually-hidden">Get started</span>
        <span className="pill-label" aria-hidden="true" />
        <span className="pill-icon"><ArrowIcon /></span>
      </Link>
}

export default function Home() {
  useEffect(() => {
    document.title = 'Taasheera | Your AI visa agent'
  }, [])

  return (
    <>
      <section className="hero" aria-labelledby="hero-heading">
        <div className="container hero-inner">
          <p className="eyebrow status-dot status-dot--ok">Schengen · UK · US · Canada</p>
          <h1 id="hero-heading" className="hero-title" aria-label="Prepare your visa application with AI">
            Visa paperwork,<br className="hero-break" /> done properly.
            <span className="visually-hidden"> Prepare your visa application with AI</span>
          </h1>
          <p className="hero-sub">
            Photograph your passport, answer only what your visa needs, and check your information line by line.
            You stay in charge of what gets submitted.
          </p>
          <div className="hero-buttons">
            <MainButton />
            <a href="#how-it-works" className="pill-button pill-secondary" data-activity-id="how-it-works-link">
              <span className="pill-label">See how it works</span>
            </a>
          </div>
          <div className="hero-preview" role="group" aria-label="Illustrative product preview">
            <div className="hero-preview-grid">
              <div className="preview-pane">
                <div className="preview-top"><span className="eyebrow">01 · Passport</span>
                  <span className="status-dot status-dot--ok">Check digits pass</span>
                </div>
                <dl className="preview-fields">
                  <dt>Surname</dt><dd>ERIKSSON</dd>
                  <dt>Given</dt><dd>ANNA MARIA</dd>
                  <dt>Number</dt><dd>L898902C3</dd>
                  <dt>Born</dt><dd>12 AUG 1974</dd>
                </dl>
                <p className="preview-mrz" dir="ltr">P&lt;UTOERIKSSON&lt;&lt;ANNA&lt;MARIA&lt;&lt;&lt;&lt;&lt;&lt;&lt;&lt;</p>
              </div>
              <div className="preview-pane">
                <span className="eyebrow">02 · Question 14 of 22</span>
                <p className="preview-question">Where will you stay in France?</p>
                <span className="preview-choice is-selected">Hotel or rental</span>
                <span className="preview-choice">With a host</span>
              </div>
              <div className="preview-pane preview-check">
                <span className="eyebrow">03 · Check</span>
                <p className="preview-count"><strong>35</strong> of 37 boxes match</p>
                <ul>
                  <li className="status-dot status-dot--warn">Hotel check-out after return flight</li>
                  <li className="status-dot status-dot--danger">Travel insurance not uploaded</li>
                </ul>
              </div>
            </div>
            <p className="preview-caption">Illustrative example · ICAO specimen data</p>
          </div>
        </div>
      </section>

      <section id="how-it-works" className="section" aria-labelledby="how-heading">
        <div className="container">
          <header className="section-header">
            <h2 id="how-heading">Four steps. You sign off on each.</h2>
          </header>
          <ol className="steps">
            {steps.map((step, index) => (
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
            <h2 id="what-heading">Built to catch what consulates catch.</h2>
          </header>
          <div className="feature-grid">
            <article className="card feature-card feature-passport">
              <div className="feature-mrz" dir="ltr" aria-hidden="true">
                P&lt;UTOERIKSSON&lt;&lt;ANNA&lt;MARIA&lt;&lt;&lt;&lt;<br />
                L898902C<mark>3</mark>6UTO7408122F1204159
              </div>
              <div><h3>Passport reading you can check</h3>
                <p>We test the machine-readable lines against their check digits and mark uncertain values for you to confirm.</p></div>
            </article>
            <article className="card feature-card">
              <div className="feature-question"><strong>22</strong> questions in this example</div>
              <div><h3>Only what applies</h3>
                <p>The questionnaire adapts to your trip and saves your answers so you can pick up where you left off.</p></div>
            </article>
            <article className="card feature-card">
              <ul className="feature-checks" aria-label="Example document checks">
                <li className="status-dot status-dot--ok">Name matches bank statement</li>
                <li className="status-dot status-dot--warn">Booking ends after return</li>
                <li className="status-dot status-dot--danger">Insurance missing</li>
              </ul>
              <div><h3>Cross-document check</h3>
                <p>Compare key names, dates and numbers against the documents we can read.</p></div>
            </article>
            <article className="card feature-card">
              <div className="feature-chat" aria-label="Illustrative assistant conversation">
                <p className="bubble bubble-user">Where do I add my booking?</p>
                <p className="bubble bubble-assistant">Open Supporting documents to see the checklist for your trip.</p>
              </div>
              <div><h3>An assistant you stay in charge of</h3>
                <p>Review every suggested change. You decide when to submit to the consulate.</p></div>
            </article>
            <div id="documents" className="card list-card feature-card">
              <div><h3>Have these ready</h3>
                <p className="list-note">Your exact checklist depends on your destination and nationality.</p></div>
              <ul className="check-list">
                {requiredDocuments.map(item => <li key={item}><CheckIcon />{item}</li>)}
              </ul>
            </div>
          </div>
        </div>
      </section>

      <section id="destinations" className="section" aria-labelledby="destinations-heading">
        <div className="container">
          <header className="section-header">
            <p className="badge">Destinations</p>
            <h2 id="destinations-heading">Where do you want to go?</h2>
          </header>
          <ul className="destinations">
            {destinations.map(item => (
              <li key={item.id} className="card destination">
                <img src={item.flag} alt="" width="40" height="30" />
                <div>
                  <h3>{item.name}</h3>
                  <p>{item.visa}</p>
                  <Feature name="visa_guide">
                    <p><Link to={`/visas/${item.guide}`} className="destination-guide" data-activity-id={`visa-guide-${item.guide}`}>
                      Read the guide<span className="visually-hidden"> for {item.name}</span></Link></p>
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
            <h2 id="privacy-heading">Your passport is yours. Nothing leaves without your say.</h2>
          </header>
          <ul className="privacy">
            {privacyPoints.map(point => (
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
            <p className="badge">FAQ</p>
            <h2 id="faq-heading">Frequently asked questions</h2>
          </header>
          <FaqList items={faqs.slice(0, 5)} />
          <p className="more-link"><Link to="/faq" data-activity-id="faq-link">See all questions</Link></p>
        </div>
      </section>

      <section className="section" aria-labelledby="cta-heading">
        <div className="container final-cta">
          <h2 id="cta-heading">Where to next?</h2>
          <MainButton />
        </div>
      </section>
    </>
  )
}
