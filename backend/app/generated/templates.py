"""Write a cover letter, an itinerary and letter templates from an application's answers (CMPS271-57/59/60/61/62).

Everything here is deterministic: no AI call, so the same answers always give the same text, it works without
a Gemini key, and no traveler data leaves the server. Only confirmed answers are used; a fact the traveler
hasn't given is left out (cover letter, itinerary) or left as a [bracketed blank] for the signer to fill in
(letters other people sign). The cover letter and itinerary also come in the embassy's language where it
isn't English (CMPS271-60): the wording is fixed, translated text, and the traveler's own words (addresses,
cities, host details) stay as they typed them.
"""
from dataclasses import dataclass, field
from datetime import date, timedelta
import re
from typing import Any, Literal

from ..applications import flow
from ..applications.form_wording import OCCUPATION_ON_FORMS

Kind = Literal["cover_letter", "itinerary", "invitation_letter", "sponsor_letter", "employment_letter"]
Language = Literal["en", "fr", "de", "es", "it"]

KINDS: dict[str, str] = {
    "cover_letter": "Cover letter",
    "itinerary": "Day-by-day itinerary",
    "invitation_letter": "Invitation letter (for your host to sign)",
    "sponsor_letter": "Sponsorship letter (for your sponsor to sign)",
    "employment_letter": "Employment and leave letter (for your employer to sign)",
}
# Documents the traveler writes and signs, offered in the embassy's language as well (CMPS271-60).
TRANSLATED = ("cover_letter", "itinerary")
LANGUAGES: dict[str, str] = {"en": "English", "fr": "Français", "de": "Deutsch", "es": "Español", "it": "Italiano"}

# Schengen countries whose consulates work in a language we translate into. Others read English.
SCHENGEN_LANGUAGE = {"FRA": "fr", "BEL": "fr", "LUX": "fr", "DEU": "de", "AUT": "de", "CHE": "de", "LIE": "de",
                     "ESP": "es", "ITA": "it"}
# Where the letter goes, and "to <country>" in each language, for the countries above.
_SCHENGEN_PLACES: dict[str, dict[str, tuple[str, str]]] = {
    "fr": {"FRA": ("en France", "Ambassade ou consulat de France"),
           "BEL": ("en Belgique", "Ambassade ou consulat de Belgique"),
           "LUX": ("au Luxembourg", "Ambassade ou consulat du Luxembourg")},
    "de": {"DEU": ("nach Deutschland", "Botschaft oder Konsulat der Bundesrepublik Deutschland"),
           "AUT": ("nach Österreich", "Österreichische Botschaft oder Konsulat"),
           "CHE": ("in die Schweiz", "Schweizerische Botschaft oder Konsulat"),
           "LIE": ("nach Liechtenstein", "Schweizerische Botschaft oder Konsulat (Vertretung Liechtensteins)")},
    "es": {"ESP": ("a España", "Embajada o Consulado de España")},
    "it": {"ITA": ("in Italia", "Ambasciata o Consolato d'Italia")},
}
_CANADA_FR = ("au Canada", "Immigration, Réfugiés et Citoyenneté Canada")
_ENGLISH_ADDRESSEE = {
    "GBR": "UK Visas and Immigration",
    "USA": "Consular Section, Embassy of the United States of America",
    "CAN": "Immigration, Refugees and Citizenship Canada",
    "JPN": "Visa Section, Embassy of Japan",
    "CHN": "Visa Office, Embassy of the People's Republic of China",
    "IND": "Consular Section, Embassy or High Commission of India",
    "TUR": "Consular Section, Embassy of the Republic of Türkiye",
}

