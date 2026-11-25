import { useEffect, useId, useRef, useState } from 'react'
import { MicrophoneIcon } from '../icons'

type Recognition = {
  lang: string
  onresult: ((event: { results: ArrayLike<ArrayLike<{ transcript: string }>> }) => void) | null
  onend: (() => void) | null
  onerror: (() => void) | null
  start: () => void
  abort: () => void
}
type SpeechWindow = Window & {
  SpeechRecognition?: new () => Recognition
  webkitSpeechRecognition?: new () => Recognition
}

export default function VoiceDraft({ onText, disabled }: { onText: (text: string) => void; disabled: boolean }) {
  const { SpeechRecognition, webkitSpeechRecognition } = window as SpeechWindow
  const Recognition = SpeechRecognition ?? webkitSpeechRecognition
  const current = useRef<Recognition | null>(null)
  const [listening, setListening] = useState(false)
  const [error, setError] = useState('')
  const hintId = useId()

  useEffect(() => () => {
    if (current.current) {
      current.current.onresult = null
      current.current.onend = null
      current.current.onerror = null
      current.current.abort()
    }
  }, [])

  if (!Recognition) return null

  function start() {
    if (current.current) {
      current.current.onresult = null
      current.current.abort()
      return
    }
    if (!Recognition) return
    const recognition = new Recognition()
    recognition.lang = document.documentElement.lang || navigator.language
    recognition.onresult = event => {
      const text = Array.from(event.results, result => result[0]?.transcript ?? '').join(' ').trim()
      if (text) onText(text)
    }
    recognition.onerror = () => setError('Dictation is unavailable. Check microphone access or type your question.')
    recognition.onend = () => {
      current.current = null
      setListening(false)
    }
    try {
      setError('')
      recognition.start()
      current.current = recognition
      setListening(true)
    } catch {
      setError('Could not start dictation. You can still type your question.')
    }
  }

  return <div className="voice-draft">
    <button type="button" className="button button-secondary assistant-icon-button" aria-pressed={listening}
      aria-describedby={hintId} disabled={disabled && !listening} onClick={start}>
      <MicrophoneIcon />
      <span className="visually-hidden">{listening ? 'Stop dictation' : 'Dictate'}</span>
    </button>
    <span className="visually-hidden" id={hintId}>Your browser processes audio. Review the text before sending.</span>
    {error && <span role="status">{error}</span>}
  </div>
}
