import { useEffect, useState, type KeyboardEvent, type MouseEvent } from 'react'
import { Link, NavLink, Outlet, ScrollRestoration, useLocation, useNavigate } from 'react-router'
import { toast, Toaster } from 'sonner'
import { ArrowIcon, MenuIcon } from './icons'
import AppearanceSelect from './AppearanceSelect'
import ActivityTracker from './ActivityTracker'
import GlobalAssistant from './assistant/GlobalAssistant'
import IdleGuard from './IdleGuard'
import SectionNav from './SectionNav'
import { restoreSession, signOut, type Traveler } from './auth'
import { disableGoogleAutoSelect } from './googleIdentity'
import { createAccountPath, signInPath } from './paths'
import './site.css'

// Signed in, the section panel below the bar is the navigation; FAQ moves next to Log out.
function MainLinks({ signedIn }: { signedIn: boolean }) {
  if (signedIn) return null
  return (
    <>
      <Link to="/#how-it-works" data-activity-id="how-it-works-link">How it works</Link>
      <Link to="/#destinations" data-activity-id="destinations-link">Destinations</Link>
      <Link to="/#privacy" data-activity-id="privacy-link">Privacy</Link>
      <NavLink to="/faq" data-activity-id="faq-link">FAQ</NavLink>
    </>
  )
}

export type LayoutContext = { traveler: Traveler | null }

type AccountProps = { traveler: Traveler | null; loggingOut: boolean; onLogOut: () => void; inMenu?: boolean
  // The applications list has its own New application button, so the header doesn't repeat it there.
  hideCta?: boolean }

// Signed out: Log in / Get started. Signed in: Log out / New application (Account is in the section panel).
function AccountLinks({ traveler, loggingOut, onLogOut, inMenu = false, hideCta = false }: AccountProps) {
  const cta = traveler
    ? <Link to="/apply" className="header-cta" data-activity-id="start-application-link">
        <span className="mini-circle"><ArrowIcon /></span>New application
      </Link>
    : <Link to={createAccountPath} reloadDocument className="header-cta" data-activity-id="get-started-link">
        <span className="mini-circle"><ArrowIcon /></span>Get started
      </Link>
  const account = traveler
    ? <>
        <NavLink to="/faq" className={inMenu ? undefined : 'login-link'} data-activity-id="faq-link">FAQ</NavLink>
        <button type="button" className={`logout-button${inMenu ? '' : ' login-link'}`} data-activity-id="logout-button"
          disabled={loggingOut} onClick={onLogOut}>{loggingOut ? 'Logging out…' : 'Log out'}</button>
      </>
    : <Link to={signInPath} className={inMenu ? undefined : 'login-link'} data-activity-id="login-link">Log in</Link>
  return <>{account}{hideCta ? null : cta}</>
}