_MONTHS = {
    "en": ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
           "November", "December"],
    "fr": ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre",
           "novembre", "décembre"],
    "de": ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober",
           "November", "Dezember"],
    "es": ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre",
           "noviembre", "diciembre"],
    "it": ["gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno", "luglio", "agosto", "settembre", "ottobre",
           "novembre", "dicembre"],
}
_WEEKDAYS = {
    "en": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"],
    "fr": ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"],
    "de": ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"],
    "es": ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"],
    "it": ["lunedì", "martedì", "mercoledì", "giovedì", "venerdì", "sabato", "domenica"],
}
_PURPOSES = {
    "en": {"tourism": "tourism", "family_friends": "visiting family and friends", "business": "business",
           "culture": "cultural activities", "sports": "a sports event", "official": "an official visit",
           "medical": "medical treatment", "study": "a short course of study", "yoga": "a yoga programme",
           "work_or_study": "a short visit", "other": "a private visit"},
    "fr": {"tourism": "tourisme", "family_friends": "visite familiale ou amicale", "business": "affaires",
           "culture": "activités culturelles", "sports": "manifestation sportive", "official": "visite officielle",
           "medical": "raisons médicales", "study": "études de courte durée", "other": "visite privée"},
    "de": {"tourism": "Tourismus", "family_friends": "Besuch bei Familie oder Freunden", "business": "Geschäftsreise",
           "culture": "Kultur", "sports": "Sportveranstaltung", "official": "offizieller Besuch",
           "medical": "medizinische Behandlung", "study": "kurzer Studienaufenthalt", "other": "privater Besuch"},
    "es": {"tourism": "turismo", "family_friends": "visita a familiares o amigos", "business": "negocios",
           "culture": "actividades culturales", "sports": "evento deportivo", "official": "visita oficial",
           "medical": "tratamiento médico", "study": "estudios de corta duración", "other": "visita privada"},
    "it": {"tourism": "turismo", "family_friends": "visita a familiari o amici", "business": "affari",
           "culture": "attività culturali", "sports": "evento sportivo", "official": "visita ufficiale",
           "medical": "cure mediche", "study": "breve periodo di studio", "other": "visita privata"},
}

