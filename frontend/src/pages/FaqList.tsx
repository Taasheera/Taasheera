import RichText from '../help/RichText'
import type { HelpItem } from '../help/helpContent'

// Native <details> gives keyboard and screen-reader support for the accordion.
export default function FaqList({ items }: { items: HelpItem[] }) {
  return (
    <div className="faq-list">
      {items.map(item => (
        <details key={item.id ?? item.question} className="faq-item" lang={item.language} dir={item.language ? 'auto' : undefined}>
          <summary data-activity-id="faq-question">
            {item.question}
            <span className="faq-icon" aria-hidden="true" />
          </summary>
          <RichText text={item.answer} />
        </details>
      ))}
    </div>
  )
}
