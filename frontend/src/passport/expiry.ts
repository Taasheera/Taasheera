// Most embassies want the passport valid for three to six months after the trip, so warn early.
export function expiryWarning(expiry: string | null | undefined, today = new Date()): { tone: 'danger' | 'warn'; text: string } | null {
  if (!expiry || !/^\d{4}-\d{2}-\d{2}$/.test(expiry)) return null
  const todayIso = today.toISOString().slice(0, 10)
  if (expiry < todayIso) return { tone: 'danger', text: 'This passport has expired. Embassies will not accept it, so renew it before you apply.' }
  const sixMonths = new Date(today)
  sixMonths.setMonth(sixMonths.getMonth() + 6)
  if (expiry < sixMonths.toISOString().slice(0, 10)) {
    return { tone: 'warn', text: 'This passport expires within six months. Many embassies need it valid for three to six months after your trip.' }
  }
  return null
}