# The cover letter's fixed sentences. {placeholders} are filled from the traveler's answers; a sentence whose
# fact is missing is left out rather than written with a gap.
_COVER = {
    "en": {
        "to": "To the {addressee}", "subject": "Subject: Application for a {visa}, {name}, passport {passport}",
        "dear": "Dear Sir or Madam,",
        "trip": "I am applying for a {visa} to visit {destination} for {purpose}, arriving on {arrival} and "
                "leaving on {departure} ({nights}).",
        "trip_no_dates": "I am applying for a {visa} to visit {destination} for {purpose}.",
        "cities": "During the trip I plan to visit {cities}.",
        "hotel": "I will stay at {stay}.", "host": "I will stay with my host: {stay}.",
        "employed": "I work as {job} at {employer}, and my employer has approved my leave for these dates.",
        "employed_no_employer": "I work as {job}.",
        "self_employed": "I am self-employed ({job}{at_employer}).",
        "student": "I am a student{at_school}.", "retired": "I am retired.",
        "self_funded": "I will pay for the trip myself, and my bank statements are enclosed.",
        "sponsored": "My trip is paid for by my sponsor: {sponsor}. Their letter and bank statements are enclosed.",
        "return": "I will return home{to_city} at the end of my visit, before the end of the period my visa allows.",
        "enclosed": "The supporting documents are enclosed. Please contact me at {contact} if you need anything "
                    "further.",
        "thanks": "Thank you for considering my application.", "close": "Yours faithfully,",
        "nights": ("{n} night", "{n} nights"),
    },
    "fr": {
        "to": "À l'attention de : {addressee}", "subject": "Objet : demande de visa ({visa}), {name}, passeport {passport}",
        "dear": "Madame, Monsieur,",
        "trip": "Je sollicite un visa afin de me rendre {destination} du {arrival} au {departure} ({nights}). "
                "Motif du voyage : {purpose}.",
        "trip_no_dates": "Je sollicite un visa afin de me rendre {destination}. Motif du voyage : {purpose}.",
        "cities": "Au cours de mon séjour, je prévois de visiter : {cities}.",
        "hotel": "Hébergement : {stay}.", "host": "Je serai hébergé(e) par : {stay}.",
        "employed": "J'exerce la profession de {job} chez {employer}, et mon employeur a approuvé mon congé pour ces dates.",
        "employed_no_employer": "J'exerce la profession de {job}.",
        "self_employed": "Je suis travailleur indépendant ({job}{at_employer}).",
        "student": "Je suis étudiant(e){at_school}.", "retired": "Je suis retraité(e).",
        "self_funded": "Je prendrai en charge moi-même les frais du voyage ; mes relevés bancaires sont joints.",
        "sponsored": "Les frais de mon voyage sont pris en charge par : {sponsor}. Sa lettre et ses relevés bancaires "
                     "sont joints.",
        "return": "Je rentrerai chez moi{to_city} à la fin de mon séjour, avant l'expiration de la durée autorisée "
                  "par mon visa.",
        "enclosed": "Vous trouverez ci-joint les pièces justificatives. Je reste à votre disposition à l'adresse "
                    "{contact} pour toute information complémentaire.",
        "thanks": "Je vous remercie de l'attention portée à ma demande.",
        "close": "Veuillez agréer, Madame, Monsieur, l'expression de mes salutations distinguées.",
        "nights": ("{n} nuit", "{n} nuits"),
    },
    "de": {
        "to": "An: {addressee}", "subject": "Betreff: Visumantrag ({visa}), {name}, Reisepass {passport}",
        "dear": "Sehr geehrte Damen und Herren,",
        "trip": "hiermit beantrage ich ein Visum für eine Reise {destination} vom {arrival} bis zum {departure} "
                "({nights}). Reisezweck: {purpose}.",
        "trip_no_dates": "hiermit beantrage ich ein Visum für eine Reise {destination}. Reisezweck: {purpose}.",
        "cities": "Während der Reise möchte ich folgende Orte besuchen: {cities}.",
        "hotel": "Unterkunft: {stay}.", "host": "Ich wohne bei meinem Gastgeber: {stay}.",
        "employed": "Ich bin als {job} bei {employer} beschäftigt; mein Arbeitgeber hat meinen Urlaub für diesen "
                    "Zeitraum genehmigt.",
        "employed_no_employer": "Ich bin als {job} beschäftigt.",
        "self_employed": "Ich bin selbstständig ({job}{at_employer}).",
        "student": "Ich bin Student(in){at_school}.", "retired": "Ich bin im Ruhestand.",
        "self_funded": "Die Reisekosten trage ich selbst; meine Kontoauszüge liegen bei.",
        "sponsored": "Die Reisekosten übernimmt: {sponsor}. Das Schreiben und die Kontoauszüge liegen bei.",
        "return": "Am Ende meines Aufenthalts kehre ich nach Hause zurück{to_city}, bevor die erlaubte "
                  "Aufenthaltsdauer endet.",
        "enclosed": "Die Unterlagen liegen bei. Für Rückfragen erreichen Sie mich unter {contact}.",
        "thanks": "Vielen Dank für die Prüfung meines Antrags.", "close": "Mit freundlichen Grüßen",
        "nights": ("{n} Nacht", "{n} Nächte"),
    },
    "es": {
        "to": "A la atención de: {addressee}", "subject": "Asunto: solicitud de visado ({visa}), {name}, pasaporte {passport}",
        "dear": "Estimados señores:",
        "trip": "Solicito un visado para viajar {destination} del {arrival} al {departure} ({nights}). "
                "Motivo del viaje: {purpose}.",
        "trip_no_dates": "Solicito un visado para viajar {destination}. Motivo del viaje: {purpose}.",
        "cities": "Durante el viaje tengo previsto visitar: {cities}.",
        "hotel": "Alojamiento: {stay}.", "host": "Me alojaré con mi anfitrión: {stay}.",
        "employed": "Trabajo como {job} en {employer}, y mi empleador ha aprobado mis vacaciones para estas fechas.",
        "employed_no_employer": "Trabajo como {job}.",
        "self_employed": "Soy trabajador(a) autónomo(a) ({job}{at_employer}).",
        "student": "Soy estudiante{at_school}.", "retired": "Estoy jubilado(a).",
        "self_funded": "Pagaré el viaje yo mismo(a); adjunto mis extractos bancarios.",
        "sponsored": "Los gastos del viaje los cubre: {sponsor}. Se adjuntan su carta y sus extractos bancarios.",
        "return": "Regresaré a mi domicilio{to_city} al final de mi estancia, antes de que termine el periodo "
                  "autorizado por el visado.",
        "enclosed": "Adjunto la documentación justificativa. Pueden contactarme en {contact} si necesitan más "
                    "información.",
        "thanks": "Agradezco de antemano la atención prestada a mi solicitud.", "close": "Atentamente,",
        "nights": ("{n} noche", "{n} noches"),
    },
    "it": {
        "to": "All'attenzione di: {addressee}", "subject": "Oggetto: domanda di visto ({visa}), {name}, passaporto {passport}",
        "dear": "Gentili Signore e Signori,",
        "trip": "chiedo il rilascio di un visto per un viaggio {destination} dal {arrival} al {departure} "
                "({nights}). Motivo del viaggio: {purpose}.",
        "trip_no_dates": "chiedo il rilascio di un visto per un viaggio {destination}. Motivo del viaggio: {purpose}.",
        "cities": "Durante il viaggio prevedo di visitare: {cities}.",
        "hotel": "Alloggio: {stay}.", "host": "Sarò ospite di: {stay}.",
        "employed": "Lavoro come {job} presso {employer}, e il mio datore di lavoro ha approvato le ferie per queste date.",
        "employed_no_employer": "Lavoro come {job}.",
        "self_employed": "Sono un lavoratore autonomo ({job}{at_employer}).",
        "student": "Sono uno studente{at_school}.", "retired": "Sono in pensione.",
        "self_funded": "Sosterrò personalmente le spese del viaggio; allego i miei estratti conto.",
        "sponsored": "Le spese del viaggio sono a carico di: {sponsor}. Allego la sua lettera e i suoi estratti conto.",
        "return": "Al termine del soggiorno tornerò a casa{to_city}, prima della scadenza del periodo consentito "
                  "dal visto.",
        "enclosed": "Allego la documentazione a supporto. Potete contattarmi a {contact} per qualsiasi ulteriore "
                    "informazione.",
        "thanks": "Ringrazio per l'attenzione dedicata alla mia domanda.", "close": "Distinti saluti,",
        "nights": ("{n} notte", "{n} notti"),
    },
}
_AT = {"en": (" at {x}", " in {x}"), "fr": (" chez {x}", " à {x}"), "de": (" bei {x}", " in {x}"),
       "es": (" en {x}", " en {x}"), "it": (" presso {x}", " a {x}")}

