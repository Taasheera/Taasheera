import http from 'k6/http'
import { check, group, sleep } from 'k6'
import { THINK } from './config.js'
import { anonymousGet } from './client.js'

const SPECIMEN_JPEG = open('../fixtures/specimen-passport.jpg', 'b')
const SPECIMEN_PDF = open('../fixtures/synthetic-booking.pdf', 'b')
// (destination, visa_type) as the questionnaires define them; Schengen twice, as most travelers pick it.
const DESTINATIONS = [['SCHENGEN', 'short_stay'], ['SCHENGEN', 'short_stay'], ['GBR', 'standard_visitor'], ['USA', 'b2_tourist'],
  ['JPN', 'short_term_stay'], ['CAN', 'visitor'], ['TUR', 'e_visa'], ['IND', 'tourist'], ['CHN', 'tourist_l']]

function pause(scale = 1) {
  if (THINK > 0) sleep(THINK * scale * (0.5 + Math.random()))
}

function isoDay(offsetDays) {
  return new Date(Date.now() + offsetDays * 86400000).toISOString().slice(0, 10)
}

function addDays(day, offset) {
  return new Date(Date.parse(`${day}T00:00:00Z`) + offset * 86400000).toISOString().slice(0, 10)
}

/** A synthetic answer of the right shape. `attempt` walks through alternatives after a 422. */
export function answerFor(question, attempt) {
  switch (question.type) {
    case 'choice': return question.options[attempt % question.options.length].value
    case 'yes_no': return attempt % 2 === 1
    case 'text': return 'Synthetic load-test answer for capacity measurement.'.slice(0, Math.max(1, question.max_length || 40))
    case 'country': return ['FR', 'DE', 'IT', 'ES'][attempt % 4]
    case 'phone': return '+33612345678'
    case 'address': return { line1: '1 Synthetic Street', city: 'Paris', country: 'FR' }
    case 'passport': return { surname: 'SPECIMEN', given_names: 'LOAD TEST', document_number: `LT${String(__VU).padStart(7, '0')}`,
      nationality: 'LBN', issuing_country: 'LBN', birth_date: '1990-01-01', sex: 'X', expiry_date: isoDay(3650), personal_number: '' }
    case 'date': {
      const { date_min: earliest, date_max: latest } = question
      if (earliest && latest) return attempt ? latest : earliest
      if (earliest) return addDays(earliest, 7 + attempt * 7)
      if (latest) return addDays(latest, -(attempt ? 365 * 3 : 365 * 30))
      return attempt ? isoDay(-365 * 3) : isoDay(45)
    }
    default: return null
  }
}

/** Public pages: the home page, its script, feature flags and the visa guides (no sign-in). */
export function browse() {
  group('browse', () => {
    const home = anonymousGet('/', 'GET / (page)')
    const script = /src="(\/assets\/[^"]+\.js)"/.exec(home.body || '')
    if (script) anonymousGet(script[1], 'GET /assets/*.js')
    anonymousGet('/features', 'GET /features')
    pause(0.5)
    const guides = anonymousGet('/visa-guides', 'GET /visa-guides')
    const list = guides.status === 200 ? guides.json() : []
    if (list.length) {
      const guide = list[Math.floor(Math.random() * list.length)]
      anonymousGet(`/visa-guides/${guide.id}`, 'GET /visa-guides/:id')
    }
    pause()
  })
}

/** Signed-in account pages: who am I, profile, bell, sessions, preferences (Hariri's area). */
export function account(visitor) {
  group('account', () => {
    if (!visitor.ensureSession()) return
    visitor.get('/auth/me', 'GET /auth/me')
    visitor.get('/notifications/unread-count', 'GET /notifications/unread-count')
    pause(0.5)
    visitor.get('/profile', 'GET /profile')
    visitor.get('/notifications', 'GET /notifications')
    pause(0.5)
    visitor.get('/sessions', 'GET /sessions')
    visitor.get('/profile/notification-preferences', 'GET /profile/notification-preferences')
    if (Math.random() < 0.2) {
      visitor.put('/profile', { phone: '+33612345678', address_line1: '1 Synthetic Street', address_city: 'Paris',
        address_country: 'FR' }, 'PUT /profile')
    }
    pause()
  })
}

