import type { AnswerType, AnswerValue, ApplicationSummary, CheckReport, Option, Timeline } from '../application/api'
import { adminRequest, json, type Role } from './api'

export const supportStatuses = ['open', 'in_progress', 'resolved'] as const
export type SupportStatus = typeof supportStatuses[number]
export const supportStatusLabels: Record<SupportStatus, string> = { open: 'Open', in_progress: 'In progress', resolved: 'Resolved' }

export type StaffProfile = { id: number; name: string; email: string; role: Role }

export type RequestSummary = {
  id: number
  traveler_name: string
  source: 'website' | 'telegram'
  status: SupportStatus
  excerpt: string
  application_id: string | null
  assignee_name: string | null
  assigned_to_me: boolean
  created_at: string
  updated_at: string
}

export type RequestDetail = RequestSummary & {
  message: string
  reply: string | null
  notes: string | null
  replied_at: string | null
  resolved_at: string | null
  access_expires_at: string | null
}

export type RequestPage = { items: RequestSummary[]; total: number; page: number; page_size: number }

export type SharedApplication = { application_id: string; title: string; traveler_name: string; expires_at: string }

export type StaffAnswer = {
  question_id: string
  step: string
  prompt: string
  type: AnswerType
  options: Option[] | null
  value: AnswerValue
  source: string
}

export type StaffApplicationView = {
  application: ApplicationSummary
  traveler_name: string
  access_expires_at: string
  answers: StaffAnswer[]
  check: CheckReport
  timeline: Timeline
}

export const SUPPORT_PAGE_SIZE = 20

export function getStaffMe() {
  return adminRequest<StaffProfile>('/support/me')
}

export function listRequests({ status = '', mine = false, page = 1 }: { status?: SupportStatus | ''; mine?: boolean; page?: number }) {
  const params = new URLSearchParams()
  if (status) params.set('status', status)
  if (mine) params.set('mine', 'true')
  params.set('page', String(page))
  params.set('page_size', String(SUPPORT_PAGE_SIZE))
  return adminRequest<RequestPage>(`/support/requests?${params}`)
}

export function getRequest(id: number) {
  return adminRequest<RequestDetail>(`/support/requests/${id}`)
}

export function assignToMe(id: number) {
  return adminRequest<RequestDetail>(`/support/requests/${id}/assign`, json('POST'))
}

export function sendReply(id: number, reply: string) {
  return adminRequest<RequestDetail>(`/support/requests/${id}/reply`, json('PUT', { reply }))
}

export function saveNotes(id: number, notes: string) {
  return adminRequest<RequestDetail>(`/support/requests/${id}/notes`, json('PUT', { notes }))
}

export function setRequestStatus(id: number, status: SupportStatus) {
  return adminRequest<RequestDetail>(`/support/requests/${id}/status`, json('PUT', { status }))
}

export function listShared() {
  return adminRequest<SharedApplication[]>('/support/shared')
}

export function getSharedApplication(id: string) {
  return adminRequest<StaffApplicationView>(`/support/applications/${id}`)
}
