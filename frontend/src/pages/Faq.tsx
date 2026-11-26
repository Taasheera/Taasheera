import { useEffect } from 'react'
import { useText } from '../i18n/language'
import FaqList from './FaqList'

export default function Faq() {
  const { faqs, faqPage } = useText()
  useEffect(() => {
    document.title = faqPage.title
  }, [faqPage.title])

  return (
    <section className="section page" aria-labelledby="faq-page-heading">
      <div className="container narrow">
        <header className="section-header">
          <p className="badge">{faqPage.badge}</p>
          <h1 id="faq-page-heading">{faqPage.heading}</h1>
          <p>{faqPage.intro}</p>
        </header>
        <FaqList items={faqs} />
      </div>
    </section>
  )
}
