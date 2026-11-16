import canadaFlag from 'flag-icons/flags/4x3/ca.svg'
import euFlag from 'flag-icons/flags/4x3/eu.svg'
import ukFlag from 'flag-icons/flags/4x3/gb.svg'
import usFlag from 'flag-icons/flags/4x3/us.svg'

// Bundled SVGs because Windows has no flag emoji (as on the homepage).
const flags: Record<string, string> = { SCHENGEN: euFlag, GBR: ukFlag, USA: usFlag, CAN: canadaFlag }

export function destinationFlag(destination: string): string | null {
  return flags[destination] ?? null
}
