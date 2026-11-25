import { useState } from 'react'
import { useInRouterContext, useLocation } from 'react-router'
import AssistantPanel from './AssistantPanel'

function Assistant({ applicationId }: { applicationId?: string }) {
  const [open, setOpen] = useState(false)
  return <AssistantPanel step="general" open={open} onOpenChange={setOpen} applicationId={applicationId} />
}

// On an application's detail page the same button talks about that application.
function RoutedAssistant() {
  const applicationId = /^\/dashboard\/([0-9a-f]{32})\/?$/.exec(useLocation().pathname)?.[1]
  return <Assistant key={applicationId ?? 'general'} applicationId={applicationId} />
}

// The assistant button on every signed-in page (US 23). The passport and application pages
// have their own assistant with that page's context, so the layout leaves those out.
// Some shells (the account pages) mount it outside the router, where there is no application page.
export default function GlobalAssistant() {
  return useInRouterContext() ? <RoutedAssistant /> : <Assistant />
}
