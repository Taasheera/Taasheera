// The navigation panel's Assistant button opens whichever assistant the page shows: the general
// one, or the passport or application page's own. Every AssistantPanel listens for this event, so
// the panel needs no reference to the assistant and the assistant stays one component.
export const OPEN_ASSISTANT_EVENT = 'taasheera:open-assistant'

export function openAssistant() {
  window.dispatchEvent(new window.Event(OPEN_ASSISTANT_EVENT))
}
