import { useCallback, useEffect, useState } from 'react'
import { Link, Outlet, useNavigate } from 'react-router'
import { Toaster } from 'sonner'
import { restoreSession } from '../auth'
import IdleGuard from '../IdleGuard'
import GlobalAssistant from '../assistant/GlobalAssistant'
import Feature from '../Feature'
import NotFound from '../pages/NotFound'
import { signInPath } from '../paths'
import { ADMIN_REQUIRED, AdminRequestError, getAdminMe, type AdminProfile } from './api'
import { roleLabels } from './format'
import { getStaffMe } from './supportApi'
import '../site.css'
import '../ui.css'
import './admin.css'

export type AdminContext = { admin: AdminProfile; leave: () => void }

const STAFF_REQUIRED = 'Support or admin access required.'

// The page check only decides what to show: the backend refuses every /admin request from
// anyone who is not an active admin, whatever this page does.
export default function AdminLayout({ access = 'admin' }: { access?: 'admin' | 'staff' }) {
  const navigate = useNavigate()
  const [admin, setAdmin] = useState<AdminProfile | null>(null)
  const [problem, setProblem] = useState('')
  const [missing, setMissing] = useState(false)
  const [attempt, setAttempt] = useState(0)

  // Sends a non-admin home; the site layout shows the notice there.
  const leave = useCallback(() => {
    navigate('/', { replace: true, state: { notice: access === 'staff' ? STAFF_REQUIRED : ADMIN_REQUIRED } })
  }, [navigate, access])

  useEffect(() => {
    document.title = access === 'staff' ? 'Support | Taasheera' : 'Admin | Taasheera'
    let active = true
    restoreSession().then(async traveler => {
      if (!active) return
      if (!traveler) {
        navigate(signInPath, { replace: true })
        return
      }
      const profile = access === 'staff' ? await getStaffMe() : await getAdminMe()
      if (active) setAdmin(profile)
    }).catch(error => {
      if (!active) return
      if (error instanceof AdminRequestError && error.status === 403) leave()
      else if (access === 'staff' && error instanceof AdminRequestError && error.status === 404) setMissing(true)
      else setProblem(error instanceof Error ? error.message : 'Could not check your access.')
    })
    return () => { active = false }
  }, [attempt, leave, navigate, access])

  return <div className="admin-shell">
    <a className="skip-link" href="#admin-main">Skip to content</a>
    <header className="admin-header">
      <div className="admin-header-inner">
        <Link to="/" className="logo" aria-label="Taasheera home">
          <img className="logo-mark" src="/mark-ink.svg" alt="" />taasheera
        </Link>
        <span className="admin-badge">{admin ? roleLabels[admin.role] : access === 'staff' ? 'Staff' : 'Admin'}</span>
        {admin && <nav className="admin-nav" aria-label="Admin">
          {admin.role === 'admin' && <>
            <Link to="/admin">Users</Link>
            <Link to="/backoffice">Monitoring</Link>
            <Feature name="requirements_editor"><Link to="/admin/requirements">Requirements</Link></Feature>
            <Feature name="kpi_dashboard"><Link to="/admin/kpis">KPIs</Link></Feature>
          </>}
          <Feature name="support_tools">
            <Link to="/admin/support">Support requests</Link>
            {admin.role === 'admin' && <Link to="/admin/help">Help content</Link>}
          </Feature>
          <span className="admin-signed-in">Signed in as {admin.name}</span>
        </nav>}
      </div>
    </header>
    <main id="admin-main" className="admin-main">
      {missing ? <NotFound />
        : admin ? <Outlet context={{ admin, leave } satisfies AdminContext} />
        : problem ? <div className="admin-panel" role="alert">
          <p>{problem}</p>
          <div className="admin-actions">
            <button type="button" className="button button-primary" onClick={() => {
              setProblem(''); setAttempt(value => value + 1)
            }}>Try again</button>
            <Link to={signInPath} className="button button-secondary">Sign in</Link>
          </div>
        </div>
          : <p role="status" className="admin-muted">Checking your access…</p>}
    </main>
    <IdleGuard />
    {admin && <GlobalAssistant key={admin.id} />}
    <Toaster position="top-center" theme="system" closeButton />
  </div>
}
