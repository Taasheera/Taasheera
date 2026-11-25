"""Official public guidance supplied to the assistant without sending a search query."""
import re

SOURCES = {
    "schengen-short-stay": ("Schengen short-stay visas",
        "https://home-affairs.ec.europa.eu/policies/schengen/schengen-visas/applying-schengen-visa_en",
        "The short-stay limit is 90 days in any 180-day period. Confirm the local document list with the relevant consulate."),
    "uk-standard-visitor": ("UK Standard Visitor",
        "https://www.gov.uk/standard-visitor",
        "The rules depend on nationality and purpose. GOV.UK links to a visa check and explains permitted visitor activities."),
    "us-b2-tourist": ("US visitor visas",
        "https://travel.state.gov/content/travel/en/us-visas/tourism-visit/visitor.html",
        "The State Department lists the visitor visa process, DS-160 and required documents. Embassies may require additional documents."),
    "canada-visitor": ("Canada visitor visas",
        "https://www.canada.ca/en/immigration-refugees-citizenship/services/visit-canada/visitor-visa.html",
        "IRCC explains who needs a visitor visa, links to the travel authorization check and provides the application steps."),
    "china-tourist-l": ("China tourist (L) visas",
        "https://bio.visaforchina.cn/BEY3_EN/qianzhengyewu/jichuzhishi/banliliucheng/420378516949962755.html",
        "Apply with the COVA online form, then give fingerprints at a Chinese visa centre. Some passports are visa-free for 30 days."),
    "japan-short-stay": ("Japan short-term stay visas",
        "https://www.mofa.go.jp/j_info/visit/visa/index.html",
        "MOFA lists visa-exempt countries, JAPAN eVISA eligibility and the documents for a short-term stay of up to 90 days."),
    "india-tourist": ("India tourist and e-Tourist visas",
        "https://indianvisaonline.gov.in/evisa/tvoa.html",
        "Eligible passports apply fully online for an e-Tourist Visa; others use the regular online form and an embassy."),
    "turkiye-evisa": ("Türkiye e-Visa",
        "https://www.mfa.gov.tr/visa-information-for-foreigners.en.mfa",
        "The Ministry lists who is visa-exempt, who can get an e-Visa and the conditions for some passports."),
}
_URL = re.compile(r"https?://[^\s<>\])]+")
_CITATION = re.compile(r"\[source:([a-z0-9-]+)\]", re.IGNORECASE)


def policy_prompt() -> str:
    return "\n".join(f"- [{key}] {title}. {note}" for key, (title, _, note) in SOURCES.items())


def link_citations(reply: str) -> str:
    # URL output is entirely controlled by the server. The model can only select a supplied source ID.
    without_urls = _URL.sub(lambda match: match[0] if match[0].rstrip(".,;") in
                            {source[1] for source in SOURCES.values()} else "", reply)
    return _CITATION.sub(lambda match: SOURCES[match[1].lower()][1] if match[1].lower() in SOURCES else "", without_urls)
