import { useEffect, useRef, useState } from 'react'
import { getPortalGuide, type GuideField, type PortalGuideData } from './portalGuideApi'
import { countries } from '../countries'
import './portalGuide.css'

const countryName = (code: string) => countries.find(country => country.code === code)?.name ?? code
// Addresses end with the country code as saved; show the country's name instead.
const shown = (field: GuideField) => field.value === null ? null
  : field.kind === 'country' ? countryName(field.value)
  : field.kind === 'address' ? field.value.replace(/(^|, )([A-Z]{1,3})$/, (_, lead: string, code: string) => lead + countryName(code))
  : field.value

function CopyButton({ text, label }: { text: string; label: string }) {
  const [copied, setCopied] = useState(false)
  const timer = useRef<number>(undefined)
  useEffect(() => () => window.clearTimeout(timer.current), [])
  async function copy() {
    try {
      await navigator.clipboard.writeText(text)
      setCopied(true)
      window.clearTimeout(timer.current)
      timer.current = window.setTimeout(() => setCopied(false), 1500)
    } catch { /* The value stays visible to select by hand. */ }
  }
  return <button type="button" className="button button-secondary portal-copy" onClick={() => void copy()}
    aria-label={`Copy ${label}`} data-activity-id="portal-guide-copy">{copied ? 'Copied' : 'Copy'}</button>
}

// For visas applied for in an online portal (UK, US, Canada, ...): the portal's own pages in order,
// with the traveler's saved answers ready to copy. Nothing is sent to the portal from here.
export default function PortalGuide({ applicationId, focused }: { applicationId: string; focused?: boolean }) {
  const [guide, setGuide] = useState<PortalGuideData | null | undefined>(undefined)
  const [error, setError] = useState('')
  const section = useRef<HTMLElement>(null)

  useEffect(() => {
    let active = true
    getPortalGuide(applicationId).then(data => { if (active) setGuide(data) })
      .catch(problem => { if (active) setError(problem instanceof Error ? problem.message : 'Could not load the form guide.') })
    return () => { active = false }
  }, [applicationId])
  useEffect(() => {
    if (focused && guide) section.current?.scrollIntoView?.({ block: 'start' })
  }, [focused, guide])

  if (guide === null) return null
  if (error) return <p className="dash-status" role="alert">{error}</p>
  if (!guide) return <p className="dash-status" role="status">Loading the official form guide…</p>

  return <section id="official-form" ref={section} className="portal-guide" aria-labelledby="portal-guide-title" tabIndex={-1}>
    <div className="portal-guide-head">
      <div>
        <p className="eyebrow">Official online form</p>
        <h2 id="portal-guide-title">Fill in {guide.title.split(':')[0]}</h2>
        <p>{guide.title}. Your saved answers are laid out page by page, in the portal's order, ready to copy.
          {' '}<strong>{guide.filled} of {guide.total}</strong> fields are ready from your answers.</p>
      </div>
      <a className="button button-primary" href={guide.portal.url} target="_blank" rel="noreferrer"
        data-activity-id="portal-guide-open">{guide.portal.label}</a>
    </div>

    <div className="portal-guide-before">
      <h3>Before you start</h3>
      <ul>{guide.before.map(item => <li key={item}>{item}</li>)}</ul>
    </div>

    <ol className="portal-guide-sections">
      {guide.sections.map((part, index) => <li key={part.title}>
        <details open={index === 0}>
          <summary><span className="portal-guide-step">Page {index + 1}</span> {part.title}
            {part.fields.length > 0 && <span className="portal-guide-count">
              {part.fields.filter(field => field.value).length}/{part.fields.length}
            </span>}
          </summary>
          {part.intro && <p>{part.intro}</p>}
          {part.fields.length > 0 && <dl className="portal-guide-fields">
            {part.fields.map(field => {
              const value = shown(field)
              return <div key={field.label} className={value ? '' : 'is-missing'}>
                <dt>{field.label}{field.note && <span className="portal-guide-note">{field.note}</span>}</dt>
                <dd>
                  {value ? <><span className="portal-guide-value">{value}</span><CopyButton text={value} label={field.label} /></>
                    : <span className="portal-guide-empty">Not in your answers. Fill it in on the portal.</span>}
                </dd>
              </div>
            })}
          </dl>}
          {part.yourself.length > 0 && <div className="portal-guide-yourself">
            <p className="eyebrow">Answer these yourself</p>
            <ul>{part.yourself.map(item => <li key={item}>{item}</li>)}</ul>
          </div>}
        </details>
      </li>)}
    </ol>

    <div className="portal-guide-after">
      <h3>After you submit</h3>
      <ol>{guide.after.map(item => <li key={item}>{item}</li>)}</ol>
    </div>
    <p className="portal-guide-sources">Sources:{' '}
      {guide.sources.map((source, index) => <span key={source.url}>{index > 0 && ' · '}
        <a href={source.url} target="_blank" rel="noreferrer">{source.label}</a></span>)}
    </p>
    <p className="portal-guide-sources">You submit the application yourself. Ta'asheera doesn't give legal or immigration advice.</p>
  </section>
}
