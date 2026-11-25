import canadaFlag from 'flag-icons/flags/4x3/ca.svg'
import euFlag from 'flag-icons/flags/4x3/eu.svg'
import ukFlag from 'flag-icons/flags/4x3/gb.svg'
import usFlag from 'flag-icons/flags/4x3/us.svg'

// Homepage and FAQ copy lives here so the pages stay about layout.
// Flags are bundled SVGs because Windows has no flag emoji.

export const destinations = [
  { id: 'schengen', flag: euFlag, name: 'Schengen area', visa: 'Short-stay tourist visa', guide: 'schengen-short-stay' },
  { id: 'uk', flag: ukFlag, name: 'United Kingdom', visa: 'Standard Visitor visa', guide: 'uk-standard-visitor' },
  { id: 'us', flag: usFlag, name: 'United States', visa: 'B-2 tourist visa', guide: 'us-b2-tourist' },
  { id: 'canada', flag: canadaFlag, name: 'Canada', visa: 'Visitor visa', guide: 'canada-visitor' },
]

export const trustBadges = [
  'You control submission',
  'Passport data stays private',
  'Not affiliated with any embassy',
]

export const steps = [
  { title: 'Pick your destination', body: 'Choose a destination and visa type to start the matching questionnaire.' },
  { title: 'Upload your passport', body: 'We read your details from the passport. You check and confirm them.' },
  { title: 'Answer a few questions', body: 'Tell us about your trip. Your answers are saved as you go.' },
  { title: 'Review and submit', body: 'Check your answers and follow the instructions from your consulate.' },
]

export const whatWeDo = [
  'Read passport details so you can check them before saving',
  'Ask the questions that apply to your trip',
  'Flag missing answers and inconsistencies before review',
  'Save your application so you can pick up where you stopped',
]

export const requiredDocuments = [
  'Your current passport',
  'A recent passport photo',
  'Bank statements or other proof of funds',
  'Flight and hotel reservations',
  'A letter from your employer or university',
  'Travel insurance (for Schengen visas)',
]

export const privacyPoints = [
  { title: 'Private storage', body: 'Uploaded files are stored privately and access to them is restricted to your account.' },
  { title: 'Location data removed', body: 'Photos are cleaned when you upload them, so hidden location data is removed.' },
  { title: 'You stay in control', body: 'You review your saved information and submit through the embassy’s process.' },
]

export const faqs = [
  {
    question: 'What does Taasheera do?',
    answer: 'Taasheera reads your passport, asks questions about your trip and checks your saved answers for missing or inconsistent information. You review the details before taking them to the embassy.',
  },
  {
    question: 'Will anything be submitted without my approval?',
    answer: 'You decide when to submit. Taasheera prepares your information for review, and you submit it through your consulate’s process.',
  },
  {
    question: 'Which destinations can I apply for?',
    answer: 'Guided questionnaires are available for Schengen short stays, the UK Standard Visitor visa, the US B-2 visa and the Canadian visitor visa.',
  },
  {
    question: 'How do I use my application at the embassy?',
    answer: 'Follow the instructions from your consulate. When PDF preparation is available for your Schengen application, you can compare each prefilled value with your saved answers, download the form, then complete and sign it.',
  },
  {
    question: 'How do you read my passport?',
    answer: 'We read the machine-readable zone, the two lines of text at the bottom of your passport\'s photo page, and verify it with its built-in check digits. Some mistakes can still slip through, so we always ask you to confirm each field.',
  },
  {
    question: 'What if my passport photo can\'t be read?',
    answer: 'Retake the photo in even light with the entire passport page visible, or type the details by hand in the application.',
  },
  {
    question: 'Is my passport data safe?',
    answer: 'Your passport and documents are stored privately and are only ever available to your account. Photos are re-encoded when you upload them, which removes hidden location data. Sign-in tokens are never kept in your browser\'s storage.',
  },
  {
    question: 'Do I still need to go to the embassy?',
    answer: 'Your consulate may ask for an interview or fingerprints in person. Check its instructions for how to book and what to bring.',
  },
  {
    question: 'Does Taasheera guarantee my visa?',
    answer: 'Only the embassy or consulate decides whether to issue a visa. Our checks help you catch missing or inconsistent information before you submit.',
  },
  {
    question: 'Is this legal or immigration advice?',
    answer: 'No. Taasheera is a preparation tool, not a lawyer or an immigration adviser. For complex cases, such as a previous refusal or overstay, speak to a qualified adviser or the embassy directly.',
  },
]