export default function Layout() {
  const navigate = useNavigate()
  const location = useLocation()
  // Below 1000px the nav links collapse into this menu (see site.css).
  const [menuOpen, setMenuOpen] = useState(false)
  const [traveler, setTraveler] = useState<Traveler | null>(null)
  const [loggingOut, setLoggingOut] = useState(false)
  const closeOnLink = (event: MouseEvent) => {
    if ((event.target as Element).closest('a')) setMenuOpen(false)
  }
  const closeOnEscape = (event: KeyboardEvent) => {
    if (event.key === 'Escape') setMenuOpen(false)
  }

  // Shares the session restore ActivityTracker already makes; a failure just leaves the header signed out.
  useEffect(() => {
    let active = true
    restoreSession().then(profile => { if (active) setTraveler(profile) }).catch(() => {})
    return () => { active = false }
  }, [])

  // A page that sent the visitor here (e.g. the admin dashboard) can leave a notice to show once.
  const notice = (location.state as { notice?: unknown } | null)?.notice
  useEffect(() => {
    if (typeof notice !== 'string') return
    toast.error(notice, { id: 'route-notice' })
    navigate(`${location.pathname}${location.search}${location.hash}`, { replace: true, state: null })
  }, [notice, navigate, location.pathname, location.search, location.hash])

  async function logOut() {
    setLoggingOut(true)
    try {
      await signOut()
      disableGoogleAutoSelect()
      setTraveler(null)
      setMenuOpen(false)
      // Leaving the page unmounts anything it showed, such as a passport under review.
      navigate('/')
    } catch {
      toast.error('Could not log out. Check your connection and try again.')
    } finally {
      setLoggingOut(false)
    }
  }
  const account = { traveler, loggingOut, onLogOut: logOut, hideCta: traveler !== null && /^\/dashboard\/?$/.test(location.pathname) }

  return (
    <div className="site">
      <ActivityTracker />
      <IdleGuard />
      <ScrollRestoration />
      <a className="skip-link" href="#main" data-activity-id="skip-link">Skip to content</a>
      <header className={`navbar-wrap${traveler ? ' navbar-signed-in' : ''}`} onKeyDown={closeOnEscape}>
        <div className="navbar">
          <Link to="/" className="logo" aria-label="Taasheera home" data-activity-id="home-link">
            <img className="logo-mark" src="/mark-ink.svg" alt="" />taasheera
          </Link>
          {traveler ? <SectionNav pathname={location.pathname} routed /> :
            <nav className="nav-links" aria-label="Main"><MainLinks signedIn={false} /></nav>}
          <div className="nav-right">
            <AccountLinks {...account} />
            <AppearanceSelect />
            {traveler && <span className="nav-avatar" aria-hidden="true">
              {traveler.name.trim().split(/\s+/).slice(0, 2).map(part => part[0]).join('').toUpperCase()}
            </span>}
            <button
              type="button"
              className="menu-button"
              data-activity-id="menu-toggle"
              aria-expanded={menuOpen}
              aria-controls="mobile-menu"
              aria-label={menuOpen ? 'Close menu' : 'Open menu'}
              onClick={() => setMenuOpen(open => !open)}
            >
              <MenuIcon open={menuOpen} />
            </button>
          </div>
        </div>
        {menuOpen && (
          <nav id="mobile-menu" className="mobile-menu" aria-label="Menu" onClick={closeOnLink}>
            <MainLinks signedIn={traveler !== null} />
            <AccountLinks {...account} inMenu />
          </nav>
        )}
      </header>

      {/* Pages read the signed-in traveler from here, e.g. the homepage's main button. */}
      <main id="main"><Outlet context={{ traveler } satisfies LayoutContext} /></main>
      {/* The passport and application pages have their own assistant with that page's context. */}
      {traveler && !/^\/(passport|apply)(\/|$)/.test(location.pathname) && <GlobalAssistant />}

      <footer className="site-footer">
        <div className="container">
          <div className="footer-top">
            <Link to="/" className="logo" data-activity-id="home-link">
              <img className="logo-mark" src="/mark-ink.svg" alt="" />taasheera
            </Link>
            <nav className="footer-links" aria-label="Footer">
              <Link to="/#how-it-works" data-activity-id="how-it-works-link">How it works</Link>
              <Link to="/faq" data-activity-id="faq-link">FAQ</Link>
              {traveler
                ? <>
                    <Link to="/dashboard" data-activity-id="dashboard-link">Your applications</Link>
                    <Link to="/passport" data-activity-id="passport-link">Your passport</Link>
                  </>
                : <Link to={signInPath} data-activity-id="login-link">Log in</Link>}
            </nav>
          </div>
          <p className="footer-legal">
            Taasheera helps you prepare visa applications. We are not affiliated with any embassy or
            government authority, and we do not give legal or immigration advice. Only the embassy or
            consulate decides whether to issue a visa. Nothing is submitted without your confirmation.
          </p>
        </div>
      </footer>

      <Toaster position="top-center" theme="system" closeButton />
    </div>
  )
}
