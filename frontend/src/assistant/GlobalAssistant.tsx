import { useState } from 'react'
import { useLocation } from 'react-router'
import AssistantPanel from './AssistantPanel'

// The assistant button on every signed-in page (US 23). The passport and application pages
// have their own assistant with that page's context, so the layout leaves those out.
// On an application's detail page the same button talks about that application.
export default function GlobalAssistant() {
  const [open, setOpen] = useState(false)
  const applicationId = /^\/dashboard\/([0-9a-f]{32})\/?$/.exec(useLocation().pathname)?.[1]
  return <AssistantPanel key={applicationId ?? 'general'} step="general" open={open} onOpenChange={setOpen}
    applicationId={applicationId} />
}
