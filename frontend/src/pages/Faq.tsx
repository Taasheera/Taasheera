import { useEffect } from 'react'
import { byCategory, useHelpEntries } from '../help/helpContent'
import { useText } from '../i18n/language'
import FaqList from './FaqList'

export default function Faq() {
  const { faqPage } = useText()
  const items = useHelpEntries()
  const groups = byCategory(items)
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
        {groups.length > 1
          ? groups.map(group => <section key={group.category} aria-label={group.category || 'Other questions'}>
              {group.category && <h2>{group.category}</h2>}
              <FaqList items={group.items} />
            </section>)
          : <FaqList items={items} />}
      </div>
    </section>
  )
}