_ITINERARY = {
    "en": {"title": "Travel itinerary", "passport": "Passport", "trip": "{destination}: {arrival} to {departure} ({nights})",
           "day": "Day {n}", "arrive": "Arrive in {destination}. Check in{stay}.", "explore": "Explore {city}.",
           "travel": "Travel from {previous} to {city}.", "depart": "Check out and leave {destination}.",
           "overnight": "Overnight: {place}", "in_city": "accommodation in {city}", "stay_at": " at {stay}",
           "note": "Plans may change; flight and hotel reservations are enclosed."},
    "fr": {"title": "Programme de voyage", "passport": "Passeport", "trip": "{destination} : du {arrival} au {departure} ({nights})",
           "day": "Jour {n}", "arrive": "Arrivée {destination}. Installation{stay}.", "explore": "Journée à {city}.",
           "travel": "Trajet de {previous} à {city}.", "depart": "Départ {destination}.",
           "overnight": "Nuit : {place}", "in_city": "hébergement à {city}", "stay_at": " : {stay}",
           "note": "Programme indicatif ; les réservations de vol et d'hébergement sont jointes."},
    "de": {"title": "Reiseplan", "passport": "Reisepass", "trip": "{destination}: {arrival} bis {departure} ({nights})",
           "day": "Tag {n}", "arrive": "Ankunft {destination}. Check-in{stay}.", "explore": "Aufenthalt in {city}.",
           "travel": "Fahrt von {previous} nach {city}.", "depart": "Check-out und Abreise {destination}.",
           "overnight": "Übernachtung: {place}", "in_city": "Unterkunft in {city}", "stay_at": ": {stay}",
           "note": "Änderungen vorbehalten; Flug- und Hotelreservierungen liegen bei."},
    "es": {"title": "Itinerario de viaje", "passport": "Pasaporte", "trip": "{destination}: del {arrival} al {departure} ({nights})",
           "day": "Día {n}", "arrive": "Llegada {destination}. Registro{stay}.", "explore": "Día en {city}.",
           "travel": "Viaje de {previous} a {city}.", "depart": "Salida {destination}.",
           "overnight": "Noche: {place}", "in_city": "alojamiento en {city}", "stay_at": ": {stay}",
           "note": "Itinerario orientativo; se adjuntan las reservas de vuelo y alojamiento."},
    "it": {"title": "Itinerario di viaggio", "passport": "Passaporto", "trip": "{destination}: dal {arrival} al {departure} ({nights})",
           "day": "Giorno {n}", "arrive": "Arrivo {destination}. Check-in{stay}.", "explore": "Giornata a {city}.",
           "travel": "Trasferimento da {previous} a {city}.", "depart": "Check-out e partenza {destination}.",
           "overnight": "Pernottamento: {place}", "in_city": "alloggio a {city}", "stay_at": ": {stay}",
           "note": "Programma indicativo; si allegano le prenotazioni di volo e alloggio."},
}
# "from <country>" for the itinerary's last day, next to the "to <country>" phrases above.
_FROM = {"fr": {"FRA": "de France", "BEL": "de Belgique", "LUX": "du Luxembourg", "CAN": "du Canada"},
         "de": {"DEU": "aus Deutschland", "AUT": "aus Österreich", "CHE": "aus der Schweiz", "LIE": "aus Liechtenstein"},
         "es": {"ESP": "de España"}, "it": {"ITA": "dall'Italia"}}


