import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'
import { GoogleLinkRequiredError, restoreSession, signIn, signInWithGoogle, signOut, type Traveler } from './auth'
import GoogleAccountLink from './GoogleAccountLink'
import GoogleSignIn from './GoogleSignIn'
import { disableGoogleAutoSelect } from './googleIdentity'
import './App.css'
import CreateAccount from './CreateAccount'
import ForgotPassword from './ForgotPassword'
import SetNewPassword from './SetNewPassword'
import ConnectGoogle from './ConnectGoogle'
import ChangePassword from './ChangePassword'
import ProfileForm from './ProfileForm'
import Sessions from './Sessions'
import DocumentHistory from './DocumentHistory'
import DeleteAccount from './DeleteAccount'
import DownloadData from './DownloadData'
import GlobalAssistant from './assistant/GlobalAssistant'
import SectionNav from './SectionNav'
import { readNavigation, type Navigation } from './navigation'

const pageTitles = {
  'sign-in': 'Sign in',
  'create-account': 'Create account',
  'forgot-password': 'Forgot password',
  'set-new-password': 'Set new password',
}

function App({ initialNavigation, accountPage = false, googleClientId = import.meta.env?.VITE_GOOGLE_CLIENT_ID?.trim() ?? '',
  googleConfigurationStatus = 'ready', onGoogleConfigurationRetry, signedInRedirect, embedded = false }: {
  initialNavigation: Navigation; accountPage?: boolean; googleClientId?: string
  googleConfigurationStatus?: 'loading' | 'ready' | 'failed'; onGoogleConfigurationRetry?: () => void
  // The sign-in route sends a signed-in traveler on to this page instead of showing the welcome card.
  signedInRedirect?: string
  // The account page sits inside the site layout, which already has the header, sections, assistant and footer.
  embedded?: boolean
}) {
  const [route, setRoute] = useState({ ...initialNavigation, version: 0 })
  const page = route.page
  const isResetPage = page === 'set-new-password'
  const isRecoveryPage = isResetPage || page === 'forgot-password'
  const [traveler, setTraveler] = useState<Traveler | null>(null)
  const [checking, setChecking] = useState(true)
  const [sessionError, setSessionError] = useState('')
  const [loggingOut, setLoggingOut] = useState(false)
  const [registrationMessage, setRegistrationMessage] = useState('')

  useEffect(() => {
    if (isRecoveryPage) return
    let active = true
    restoreSession().then((profile) => {
      if (active) setTraveler(profile)
    }).catch(() => {
      if (active) setSessionError('Could not restore your session. Check your connection and try reloading, or sign in again.')
    }).finally(() => {
      if (active) setChecking(false)
    })
    return () => { active = false }
  }, [isRecoveryPage])

  useEffect(() => {
    const updatePage = () => {
      const next = readNavigation(window)
      if (next.page !== 'sign-in') setRegistrationMessage('')
      setRoute(previous => ({ ...next, version: previous.version + 1 }))
    }
    window.addEventListener('hashchange', updatePage)
    return () => window.removeEventListener('hashchange', updatePage)
  }, [])

  const redirecting = Boolean(signedInRedirect && traveler && !accountPage && !isRecoveryPage)
  useEffect(() => {
    if (redirecting && signedInRedirect) window.location.replace(signedInRedirect)
  }, [redirecting, signedInRedirect])

  useEffect(() => {
    document.title = `${traveler && !isRecoveryPage ? (accountPage ? 'Account' : 'Signed in') : pageTitles[page]} | Taasheera`
  }, [page, traveler, isRecoveryPage, accountPage])

  return (
    <><Shell embedded={embedded}>
      {!embedded && <header className="brand">
        {/* The sign-in screens sit outside the site layout, so this is their only way home. */}
        <a href="/" className="brand-link" aria-label="Taasheera home" data-activity-id="home-link">
          <img className="brand-mark" src="/mark-midnight.svg" alt="" />
          taasheera
        </a>
      </header>}
      {!embedded && traveler && !isRecoveryPage && !checking && !redirecting && <SectionNav pathname={window.location.pathname} />}

      <div className="auth-body">
      {isResetPage ? <SetNewPassword
        key={route.version}
        token={route.resetToken}
        onTokenCleared={() => setRoute(previous => ({ ...previous, resetToken: null }))}
        onSuccess={() => {
          setTraveler(null)
          setSessionError('')
          setChecking(false)
          setRoute(previous => ({ ...previous, resetToken: null }))
        }}
      /> : page === 'forgot-password' ? <ForgotPassword /> : checking ? <p role="status">Restoring your session…</p>
        : redirecting ? <p role="status">Taking you to your applications…</p> : traveler ? (
        <section className={`sign-in-card${accountPage ? ' account-card' : ''}`} aria-labelledby="signed-in-heading">
          <p className="eyebrow">YOUR TRAVELER ACCOUNT</p>
          <h1 id="signed-in-heading">{accountPage ? 'Your account' : `Welcome, ${traveler.name}`}</h1>
          {accountPage ? <>
            <p className="intro">Your details, how you sign in, and your privacy. <a href="/dashboard">Back to your applications</a></p>
            <ProfileForm />
            <ConnectGoogle traveler={traveler} clientId={googleClientId}
              configurationStatus={googleConfigurationStatus} onConfigurationRetry={onGoogleConfigurationRetry}
              onConnected={setTraveler} />
            {traveler.password_enabled === true && <ChangePassword />}
            <Sessions />
            <DocumentHistory />
            <DownloadData />
            <DeleteAccount traveler={traveler} clientId={googleClientId} onDeleted={complete => {
              setTraveler(null)
              setRegistrationMessage(complete ? 'Your account and data have been deleted.' : 'Your account is disabled. Data erasure is queued for retry.')
              setRoute(previous => ({ ...previous, page: 'sign-in', version: previous.version + 1 }))
            }} />
          </> : <>
            <p className="intro">You are signed in.</p>
            <a href="/account" className="account-link">Edit your account details</a>
            <a href="/apply" className="account-link">Your applications</a>
            <a href="/backoffice" className="account-link">Staff dashboard</a>
            <a href="/passport" className="primary-button passport-link">Upload your passport</a>
          </>}
          {!embedded && <button className="primary-button" data-activity-id="logout-button" disabled={loggingOut} onClick={async () => {
            setLoggingOut(true)
            setSessionError('')
            try {
              await signOut()
              disableGoogleAutoSelect()
              setTraveler(null)
              window.location.hash = 'sign-in'
            } catch {
              setSessionError('Could not confirm logout. Check your connection and try again.')
            } finally {
              setLoggingOut(false)
            }
          }}>{loggingOut ? 'Logging out…' : 'Log out'}</button>}
        </section>
      ) : <>
        {page === 'sign-in' && <SignIn googleClientId={googleClientId}
          googleConfigurationStatus={googleConfigurationStatus} onGoogleConfigurationRetry={onGoogleConfigurationRetry}
          initialMessage={registrationMessage} onSignedIn={(profile) => {
          setRegistrationMessage('')
          setSessionError('')
          setTraveler(profile)
        }} />}
        {page === 'create-account' && <CreateAccount googleClientId={googleClientId}
          googleConfigurationStatus={googleConfigurationStatus} onGoogleConfigurationRetry={onGoogleConfigurationRetry}
          onSignedIn={profile => { setSessionError(''); setTraveler(profile) }} onCreated={() => {
          setRegistrationMessage('Your traveler account has been created. You can now sign in.')
          window.location.hash = 'sign-in'
        }} />}
      </>}
      {sessionError && !isRecoveryPage && <p className="auth-status auth-error" role="alert">{sessionError}</p>}

      </div>
      {!embedded && <aside className="auth-privacy">
        <span lang="ar">تأشيرة</span>
        <div>
          <p>Your answers save after every question. You decide when to submit.</p>
          <ul><li>Owner-only storage</li><li>Private sign-in</li></ul>
        </div>
      </aside>}
    </Shell>
    {!embedded && traveler && !isRecoveryPage && !redirecting && <GlobalAssistant key={traveler.id} />}</>
  )
}

