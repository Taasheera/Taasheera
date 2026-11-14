// The traveler's sections in the navigation panel (US 15), in order. A section is current when the
// page's path is one of its prefixes or below one, so /apply/<id> is inside Applications.
// The notification bell (CMPS271-100) sits after these sections, outside the list.
export type Section = {
  id: 'applications' | 'documents' | 'account'
  label: string
  href: string
  prefixes: string[]
  // Only identifiers already on the activity allowlist (ActivityTracker and backend/app/activity.py).
  activityId?: string
}

export const sections: Section[] = [
  { id: 'applications', label: 'Applications', href: '/dashboard', prefixes: ['/dashboard', '/apply'], activityId: 'dashboard-link' },
  { id: 'documents', label: 'Passport & documents', href: '/passport', prefixes: ['/passport'], activityId: 'passport-link' },
  { id: 'account', label: 'Account', href: '/account', prefixes: ['/account'] },
]

export function currentSection(pathname: string): Section | null {
  const path = pathname.replace(/\/+$/, '') || '/'
  return sections.find(section => section.prefixes.some(prefix => path === prefix || path.startsWith(`${prefix}/`))) ?? null
}