@dataclass(frozen=True)
class Facts:
    """What the documents may say about the trip, taken only from the application's answers."""

    name: str
    passport_number: str
    email: str
    visa: str
    # The destination as English text ("France", "the United Kingdom") and the code that picks translations.
    destination: str
    place_code: str
    phone: str | None = None
    address: str | None = None
    home_city: str | None = None
    purpose: str | None = None
    arrival: date | None = None
    departure: date | None = None
    stay: str | None = None
    hosted: bool = False
    cities: tuple[str, ...] = ()
    occupation: str | None = None
    job: str | None = None
    employer: str | None = None
    school: str | None = None
    funding: str | None = None
    sponsor: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Generated:
    title: str
    body: str


def _text(answers: flow.Answers, *keys: str) -> str | None:
    for key in keys:
        value = answers.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _date(answers: flow.Answers, key: str) -> date | None:
    try:
        return date.fromisoformat(answers[key]) if isinstance(answers.get(key), str) else None
    except ValueError:
        return None


def _name(passport: dict) -> str:
    parts = [str(passport.get(key) or "").strip() for key in ("given_names", "surname")]
    # Passports print names in capitals; a letter reads better in name case ("MARIA-JOSE" -> "Maria-Jose").
    return " ".join(part.title() for part in parts if part)


def _cities(text: str | None) -> tuple[str, ...]:
    if not text:
        return ()
    parts = re.split(r"\s*(?:,|;|/|\n|\band\b|\bthen\b|->|→)\s*", text)
    return tuple(dict.fromkeys(part.strip(" .") for part in parts if part.strip(" .")))[:10]


def _destination(questionnaire: flow.Questionnaire, answers: flow.Answers) -> tuple[str, str]:
    if questionnaire.destination == "SCHENGEN" and answers.get("main_destination"):
        code = answers["main_destination"]
        label = flow.display_value(questionnaire.question("main_destination"), code)
        return label, code
    labels = {"GBR": "the United Kingdom", "USA": "the United States", "CAN": "Canada", "JPN": "Japan",
              "CHN": "China", "IND": "India", "TUR": "Türkiye"}
    return labels.get(questionnaire.destination, questionnaire.destination_label), questionnaire.destination


def facts(questionnaire: flow.Questionnaire, answers: flow.Answers, email: str) -> Facts:
    """The facts the documents use, from answers already reconciled by the engine."""
    passport = answers.get("passport") if isinstance(answers.get("passport"), dict) else {}
    address = answers.get("home_address") if isinstance(answers.get("home_address"), dict) else {}
    destination, code = _destination(questionnaire, answers)
    hosted = answers.get("accommodation") == "host" or answers.get("host") is True
    stay = _text(answers, "host_details") if hosted else _text(answers, "accommodation_details", "canada_address",
                                                               "uk_address", "us_address")
    occupation = answers.get("occupation") or answers.get("employment")
    funding = answers.get("funding")
    cities = _cities(_text(answers, "itinerary_cities", "places_to_visit"))
    return Facts(
        name=_name(passport) or "[Your full name]",
        passport_number=str(passport.get("document_number") or "[passport number]"),
        email=email, visa=questionnaire.visa_label, destination=destination, place_code=code,
        phone=_text(answers, "phone"),
        address=", ".join(str(address[key]) for key in ("line1", "line2", "city", "postal_code") if address.get(key)) or None,
        home_city=str(address.get("city") or "").strip() or None,
        purpose=answers.get("purpose"),
        arrival=_date(answers, "arrival_date"), departure=_date(answers, "departure_date"),
        stay=stay, hosted=hosted and stay is not None, cities=cities,
        occupation=occupation, job=_text(answers, "job_title") or OCCUPATION_ON_FORMS.get(occupation or ""),
        employer=_text(answers, "employer"), school=_text(answers, "school"),
        funding=funding, sponsor=_text(answers, "sponsor_details", "payer_details"),
        extra={"host_details": _text(answers, "host_details")},
    )


