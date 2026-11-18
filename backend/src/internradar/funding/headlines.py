"""Turn a funding-news headline into a structured deal, without an LLM.

Indian startup outlets write funding headlines in a few stable shapes:
  "Physical AI startup Humyn Labs raises $47 Mn in equity and debt"
  "IvyCap Ventures leads $11 Mn Series B round in robotics company Botsync"
  "Recove Ventures Nets ₹56 Cr To Expand Recycling Business"
Everything else (fund closes, stake sales, "to raise" rumours) is rejected.
"""

import re
from dataclasses import dataclass

INR_PER_USD = 88.0  # approximate; amounts are for ranking, not accounting

_RAISE_VERBS = r"raises|raised|bags|secures|nets|gets|receives|picks up|lands|mops up"

_RAISES = re.compile(rf"^(?P<subject>.+?)\s+(?:{_RAISE_VERBS})\s+(?P<rest>.+)$", re.I)
_LEADS = re.compile(
    r"^(?P<lead>.+?)\s+(?:leads|co-leads)\s+(?P<rest>.+?)\s+(?:round\s+)?in\s+(?P<subject>.+)$",
    re.I,
)

# Not a startup raising money: VC funds closing, secondary sales, IPOs, rumours.
_NOT_A_ROUND = re.compile(
    r"\b(to raise|in talks|plans? to|set to|eyes|looking to|first close|final close|"
    r"fund\b(?!ing)|stake|ipo|block deal|acquires|acquisition|revenue|loss)",
    re.I,
)

# Words that end a description of the company ("AI video startup Lemonpeel"), so the
# company name is whatever follows the last of them.
_DESCRIPTOR = re.compile(
    r"^.*\b(?:startup|start-up|company|platform|brand|firm|app|maker|player|provider|"
    r"unicorn|based)\s+",
    re.I,
)

_USD = re.compile(r"(?:\$|USD\s?)\s?(\d[\d,]*(?:\.\d+)?)\s?(mn|m|million|bn|b|billion|k)\b", re.I)
_INR = re.compile(r"(?:Rs\.?|INR|₹)\s?(\d[\d,]*(?:\.\d+)?)\s?(cr|crore|lakh|l)\b", re.I)
_ROUND = re.compile(
    r"\b(pre-seed|seed|pre-series [a-h]|series [a-h]\d?|bridge|angel|venture debt)\b", re.I
)
_LED_BY = re.compile(r"\bled by\s+(?P<lead>.+?)(?=\s+(?:to|for|as|at|with)\b|[,;]|$)", re.I)
# Investor names start with a capital; everything else is case-insensitive because Inc42
# Title-Cases headlines ("Raises $4 Mn From Bajaj Finserv To Expand").
_FROM = re.compile(r"\b(?i:from)\s+(?P<lead>[A-Z].+?)(?=\s+(?i:to|for|as|at|with)\b|[,;]|$)")

_TECH = re.compile(
    r"\b(ai|tech|deeptech|spacetech|fintech|saas|software|robot\w*|platform|app|data|cloud|"
    r"cyber\w*|devtools?|developer\w*|automation|semiconductor\w*|edtech|healthtech|"
    r"insurtech|agritech|quantum|llm|ml|analytics|agent\w*|gpu|chip\w*|api)\b",
    re.I,
)

_USD_UNITS = {"k": 1e3, "m": 1e6, "mn": 1e6, "million": 1e6, "b": 1e9, "bn": 1e9, "billion": 1e9}
_INR_UNITS = {"cr": 1e7, "crore": 1e7, "lakh": 1e5, "l": 1e5}


@dataclass(frozen=True, slots=True)
class Deal:
    company: str
    amount_text: str | None
    amount_usd: float | None
    round: str | None
    lead_investors: str | None
    is_tech: bool


def _number(text: str) -> float:
    return float(text.replace(",", ""))


def parse_amount(text: str) -> tuple[str | None, float | None]:
    if m := _USD.search(text):
        return m.group(0), _number(m.group(1)) * _USD_UNITS[m.group(2).lower()]
    if m := _INR.search(text):
        return m.group(0), _number(m.group(1)) * _INR_UNITS[m.group(2).lower()] / INR_PER_USD
    return None, None


def clean_company(subject: str) -> str:
    subject = re.split(r"[:?|]", subject)[-1]  # "Exclusive: X", "Can AI run a factory? X"
    subject = re.split(r"['’]s\s+", subject)[-1]  # "Ex-TVF director's AI video startup X"
    subject = _DESCRIPTOR.sub("", subject)
    return subject.strip(" '\"‘’“”-")


def parse_headline(headline: str) -> Deal | None:
    title = " ".join(headline.split())  # some feeds pad titles with \r\n
    if _NOT_A_ROUND.search(title):
        return None

    if m := _LEADS.match(title):
        subject, lead, rest = m["subject"], m["lead"], m["rest"]
    elif m := _RAISES.match(title):
        subject, rest = m["subject"], m["rest"]
        lead_match = _LED_BY.search(rest) or _FROM.search(rest)
        lead = lead_match["lead"] if lead_match else None
    else:
        return None

    amount_text, amount_usd = parse_amount(rest)
    company = clean_company(subject)
    if amount_text is None or not company:
        return None

    round_match = _ROUND.search(title)
    return Deal(
        company=company,
        amount_text=amount_text,
        amount_usd=round(amount_usd, 0) if amount_usd else None,
        round=round_match.group(1).title() if round_match else None,
        lead_investors=lead.strip() if lead else None,
        is_tech=bool(_TECH.search(title)),
    )