// Its own page for sign-in, or a section of the site layout's <main> for the account page.
function Shell({ embedded, children }: { embedded: boolean; children: ReactNode }) {
  return embedded ? <div className="sign-in-page account-page">{children}</div> : <main className="sign-in-page">{children}</main>
}

function SignIn({ onSignedIn, googleClientId, initialMessage, googleConfigurationStatus,
  onGoogleConfigurationRetry }: {
  onSignedIn: (traveler: Traveler) => void; googleClientId: string; initialMessage: string
  googleConfigurationStatus: 'loading' | 'ready' | 'failed'; onGoogleConfigurationRetry?: () => void
}) {
  const [message, setMessage] = useState(initialMessage)
  const [submitting, setSubmitting] = useState(false)
  const [linkCredential, setLinkCredential] = useState<string | null>(null)
  const inProgress = useRef(false)
  const mounted = useRef(false)
  useEffect(() => {
    mounted.current = true
    return () => { mounted.current = false }
  }, [])
  const googleCredential = useCallback(async (credential: string) => {
    if (inProgress.current || !mounted.current) return
    inProgress.current = true
    setSubmitting(true)
    setMessage('Signing in with Google…')
    try {
      const profile = await signInWithGoogle(credential)
      if (mounted.current) onSignedIn(profile)
    } catch (error) {
      if (mounted.current && error instanceof GoogleLinkRequiredError) {
        setLinkCredential(credential)
        setMessage('')
        return
      }
      if (mounted.current) setMessage(error instanceof Error && error.name === 'Error'
        ? error.message : 'Could not complete Google sign-in. Please try again.')
    } finally {
      inProgress.current = false
      if (mounted.current) setSubmitting(false)
    }
  }, [onSignedIn])

  if (linkCredential) return <GoogleAccountLink credential={linkCredential}
    onSignedIn={traveler => { setLinkCredential(null); onSignedIn(traveler) }}
    onCancel={() => { setLinkCredential(null); setMessage('') }} />

  return (
    <section className="sign-in-card" aria-labelledby="sign-in-heading">
      <p className="eyebrow">Sign in</p>
      <h1 id="sign-in-heading">Welcome back</h1>
      <p className="intro">Pick up your application where you left it.</p>

      <GoogleSignIn clientId={googleClientId} disabled={submitting}
        configurationStatus={googleConfigurationStatus} onConfigurationRetry={onGoogleConfigurationRetry}
        onCredential={googleCredential} onMessage={setMessage} />

      <div className="divider"><span>or with email</span></div>

      <form
        aria-busy={submitting}
        onSubmit={async (event) => {
          event.preventDefault()
          if (inProgress.current) return
          const form = event.currentTarget
          const fields = new FormData(form)
          inProgress.current = true
          setSubmitting(true)
          setMessage('')
          try {
            const profile = await signIn(String(fields.get('email') ?? ''), String(fields.get('password') ?? ''))
            onSignedIn(profile)
          } catch (error) {
            setMessage(error instanceof Error && error.name === 'Error'
              ? error.message : 'Could not sign in. Check your connection and try again.')
          } finally {
            const password = form.elements.namedItem('password')
            if (password instanceof HTMLInputElement) password.value = ''
            inProgress.current = false
            setSubmitting(false)
          }
        }}
      >
        <label htmlFor="email">Email</label>
        <input
          id="email"
          name="email"
          type="email"
          autoComplete="email"
          disabled={submitting}
          placeholder="you@example.com"
          required
        />

        <label htmlFor="password">Password</label>
        <input
          id="password"
          name="password"
          type="password"
          autoComplete="current-password"
          disabled={submitting}
          required
        />

        <a
          className="forgot-password"
          data-activity-id="forgot-password-link"
          href="#forgot-password"
        >
          Forgot your password?
        </a>

        <button className="primary-button" data-activity-id="submit-login" type="submit" disabled={submitting}>{submitting ? 'Signing in…' : 'Sign in'}</button>
      </form>

      <p className="create-account">
        New to Taasheera?{' '}
        <a href="#create-account" data-activity-id="create-account-link">
          Create an account
        </a>
      </p>

      <p className="auth-status" id="auth-status" role="status">{message}</p>
    </section>
  )
}

export default App
