import canadaFlag from 'flag-icons/flags/4x3/ca.svg'
import chinaFlag from 'flag-icons/flags/4x3/cn.svg'
import euFlag from 'flag-icons/flags/4x3/eu.svg'
import indiaFlag from 'flag-icons/flags/4x3/in.svg'
import japanFlag from 'flag-icons/flags/4x3/jp.svg'
import turkiyeFlag from 'flag-icons/flags/4x3/tr.svg'
import ukFlag from 'flag-icons/flags/4x3/gb.svg'
import usFlag from 'flag-icons/flags/4x3/us.svg'
import type { Text } from './i18n/en'

// The homepage destinations: flag and guide. Their names, and the rest of the homepage and FAQ
// copy, are in i18n/en.ts and i18n/ar.ts (CMPS271-16).
// Flags are bundled SVGs because Windows has no flag emoji.

export const destinations: { id: keyof Text['destinations']; flag: string; guide: string }[] = [
  { id: 'schengen', flag: euFlag, guide: 'schengen-short-stay' },
  { id: 'uk', flag: ukFlag, guide: 'uk-standard-visitor' },
  { id: 'us', flag: usFlag, guide: 'us-b2-tourist' },
  { id: 'canada', flag: canadaFlag, guide: 'canada-visitor' },
  { id: 'japan', flag: japanFlag, guide: 'japan-short-stay' },
  { id: 'china', flag: chinaFlag, guide: 'china-tourist-l' },
  { id: 'india', flag: indiaFlag, guide: 'india-tourist' },
  { id: 'turkiye', flag: turkiyeFlag, guide: 'turkiye-evisa' },
]
