"""Cheap rule-based triage, so only mail that can contain opportunities reaches the LLM."""

import re

from internradar.gmail.client import Email
from internradar.models import EmailKind

_ALERT_SENDERS = (
    "linkedin.com", "naukri.com", "internshala.com", "unstop.com", "indeed.com",
    "wellfound.com", "instahyre.com", "cutshort.io", "glassdoor.com", "foundit.in",
)  # fmt: skip

# Replies about an application already made (handled by application tracking, not here).
_STATUS = re.compile(
    r"\b(thank you for (applying|your application)|application (received|status)|"
    r"regret to inform|not moving forward|unfortunately|shortlisted|interview (invite|scheduled)|"
    r"online assessment|coding (test|assessment)|offer letter)\b",
    re.I,
)

_OPPORTUNITY = re.compile(
    r"\b(intern|interns|internship|internships|placement|hiring|opportunity|drive|"
    r"apply by|last date|registration|register|job alert|jobs? for you|new jobs?)\b",
    re.I,
)


def classify(email: Email) -> EmailKind:
    subject = email.subject
    if _STATUS.search(subject):
        return EmailKind.STATUS
    domain = email.sender.rsplit("@", 1)[-1]
    if any(domain == d or domain.endswith("." + d) for d in _ALERT_SENDERS):
        return EmailKind.JOB_ALERT if _OPPORTUNITY.search(subject) else EmailKind.OTHER
    if _OPPORTUNITY.search(subject) or _OPPORTUNITY.search(email.body[:2000]):
        return EmailKind.OPPORTUNITY
    return EmailKind.OTHER
