import { useLanguage } from './language'
import './language.css'

// One button that names the other language in that language, so either reader can find it.
export default function LanguageToggle() {
  const { language, canSwitch, choose } = useLanguage()
  if (!canSwitch) return null
  const next = language === 'ar' ? 'en' : 'ar'
  return (
    <button type="button" className="language-toggle" lang={next} onClick={() => choose(next)}>
      {next === 'ar' ? 'العربية' : 'English'}
    </button>
  )
}
