"""Text tokenization, stop words, and stemming."""
from __future__ import annotations

import re

STOP_WORDS = frozenset({
    "i", "me", "my", "myself", "we", "our", "ours", "ourselves", "you", "your",
    "yours", "yourself", "yourselves", "he", "him", "his", "himself", "she", "her",
    "hers", "herself", "it", "its", "itself", "they", "them", "their", "theirs",
    "themselves", "what", "which", "who", "whom", "this", "that", "these", "those",
    "am", "is", "are", "was", "were", "be", "been", "being", "have", "has", "had",
    "having", "do", "does", "did", "doing", "a", "an", "the", "and", "but", "if",
    "or", "because", "as", "until", "while", "of", "at", "by", "for", "with",
    "about", "against", "between", "through", "during", "before", "after", "above",
    "below", "to", "from", "up", "down", "in", "out", "on", "off", "over", "under",
    "again", "further", "then", "once", "here", "there", "when", "where", "why",
    "how", "all", "both", "each", "few", "more", "most", "other", "some", "such",
    "no", "nor", "not", "only", "own", "same", "so", "than", "too", "very", "s",
    "t", "can", "will", "just", "don", "should", "now", "d", "ll", "m", "o", "re",
    "ve", "y", "ain", "aren", "couldn", "didn", "doesn", "hadn", "hasn", "haven",
    "isn", "ma", "mightn", "mustn", "needn", "shan", "shouldn", "wasn", "weren",
    "won", "wouldn",
})

WORD_RE = re.compile(r"[a-z0-9]+(?:'[a-z]+)?")


def stem(word: str) -> str:
    if len(word) <= 3:
        return word
    suffixes = [
        ("ational", "ate"), ("tional", "tion"), ("encies", "ence"),
        ("ancies", "ance"), ("izers", "ize"), ("ously", "ous"),
        ("ively", "ive"), ("ments", "ment"), ("ities", ""),
        ("ness", ""), ("ings", ""), ("ment", ""), ("ence", ""),
        ("ance", ""), ("ible", ""), ("able", ""), ("tion", ""),
        ("ling", ""), ("ally", ""), ("ized", "ize"), ("ised", "ise"),
        ("ful", ""), ("ing", ""), ("ers", ""), ("ies", "y"),
        ("ess", ""), ("est", ""), ("ous", ""), ("ive", ""),
        ("ize", ""), ("ise", ""), ("ion", ""), ("ed", ""),
        ("er", ""), ("ly", ""), ("es", ""), ("'s", ""), ("s", ""),
    ]
    for suffix, replacement in suffixes:
        if word.endswith(suffix) and len(word) - len(suffix) >= 2:
            return word[:-len(suffix)] + replacement
    return word


def tokenize(text: str) -> list[str]:
    return [stem(w) for w in WORD_RE.findall(text.lower()) if w not in STOP_WORDS and len(w) > 1]


def tokenize_raw(text: str) -> list[str]:
    return [w for w in WORD_RE.findall(text.lower()) if w not in STOP_WORDS and len(w) > 1]
