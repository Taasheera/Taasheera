import { useEffect, useState, type KeyboardEvent, type MouseEvent } from 'react'
import { Link, NavLink, Outlet, ScrollRestoration, useLocation, useNavigate } from 'react-router'
import { toast, Toaster } from 'sonner'
import { MenuIcon } from './icons'
import AccountMenu from './AccountMenu'
import ThemeToggle from './ThemeToggle'
import LanguageProvider from './i18n/LanguageProvider'
import LanguageToggle from './i18n/LanguageToggle'
import { useText } from './i18n/language'
import ActivityTracker from './ActivityTracker'
import GlobalAssistant from './assistant/GlobalAssistant'
import Feature from './Feature'
import IdleGuard from './IdleGuard'
import PrivacyConsent from './PrivacyConsent'
import SectionNav from './SectionNav'
import { restoreSession, signOut, type Traveler } from './auth'
import { disableGoogleAutoSelect } from './googleIdentity'
import { createAccountPath, signInPath } from './paths'
import './site.css'

// Signed out, the bar links to the homepage sections. Signed in, SectionNav replaces them.
function MainLinks() {
  const { site } = useText()
  return (
    <>
      <Link to="/#how-it-works" data-activity-id="how-it-works-link">{site.howItWorks}</Link>
      <Link to="/#destinations" data-activity-id="destinations-link">{site.destinations}</Link>
      <Feature name="visa_guide"><NavLink to="/visas" data-activity-id="visa-guides-link">{site.visaGuides}</NavLink></Feature>
      <Link to="/#privacy" data-activity-id="privacy-link">{site.privacy}</Link>
      <NavLink to="/faq" data-activity-id="faq-link">{site.faq}</NavLink>
    </>
  )
}

export type LayoutContext = { traveler: Traveler | null }

// Signed out: Log in and Get started. Signed in, the avatar's AccountMenu holds Log out instead.
function GuestLinks({ inMenu = false }: { inMenu?: boolean }) {
  const { site } = useText()
  return <>
    <Link to={signInPath} className={inMenu ? undefined : 'login-link'} data-activity-id="login-link">{site.logIn}</Link>
    <Link to={createAccountPath} reloadDocument className="header-cta" data-activity-id="get-started-link">{site.getStarted}</Link>
  </>
}

// The header, footer and translated pages read their text from the language provider (CMPS271-16).
export default function Layout() {
  return <LanguageProvider><Site /></LanguageProvider>
}

function Site() {
  const { site } = useText()
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
      toast.error(site.logOutFailed)
    } finally {
      setLoggingOut(false)
    }
  }

  return (
    <div className="site">
      <ActivityTracker />
      <IdleGuard />
      <ScrollRestoration />
      <a className="skip-link" href="#main" data-activity-id="skip-link">{site.skip}</a>
      <header className={`navbar-wrap${traveler ? ' navbar-signed-in' : ''}`} onKeyDown={closeOnEscape}>
        <div className="navbar">
          <Link to="/" className="logo" aria-label={site.home} data-activity-id="home-link">
            <img className="logo-mark" src="/mark-ink.svg" alt="" />taasheera
          </Link>
          {traveler ? <SectionNav pathname={location.pathname} routed /> :
            <nav className="nav-links" aria-label={site.mainNav}><MainLinks /></nav>}
          <div className="nav-right">
            {!traveler && <GuestLinks />}
            <LanguageToggle />
            <ThemeToggle />
            {traveler
              ? <AccountMenu traveler={traveler} loggingOut={loggingOut} onLogOut={logOut} />
              : <button
                  type="button"
                  className="menu-button"
                  data-activity-id="menu-toggle"
                  aria-expanded={menuOpen}
                  aria-controls="mobile-menu"
                  aria-label={menuOpen ? site.closeMenu : site.openMenu}
                  onClick={() => setMenuOpen(open => !open)}
                >
                  <MenuIcon open={menuOpen} />
                </button>}
          </div>
        </div>
        {menuOpen && !traveler && (
          <nav id="mobile-menu" className="mobile-menu" aria-label={site.menu} onClick={closeOnLink}>
            <MainLinks />
            <GuestLinks inMenu />
          </nav>
        )}
      </header>

      {/* Pages read the signed-in traveler from here, e.g. the homepage's main button. */}
      <main id="main"><Outlet context={{ traveler } satisfies LayoutContext} /></main>
      {/* The passport and application pages have their own assistant with that page's context. */}
      {traveler && !/^\/(passport|apply)(\/|$)/.test(location.pathname) && <GlobalAssistant />}
      {/* Signed in: accept the privacy policy before uploading documents (CMPS271-115). Keyed so another account checks again. */}
      {traveler && <Feature name="privacy_consent"><PrivacyConsent key={traveler.id} /></Feature>}

      <footer className="site-footer">
        <div className="container">
          <div className="footer-top">
            <Link to="/" className="logo" data-activity-id="home-link">
              <img className="logo-mark" src="/mark-ink.svg" alt="" />taasheera
            </Link>
            <nav className="footer-links" aria-label={site.footer}>
              <Link to="/#how-it-works" data-activity-id="how-it-works-link">{site.howItWorks}</Link>
              <Link to="/faq" data-activity-id="faq-link">{site.faq}</Link>
              <Feature name="visa_guide"><Link to="/visas" data-activity-id="visa-guides-link">{site.visaGuides}</Link></Feature>
              {traveler
                ? <>
                    <Link to="/dashboard" data-activity-id="dashboard-link">{site.yourApplications}</Link>
                    <Link to="/passport" data-activity-id="passport-link">{site.yourPassport}</Link>
                  </>
                : <Link to={signInPath} data-activity-id="login-link">{site.logIn}</Link>}
            </nav>
          </div>
          <p className="footer-legal">{site.legal}</p>
        </div>
      </footer>

      <Toaster position="top-center" theme="system" closeButton />
    </div>
  )
}