def ready_for_packet(kind: str, f: Facts) -> bool:
    """Whether a document is finished enough to print for the embassy: no blanks where the traveler's own
    name or passport number goes, and an itinerary only once the trip has dates."""
    if not f.name or f.name.startswith("[") or f.passport_number.startswith("["):
        return False
    return kind != "itinerary" or bool(f.arrival and f.departure and f.departure >= f.arrival)


def embassy_languages(questionnaire: flow.Questionnaire, answers: flow.Answers) -> list[str]:
    """English first, then the embassy's own language when it is one we translate into."""
    if questionnaire.destination == "SCHENGEN":
        language = SCHENGEN_LANGUAGE.get(answers.get("main_destination") or "")
        return ["en", language] if language else ["en"]
    if questionnaire.destination == "CAN":
        return ["en", "fr"]
    return ["en"]


def available_kinds(questionnaire: flow.Questionnaire, answers: flow.Answers) -> list[str]:
    """Which documents to offer: a cover letter and itinerary always, a letter template when the
    destination asks this traveler for that letter (the questionnaire's own document list decides)."""
    kinds = ["cover_letter", "itinerary"]
    asked = {document.id for document in flow.required_documents(questionnaire, answers)}
    if "invitation_letter" in asked or (answers.get("accommodation") == "host" or answers.get("host") is True):
        kinds.append("invitation_letter")
    if "sponsor_letter" in asked or answers.get("funding") in ("sponsor", "family", "employer", "other"):
        kinds.append("sponsor_letter")
    if "employment_letter" in asked or (answers.get("occupation") or answers.get("employment")) in ("employed",):
        kinds.append("employment_letter")
    return kinds


def format_date(value: date, language: str) -> str:
    month = _MONTHS[language][value.month - 1]
    if language == "en":
        return f"{value.day} {month} {value.year}"
    if language == "de":
        return f"{value.day}. {month} {value.year}"
    return f"{value.day} {month} {value.year}"


def _nights(count: int, words: tuple[str, str]) -> str:
    return (words[0] if count == 1 else words[1]).format(n=count)


def _place(f: Facts, language: str) -> tuple[str, str, str]:
    """(to <destination>, the addressee, from <destination>) in `language`."""
    if language == "en":
        addressee = _ENGLISH_ADDRESSEE.get(f.place_code, f"Visa Section, Embassy or Consulate of {f.destination}")
        return f.destination, addressee, f.destination
    if f.place_code == "CAN" and language == "fr":
        return _CANADA_FR[0], _CANADA_FR[1], _FROM["fr"]["CAN"]
    to, addressee = _SCHENGEN_PLACES[language].get(f.place_code, (f.destination, f.destination))
    return to, addressee, _FROM[language].get(f.place_code, f.destination)


