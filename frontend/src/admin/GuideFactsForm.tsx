import type { ReactNode } from 'react'
import type { Json, JsonObject, Problem } from './requirementsApi'

type Path = (string | number)[]

function read(data: Json, path: Path): Json {
  let node: Json = data
  for (const key of path) {
    if (node === null || typeof node !== 'object') return null
    node = Array.isArray(node) ? node[key as number] ?? null : node[key as string] ?? null
  }
  return node
}

function write(data: Json, path: Path, value: Json): Json {
  if (path.length === 0) return value
  const [key, ...rest] = path
  if (typeof key === 'number') {
    const list = Array.isArray(data) ? [...data] : []
    list[key] = write(list[key] ?? null, rest, value)
    return list
  }
  const object = data !== null && typeof data === 'object' && !Array.isArray(data) ? { ...data } : {}
  object[key] = write(object[key] ?? null, rest, value)
  return object
}

const text = (value: Json) => (typeof value === 'string' ? value : '')
const list = (value: Json): Json[] => (Array.isArray(value) ? value : [])
const key = (path: Path) => path.join('.')

type FormProps = { guide: JsonObject; problems: Problem[]; disabled: boolean; onChange: (guide: JsonObject) => void }

export default function GuideFactsForm({ guide, problems, disabled, onChange }: FormProps) {
  const errors = (path: Path, nested = false) => problems.filter(problem => {
    const where = key(problem.loc)
    return where === key(path) || (nested && where.startsWith(`${key(path)}.`))
  })
  const set = (path: Path, value: Json) => onChange(write(guide, path, value) as JsonObject)
  const shown = new Set<Problem>()

  function messages(path: Path, nested = false) {
    const found = errors(path, nested)
    found.forEach(problem => shown.add(problem))
    return found.length ? <ul className="backoffice-errors" id={`guide-error-${key(path)}`}>
      {found.map((problem, index) => <li key={index}>{problem.message}</li>)}
    </ul> : null
  }

  function field(label: string, path: Path, options: { multiline?: boolean; type?: string; hint?: string } = {}): ReactNode {
    const id = `guide-${key(path)}`
    const problemsHere = errors(path)
    const props = {
      id, disabled, value: text(read(guide, path)), 'aria-invalid': problemsHere.length > 0 || undefined,
      'aria-describedby': problemsHere.length ? `guide-error-${key(path)}` : undefined,
    }
    return <label className="admin-field backoffice-field" htmlFor={id}>
      <span>{label}</span>
      {options.hint && <small className="admin-muted">{options.hint}</small>}
      {options.multiline
        ? <textarea {...props} rows={3} onChange={event => set(path, event.target.value)} />
        : <input {...props} type={options.type ?? 'text'} onChange={event => set(path, event.target.value)} />}
      {messages(path)}
    </label>
  }

  function link(label: string, path: Path) {
    return <fieldset className="backoffice-link">
      <legend>{label}</legend>
      {field('Link text', [...path, 'label'])}
      {field('Official https address', [...path, 'url'], { type: 'url' })}
      {messages(path)}
    </fieldset>
  }

  function rows(path: Path, empty: Json, render: (index: number) => ReactNode, addLabel: string) {
    const items = list(read(guide, path))
    return <>
      {items.map((_, index) => <div className="backoffice-row" key={index}>
        {render(index)}
        <button type="button" className="button button-secondary" disabled={disabled}
          onClick={() => set(path, items.filter((__, other) => other !== index))}>Remove</button>
      </div>)}
      <button type="button" className="button button-secondary" disabled={disabled}
        onClick={() => set(path, [...items, empty])}>{addLabel}</button>
      {messages(path)}
    </>
  }

  const nationalities = Object.entries((read(guide, ['need', 'checked_nationalities']) ?? {}) as Record<string, Json>)
  const setNationalities = (entries: [string, Json][]) => set(['need', 'checked_nationalities'], Object.fromEntries(entries))
  const notes = list(read(guide, ['need', 'notes'])).map(text).join('\n')

  const sections = <>
    <section className="backoffice-group" aria-labelledby="guide-checked-heading">
      <h3 id="guide-checked-heading">When the figures were checked</h3>
      {field('Checked on', ['checked_on'], { type: 'date', hint: 'The day you compared every figure with its official source.' })}
    </section>

    <section className="backoffice-group" aria-labelledby="guide-fees-heading">
      <h3 id="guide-fees-heading">Fees</h3>
      {rows(['fees', 'items'], { label: '', amount: '' }, index => <>
        {field('Who pays it', ['fees', 'items', index, 'label'])}
        {field('Amount', ['fees', 'items', index, 'amount'], { hint: 'Up to 20 characters, such as €90.' })}
      </>, 'Add a fee')}
      {field('Note under the fees', ['fees', 'note'], { multiline: true })}
      {link('Where the fees come from', ['fees', 'source'])}
    </section>

    <section className="backoffice-group" aria-labelledby="guide-time-heading">
      <h3 id="guide-time-heading">Processing time</h3>
      {field('How long a decision takes', ['time', 'text'], { multiline: true })}
      {link('Where the time comes from', ['time', 'source'])}
    </section>

    <section className="backoffice-group" aria-labelledby="guide-need-heading">
      <h3 id="guide-need-heading">Who needs the visa</h3>
      {field('General rule', ['need', 'rule'], { multiline: true })}
      <label className="admin-field backoffice-field" htmlFor="guide-need.notes">
        <span>Notes, one per line</span>
        <textarea id="guide-need.notes" rows={3} disabled={disabled} value={notes}
          onChange={event => set(['need', 'notes'], event.target.value.split('\n'))} />
        {messages(['need', 'notes'], true)}
      </label>
      {link('Official checker', ['need', 'checker'])}
      <fieldset className="backoffice-link">
        <legend>Nationalities checked against the official list</legend>
        {nationalities.map(([code, answer], index) => <div className="backoffice-row" key={index}>
          <label className="admin-field" htmlFor={`guide-nationality-${index}`}><span>Passport code</span>
            <input id={`guide-nationality-${index}`} value={code} maxLength={3} disabled={disabled}
              onChange={event => setNationalities(nationalities.map((entry, other) =>
                other === index ? [event.target.value.toUpperCase(), entry[1]] : entry))} />
          </label>
          <label className="admin-field" htmlFor={`guide-nationality-answer-${index}`}><span>Needs a visa?</span>
            <select id={`guide-nationality-answer-${index}`} value={text(answer)} disabled={disabled}
              onChange={event => setNationalities(nationalities.map((entry, other) =>
                other === index ? [entry[0], event.target.value] : entry))}>
              <option value="visa">Needs a visa</option>
              <option value="no_visa">No visa needed</option>
            </select>
          </label>
          <button type="button" className="button button-secondary" disabled={disabled}
            onClick={() => setNationalities(nationalities.filter((_, other) => other !== index))}>Remove</button>
        </div>)}
        <button type="button" className="button button-secondary" disabled={disabled}
          onClick={() => setNationalities([...nationalities, ['', 'visa']])}>Add a nationality</button>
        {messages(['need', 'checked_nationalities'], true)}
      </fieldset>
    </section>

    <section className="backoffice-group" aria-labelledby="guide-coverage-heading">
      <h3 id="guide-coverage-heading">What Ta'asheera does for this visa</h3>
      <label className="admin-field backoffice-field" htmlFor="guide-coverage.level">
        <span>Level</span>
        <select id="guide-coverage.level" value={text(read(guide, ['coverage', 'level']))} disabled={disabled}
          onChange={event => set(['coverage', 'level'], event.target.value)}>
          <option value="filled_for_you">The form is filled for the traveler</option>
          <option value="guided">Guided: the traveler fills the official form</option>
        </select>
        {messages(['coverage', 'level'])}
      </label>
      {field('Explanation', ['coverage', 'text'], { multiline: true })}
    </section>

    <section className="backoffice-group" aria-labelledby="guide-sources-heading">
      <h3 id="guide-sources-heading">Other official sources</h3>
      {rows(['extra_sources'], { label: '', url: '' }, index => <>
        {field('Link text', ['extra_sources', index, 'label'])}
        {field('Official https address', ['extra_sources', index, 'url'], { type: 'url' })}
      </>, 'Add a source')}
    </section>
  </>
  const others = problems.filter(problem => !shown.has(problem))
  return <div className="backoffice-guide">
    {others.length > 0 && <ul className="backoffice-errors" aria-label="Other problems in the guide facts">
      {others.map((problem, index) => <li key={index}><code>{problem.path || 'guide'}</code> {problem.message}</li>)}
    </ul>}
    {sections}
  </div>
}
