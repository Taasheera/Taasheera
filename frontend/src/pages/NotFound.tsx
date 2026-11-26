import { useEffect } from 'react'
import { Link } from 'react-router'
import { useText } from '../i18n/language'

export default function NotFound() {
  const text = useText().notFound
  useEffect(() => {
    document.title = text.title
  }, [text.title])

  return (
    <section className="section page status" aria-labelledby="not-found-heading">
      <div className="container narrow">
        <p className="status-code" aria-hidden="true">404</p>
        <h1 id="not-found-heading">{text.heading}</h1>
        <p>{text.body}</p>
        <div className="status-actions">
          <Link to="/" className="pill-button pill-primary" data-activity-id="home-link"><span className="pill-label">{text.home}</span></Link>
          <Link to="/faq" className="pill-button pill-secondary" data-activity-id="faq-link"><span className="pill-label">{text.faq}</span></Link>
        </div>
      </div>
    </section>
  )
}