/** Dashboard → start or open an application → answer questions → completeness check (Dani's area). */
export function journey(visitor) {
  group('journey', () => {
    if (!visitor.ensureSession()) return
    const listing = visitor.get('/applications', 'GET /applications')
    const existing = listing.status === 200 ? listing.json().filter(item => item.status === 'DRAFT') : []
    pause(0.5)
    let state
    if (!existing.length || Math.random() < 0.25) {
      const [destination, visaType] = DESTINATIONS[Math.floor(Math.random() * DESTINATIONS.length)]
      const created = visitor.post('/applications', { destination, visa_type: visaType }, 'POST /applications')
      if (created.status !== 201) return
      state = created.json()
    } else {
      const chosen = existing[Math.floor(Math.random() * existing.length)]
      const opened = visitor.get(`/applications/${chosen.id}`, 'GET /applications/:id')
      if (opened.status !== 200) return
      state = opened.json()
    }
    // A person answers a handful of questions per sitting, pausing to read each one.
    for (let asked = 0; asked < 6 && state.current; asked += 1) {
      pause(0.6)
      let saved = null
      for (let attempt = 0; attempt < 3 && !saved; attempt += 1) {
        const reply = visitor.put(`/applications/${state.id}/answers/${state.current.id}`,
          { value: answerFor(state.current, attempt) }, 'PUT /applications/:id/answers/:question')
        if (reply.status === 200) saved = reply.json()
      }
      if (!saved) break
      state = saved
    }
    pause(0.5)
    const report = visitor.get(`/applications/${state.id}/check`, 'GET /applications/:id/check')
    check(report, { 'completeness check answered': r => r.status === 200 })
    visitor.get(`/applications/${state.id}/timeline`, 'GET /applications/:id/timeline')
    pause()
  })
}

/** Supporting documents: checklist, upload a small synthetic PDF, packet now and then. */
export function documents(visitor) {
  group('documents', () => {
    if (!visitor.ensureSession()) return
    const listing = visitor.get('/applications', 'GET /applications')
    const drafts = listing.status === 200 ? listing.json() : []
    if (!drafts.length) return journey(visitor)
    const application = drafts[Math.floor(Math.random() * drafts.length)]
    const checklist = visitor.get(`/applications/${application.id}/documents`, 'GET /applications/:id/documents')
    if (checklist.status !== 200) return
    const requirements = (checklist.json('requirements') || []).map(item => item.id).filter(Boolean)
    pause(0.5)
    if (requirements.length) {
      visitor.post(`/applications/${application.id}/documents`, {
        requirement: requirements[Math.floor(Math.random() * requirements.length)],
        file: http.file(SPECIMEN_PDF, 'synthetic-booking.pdf', 'application/pdf'),
      }, 'POST /applications/:id/documents')
    }
    if (Math.random() < 0.3) visitor.get(`/applications/${application.id}/documents/packet`, 'GET /applications/:id/documents/packet')
    pause()
  })
}

/** The expensive work, rarer: passport upload with OCR, and the assistant (Zeid's area). */
export function heavy(visitor) {
  group('heavy', () => {
    if (!visitor.ensureSession()) return
    if (Math.random() < 0.5) {
      const upload = visitor.post('/passports', { file: http.file(SPECIMEN_JPEG, 'specimen.jpg', 'image/jpeg') }, 'POST /passports')
      if (upload.status === 201) {
        pause(0.3)
        visitor.post(`/passports/${upload.json('id')}/extract`, null, 'POST /passports/:id/extract')
      }
    } else {
      visitor.post('/assistant/messages', { message: 'What documents do I need for a short tourist visit?', step: 'general', history: [] },
        'POST /assistant/messages')
    }
    pause()
  })
}