def cover_letter(f: Facts, language: str, today: date) -> Generated:
    t = _COVER[language]
    destination, addressee, _ = _place(f, language)
    purpose = _PURPOSES[language].get(f.purpose or "", _PURPOSES[language]["other"])
    header = [f.name]
    header += [line for line in (f.address, " · ".join(x for x in (f.phone, f.email) if x)) if line]
    lines = header + ["", format_date(today, language), "", t["to"].format(addressee=addressee), "",
                      t["subject"].format(visa=f.visa, name=f.name, passport=f.passport_number), "", t["dear"], ""]
    body: list[str] = []
    if f.arrival and f.departure:
        nights = _nights((f.departure - f.arrival).days, t["nights"])
        body.append(t["trip"].format(visa=f.visa, destination=destination, purpose=purpose, nights=nights,
                                     arrival=format_date(f.arrival, language),
                                     departure=format_date(f.departure, language)))
    else:
        body.append(t["trip_no_dates"].format(visa=f.visa, destination=destination, purpose=purpose))
    if f.cities:
        body[-1] += " " + t["cities"].format(cities=", ".join(f.cities))
    if f.stay:
        body[-1] += " " + t["host" if f.hosted else "hotel"].format(stay=f.stay)
    situation = []
    at_employer = _AT[language][0].format(x=f.employer) if f.employer else ""
    if f.occupation == "employed" and f.job:
        situation.append(t["employed"].format(job=f.job, employer=f.employer) if f.employer
                         else t["employed_no_employer"].format(job=f.job))
    elif f.occupation == "self_employed" and f.job:
        situation.append(t["self_employed"].format(job=f.job, at_employer=at_employer))
    elif f.occupation == "student":
        situation.append(t["student"].format(at_school=_AT[language][0].format(x=f.school) if f.school else ""))
    elif f.occupation == "retired":
        situation.append(t["retired"])
    if f.funding == "self":
        situation.append(t["self_funded"])
    elif f.funding and f.sponsor:
        situation.append(t["sponsored"].format(sponsor=f.sponsor))
    if situation:
        body.append(" ".join(situation))
    body.append(t["return"].format(to_city=_AT[language][1].format(x=f.home_city) if f.home_city else ""))
    body.append(t["enclosed"].format(contact=" / ".join(x for x in (f.email, f.phone) if x)))
    body.append(t["thanks"])
    lines += [paragraph for item in body for paragraph in (item, "")]
    lines += [t["close"], "", "", f.name]
    return Generated(title=_COVER_TITLES[language], body="\n".join(lines))


_COVER_TITLES = {"en": "Cover letter", "fr": "Lettre de motivation", "de": "Anschreiben",
                 "es": "Carta de presentación", "it": "Lettera di presentazione"}


