"""Entity, date, and preference extraction from text."""
from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Date/temporal extraction
# ---------------------------------------------------------------------------
_DATE_PATTERNS = [
    re.compile(r'\b(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2})(?:st|nd|rd|th)?\b', re.I),
    re.compile(r'\b(\d{4})[-/](\d{1,2})[-/](\d{1,2})\b'),
    re.compile(r'\b(\d{1,2})[-/](\d{1,2})[-/](\d{4})\b'),
    re.compile(r'\b(\d+|one|two|three|four|five|six|seven|eight|nine|ten)\s+(days?|weeks?|months?|years?)\s*(ago|before|after|later)?\b', re.I),
    re.compile(r'\b(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\b', re.I),
    re.compile(r'\b(\d{1,2}):(\d{2})\s*(AM|PM|am|pm)?\b'),
    re.compile(r'\b(\d{1,2})\s*(AM|PM|am|pm)\b'),
]


def extract_dates(text: str) -> list[str]:
    dates = []
    for pat in _DATE_PATTERNS:
        for m in pat.finditer(text):
            dates.append(m.group(0).lower())
    return dates


# ---------------------------------------------------------------------------
# Entity extraction
# ---------------------------------------------------------------------------
_ENTITY_RE = re.compile(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\b")
_NAME_STOPWORDS = frozenset({
    "The", "This", "That", "These", "Those", "It", "Its", "They", "Their",
    "We", "Our", "He", "She", "His", "Her", "You", "Your", "My", "In",
    "On", "At", "By", "For", "With", "From", "To", "And", "But", "Or",
    "If", "When", "Where", "What", "How", "Why", "Who", "Which", "Each",
    "Every", "Some", "Any", "All", "Most", "Many", "Few", "No", "Not",
    "Also", "However", "Therefore", "Furthermore", "Moreover", "Thus",
    "Here", "There", "Now", "Then", "After", "Before", "While", "Since",
    "Sure", "Yes", "No", "Well", "Great", "Good", "Thanks", "Thank",
    "Hi", "Hello", "Hey", "Okay", "Ok", "Right", "Let", "Can", "Could",
    "Would", "Should", "May", "Might", "Will", "Shall", "Do", "Does",
    "Did", "Have", "Has", "Had", "Are", "Is", "Was", "Were", "Be",
    "Been", "Being", "First", "Last", "Next", "New", "Old", "Just",
    "Really", "Actually", "Definitely", "Absolutely", "Recently",
})


def extract_entities(text: str) -> list[str]:
    entities = []
    for m in _ENTITY_RE.finditer(text):
        ent = m.group(1)
        if ent not in _NAME_STOPWORDS and len(ent) > 1:
            entities.append(ent.lower())
    for m in re.finditer(r"['\"]([^'\"]{2,40})['\"]", text):
        entities.append(m.group(1).lower())
    return list(set(entities))


# ---------------------------------------------------------------------------
# Preference extraction
# ---------------------------------------------------------------------------
_PREF_PATTERNS = [
    re.compile(r"\b(?:i|we)\s+(?:prefer|like|love|enjoy|hate|dislike|want|need|use|chose|picked|switched to|started using|recommend|always|usually|typically)\s+(.{3,80}?)(?:\.|,|!|\?|$)", re.I),
    re.compile(r"\b(?:my|our)\s+(?:favorite|preferred|go-to|usual|regular|default)\s+(.{3,60}?)(?:\.|,|!|\?|$)", re.I),
    re.compile(r"\b(?:i'm|i am)\s+(?:a fan of|into|interested in|passionate about|obsessed with|addicted to)\s+(.{3,50}?)(?:\.|,|!|\?|$)", re.I),
    re.compile(r"\b(?:i|we)\s+(?:always|usually|typically|normally|generally)\s+(?:go with|choose|pick|get|order|buy|use|watch|listen to|read|play|eat|drink|wear)\s+(.{3,60}?)(?:\.|,|!|\?|$)", re.I),
    re.compile(r"\b(?:i've been|i have been|i started)\s+(?:using|reading|watching|playing|eating|drinking|wearing|going to|listening to)\s+(.{3,60}?)(?:\.|,|!|\?|$)", re.I),
]


def extract_preferences(text: str) -> list[str]:
    prefs = []
    for pat in _PREF_PATTERNS:
        for m in pat.finditer(text):
            prefs.append(m.group(0).lower().strip())
    return prefs
