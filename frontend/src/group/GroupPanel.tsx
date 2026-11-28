import { useEffect, useState } from 'react'
import { Link } from 'react-router'
import { toast } from 'sonner'
import { protectedFetch } from '../auth'
import './group.css'

// Mirrors backend/app/applications/group.py (CMPS271-81).
type Member = { application_id: string; label: string; lead: boolean; status: string; answered: number; total: number }
type Group = { group_id: string | null; lead_id: string | null; members: Member[]; shared: string[]; can_add: boolean }

async function detail(response: Response, fallback: string) {
  const problem = await response.json().catch(() => null) as { detail?: unknown } | null
  return typeof problem?.detail === 'string' ? problem.detail : fallback
}

async function request(path: string, init: RequestInit & { silent?: boolean } = {}, fallback = 'Something went wrong.'): Promise<Group> {
  const response = await protectedFetch(path, { cache: 'no-store', ...init })
  if (!response.ok) throw new Error(await detail(response, fallback))
  return response.json()
}

const STATUS: Record<string, string> = {
  DRAFT: 'Answering', INELIGIBLE: 'Stopped', ANSWERS_COMPLETE: 'Answers complete', SUBMITTED: 'Submitted',
  APPROVED: 'Approved', REFUSED: 'Refused',
}

// Travelling together: an application per traveler, the trip answers asked once on the first one.
export default function GroupPanel({ applicationId, version, onChange }: { applicationId: string; version: string; onChange?: () => void }) {
  const [group, setGroup] = useState<Group | null | Error>(null)
  const [label, setLabel] = useState('')
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    let active = true
    request(`/applications/${applicationId}/group`, {}, 'Could not load your group.')
      .then(data => { if (active) setGroup(data) })
      .catch(problem => { if (active) setGroup(problem instanceof Error ? problem : new Error('Could not load your group.')) })
    return () => { active = false }
  }, [applicationId, version])

  if (group === null || group instanceof Error) return null

  async function addMember(event: React.FormEvent) {
    event.preventDefault()
    if (!label.trim()) return
    setBusy(true)
    try {
      setGroup(await request(`/applications/${applicationId}/group/members`, {
        method: 'POST', silent: true, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ label }),
      }, 'Could not add that traveler.'))
      setLabel('')
      toast.success('Added. Their trip answers are filled in; open their application for their passport and details.')
      onChange?.()
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Could not add that traveler.')
    } finally {
      setBusy(false)
    }
  }

  async function sync() {
    setBusy(true)
    try {
      setGroup(await request(`/applications/${applicationId}/group/sync`, { method: 'POST', silent: true },
        'Could not copy the trip answers.'))
      toast.success('The trip answers are copied to everyone still preparing.')
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Could not copy the trip answers.')
    } finally {
      setBusy(false)
    }
  }

  const isLead = group.lead_id === null || group.lead_id === applicationId
  return <section className="group" aria-labelledby="group-title">
    <div className="group-head">
      <div>
        <h2 id="group-title">Travelling together</h2>
        <p className="group-note">Each traveler has their own application and passport. Where, when and where you stay
          are asked once, here, and copied to everyone.</p>
      </div>
      {group.group_id && isLead && group.members.length > 1 && <button type="button"
        className="button button-secondary dash-small" disabled={busy} data-activity-id="group-sync"
        onClick={() => void sync()}>Copy trip answers to everyone</button>}
    </div>
    {group.members.length > 0 && <ul className="group-list">
      {group.members.map(member => <li key={member.application_id}>
        {member.application_id === applicationId
          ? <strong>{member.label}{member.lead && ' (first traveler)'}</strong>
          : <Link to={`/dashboard/${member.application_id}`}>{member.label}{member.lead && ' (first traveler)'}</Link>}
        <span className="group-status">{STATUS[member.status] ?? member.status} · {member.answered} of {member.total}</span>
      </li>)}
    </ul>}
    {!group.group_id && <p className="group-note">Just you so far. Add anyone travelling with you, such as family.</p>}
    {group.can_add && isLead && <form className="group-form" onSubmit={event => void addMember(event)}>
      <label>
        <span>Traveler's name</span>
        <input type="text" value={label} maxLength={60} required placeholder="For example: Maria"
          onChange={event => setLabel(event.target.value)} />
      </label>
      <button type="submit" className="button button-primary dash-small" disabled={busy || !label.trim()}
        data-activity-id="group-add">Add a traveler</button>
    </form>}
    {!isLead && group.lead_id && <p className="group-note">Trip answers come from the <Link to={`/dashboard/${group.lead_id}`}>
      first traveler's application</Link>. Add or update travelers there.</p>}
  </section>
}