def _day_cities(f: Facts) -> list[str]:
    """The city for each day of the trip, the cities spread evenly over the nights in the order given."""
    assert f.arrival and f.departure
    nights = max((f.departure - f.arrival).days, 0)
    cities = list(f.cities) or [f.destination]
    days = []
    for index in range(nights + 1):
        position = min(index * len(cities) // max(nights, 1), len(cities) - 1) if index < nights else len(cities) - 1
        days.append(cities[position])
    return days


def itinerary(f: Facts, language: str) -> Generated:
    t = _ITINERARY[language]
    nights_words = _COVER[language]["nights"]
    destination, _, leaving = _place(f, language)
    lines = [t["title"], f"{f.name} · {t['passport']} {f.passport_number}"]
    if not (f.arrival and f.departure) or f.departure < f.arrival:
        lines += ["", "Add your arrival and departure dates to the application to build the itinerary."]
        return Generated(title=t["title"], body="\n".join(lines))
    nights = (f.departure - f.arrival).days
    lines.append(t["trip"].format(destination=destination if language == "en" else f.destination,
                                  arrival=format_date(f.arrival, language), departure=format_date(f.departure, language),
                                  nights=_nights(nights, nights_words)))
    single_stay = len(set(_day_cities(f))) == 1 and f.stay
    stay_suffix = t["stay_at"].format(stay=f.stay) if single_stay else ""
    days = _day_cities(f)
    for index, city in enumerate(days):
        day = f.arrival + timedelta(days=index)
        weekday = _WEEKDAYS[language][day.weekday()]
        lines += ["", f"{t['day'].format(n=index + 1)} · {weekday} {format_date(day, language)} · {city}"]
        if index == 0:
            arrival_place = destination if language != "en" else f"{destination}" if destination == city else f"{city}, {destination}"
            lines.append(t["arrive"].format(destination=arrival_place, stay=stay_suffix))
        elif index == len(days) - 1 and index > 0:
            if city != days[index - 1]:
                lines.append(t["travel"].format(previous=days[index - 1], city=city))
            lines.append(t["depart"].format(destination=leaving))
        elif city != days[index - 1]:
            lines.append(t["travel"].format(previous=days[index - 1], city=city))
        else:
            lines.append(t["explore"].format(city=city))
        if index < len(days) - 1:
            place = f.stay if single_stay else t["in_city"].format(city=city)
            lines.append(t["overnight"].format(place=place))
    lines += ["", t["note"]]
    return Generated(title=t["title"], body="\n".join(lines))


def _trip_line(f: Facts) -> str:
    if f.arrival and f.departure:
        return f"from {format_date(f.arrival, 'en')} to {format_date(f.departure, 'en')}"
    return "from [arrival date] to [departure date]"


def invitation_letter(f: Facts, today: date) -> Generated:
    host = f.extra.get("host_details") or "[Host's full name and address]"
    body = "\n".join([
        "[Host's full name]", "[Host's address]", "[Host's phone and email]", "", format_date(today, "en"), "",
        f"To the {_place(f, 'en')[1]}", "", f"Subject: Letter of invitation for {f.name}", "", "Dear Sir or Madam,", "",
        f"I, [host's full name], born on [date of birth], [citizen / resident] of {f.destination} "
        f"([ID or residence permit number]), invite {f.name}, holder of passport {f.passport_number}, to stay with me "
        f"{_trip_line(f)}.",
        "",
        f"{f.name} is my [relationship, for example friend or cousin]. During the visit they will stay at my home: {host}. "
        "[I will / I will not] cover their accommodation and meals.",
        "",
        "A copy of my identity document or residence permit and proof of my address are enclosed. Please contact me "
        "if you need more information.",
        "", "Yours faithfully,", "", "", "[Signature]", "[Host's full name]",
    ])
    return Generated(title=KINDS["invitation_letter"], body=body)


def sponsor_letter(f: Facts, today: date) -> Generated:
    sponsor = f.sponsor or "[Sponsor's full name and relationship to the traveler]"
    body = "\n".join([
        "[Sponsor's full name]", "[Sponsor's address]", "[Sponsor's phone and email]", "", format_date(today, "en"), "",
        f"To the {_place(f, 'en')[1]}", "", f"Subject: Financial sponsorship for {f.name}", "", "Dear Sir or Madam,", "",
        f"I, [sponsor's full name], confirm that I will pay for the trip of {f.name}, holder of passport "
        f"{f.passport_number}, to {f.destination} {_trip_line(f)}.",
        "",
        f"Sponsor as given by the traveler: {sponsor}.",
        "",
        "I will cover their [travel, accommodation, meals and medical insurance]. My bank statements for the last "
        "[three] months and proof of my income are enclosed.",
        "", "Yours faithfully,", "", "", "[Signature]", "[Sponsor's full name]",
    ])
    return Generated(title=KINDS["sponsor_letter"], body=body)


def employment_letter(f: Facts, today: date) -> Generated:
    employer = f.employer or "[Company name]"
    job = f.job or "[job title]"
    resume = (f.departure + timedelta(days=1)) if f.departure else None
    body = "\n".join([
        f"[{employer} letterhead: address, phone, company registration number]", "", format_date(today, "en"), "",
        f"To the {_place(f, 'en')[1]}", "", f"Subject: Employment and leave of {f.name}", "", "Dear Sir or Madam,", "",
        f"This is to confirm that {f.name}, holder of passport {f.passport_number}, has been employed by {employer} "
        f"since [start date] as {job}, on a [permanent / fixed-term] contract. Their monthly salary is [amount and "
        "currency].",
        "",
        f"We have approved their leave {_trip_line(f)}, and they will resume work on "
        f"{format_date(resume, 'en') if resume else '[return date]'}. Their position will be kept for them during "
        "this time.",
        "",
        "Please contact us if you need any further information.",
        "", "Yours faithfully,", "", "", "[Signature and company stamp]", "[Name and position of the signer]",
        "[Phone and email]",
    ])
    return Generated(title=KINDS["employment_letter"], body=body)


def generate(kind: str, language: str, f: Facts, today: date) -> Generated:
    if kind == "cover_letter":
        return cover_letter(f, language, today)
    if kind == "itinerary":
        return itinerary(f, language)
    if language != "en":
        raise ValueError("Letter templates are in English only.")
    return {"invitation_letter": invitation_letter, "sponsor_letter": sponsor_letter,
            "employment_letter": employment_letter}[kind](f, today)
