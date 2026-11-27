import { useEffect } from 'react'
import { byCategory, useHelpEntries } from '../help/helpContent'
import FaqList from './FaqList'

export default function Faq() {
  const items = useHelpEntries()
  const groups = byCategory(items)
  useEffect(() => {
    document.title = 'FAQ | Taasheera'
  }, [])

  return (
    <section className="section page" aria-labelledby="faq-page-heading">
      <div className="container narrow">
        <header className="section-header">
          <p className="badge">Help center</p>
          <h1 id="faq-page-heading">Frequently asked questions</h1>
          <p>Everything you need to know about preparing your visa application with Taasheera.</p>
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
