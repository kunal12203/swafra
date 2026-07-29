"""Fact lifecycle system — extracts structured facts, detects conflicts, manages validity.

Architecture:
  - Facts are (subject, relation, value) triples with temporal validity
  - On ingest, facts are extracted from chunks via pattern + embedding clustering
  - When a new fact conflicts on (subject, relation), the old fact's validity ends
  - At search time, chunks whose primary facts are superseded get penalized

This is NOT an LLM call — it's local pattern extraction + embedding similarity
to detect when two facts refer to the same slot.
"""
from __future__ import annotations

import hashlib
import re
import time

from engine.embedding import cosine_sim, embed
from engine.store import get_store


def _load_facts() -> list[dict]:
    return get_store().load_facts()


def _save_facts(facts: list[dict]):
    get_store().save_facts(facts)

# ---------------------------------------------------------------------------
# Fact schema
# ---------------------------------------------------------------------------
# Each fact:
# {
#   "id": str,
#   "subject": str,           # normalized: "user", entity name, etc.
#   "relation": str,          # normalized slot: "uses_editor", "prefers_drink", etc.
#   "value": str,             # the actual value
#   "chunk_id": str,          # which chunk this was extracted from
#   "source_id": str,         # which source session
#   "created_at": float,      # timestamp
#   "valid_until": float|None # None = still current; timestamp = superseded
#   "superseded_by": str|None # id of the fact that replaced this one
#   "confidence": float,      # extraction confidence 0-1
#   "evidence": str,          # the original text span
# }


# ---------------------------------------------------------------------------
# Relation extraction patterns
# ---------------------------------------------------------------------------
# Each pattern: (compiled_regex, relation_template, subject_group, value_group)
# relation_template can use {verb} placeholder for dynamic relations

_STATE_PATTERNS = [
    # "I use/prefer/like X" — ongoing state
    (re.compile(r"\b(?:i|we)\s+(use|prefer|like|love|enjoy|recommend|go to|go with|drink|eat|read|watch|listen to|play|wear)\s+(.{2,60}?)(?:\.|,|!|\?|$)", re.I),
     "{verb}", "user", 2),

    # "My X is Y" / "My favorite X is Y"
    (re.compile(r"\bmy\s+(?:favorite\s+|preferred\s+|go-to\s+|usual\s+)?(\w+(?:\s+\w+)?)\s+is\s+(.{2,50}?)(?:\.|,|!|\?|$)", re.I),
     "has_{slot}", "user", 2),

    # "I'm a/an X" — identity/role
    (re.compile(r"\bi(?:'m| am)\s+(?:a|an)\s+(.{2,50}?)(?:\.|,|!|\?|$)", re.I),
     "is_a", "user", 1),

    # "I work at/for X" / "I'm at X"
    (re.compile(r"\b(?:i\s+work\s+(?:at|for)|i(?:'m| am)\s+(?:at|with))\s+(.{2,50}?)(?:\.|,|!|\?|$)", re.I),
     "works_at", "user", 1),

    # "I live in X" / "I'm based in X" / "I'm in X"
    (re.compile(r"\b(?:i\s+live\s+in|i(?:'m| am)\s+(?:based|located)\s+in|i(?:'m| am)\s+in)\s+(.{2,50}?)(?:\.|,|!|\?|$)", re.I),
     "located_in", "user", 1),

    # "I started X" / "I've been X-ing"
    (re.compile(r"\b(?:i\s+started|i've\s+been|i\s+began)\s+(\w+(?:ing)?)\s+(.{2,50}?)(?:\.|,|!|\?|$)", re.I),
     "started_{activity}", "user", 2),
]

_TRANSITION_PATTERNS = [
    # "I switched from X to Y"
    (re.compile(r"\b(?:i|we)\s+switched\s+(?:from\s+(.{2,40}?)\s+)?to\s+(.{2,50}?)(?:\.|,|!|\?|$)", re.I),
     "switched_to", "user"),

    # "X instead of Y" (new_value=X, old_value=Y)
    (re.compile(r"\b(?:i|we)\s+(?:switched|moved|changed)\s+to\s+(.{2,40}?)\s+instead\s+of\s+(.{2,40}?)(?:\.|,|!|\?|$)", re.I),
     "switched_to", "user"),

    # "X instead of Y" without explicit verb (captures "oat milk lattes instead of black coffee")
    (re.compile(r"\b(?:i|we)\s+\w+\s+(?:to\s+)?(.{2,40}?)\s+instead\s+of\s+(.{2,40}?)(?:\.|,|!|\?|$)", re.I),
     "switched_to", "user"),

    # "I moved to X" / "I changed to X"
    (re.compile(r"\b(?:i|we)\s+(?:moved|changed|migrated|upgraded)\s+to\s+(.{2,50}?)(?:\.|,|!|\?|$)", re.I),
     "changed_to", "user"),

    # "I stopped using X" / "I no longer use X"
    (re.compile(r"\b(?:i|we)\s+(?:stopped|quit|dropped|no longer)\s+(?:using\s+)?(.{2,50}?)(?:\.|,|!|\?|$)", re.I),
     "stopped", "user"),

    # "I replaced X with Y"
    (re.compile(r"\b(?:i|we)\s+replaced\s+(.{2,40}?)\s+with\s+(.{2,50}?)(?:\.|,|!|\?|$)", re.I),
     "replaced", "user"),
]

# Relation normalization: group semantically equivalent relations into slots
_RELATION_SLOTS = {
    "use": "uses",
    "prefer": "prefers",
    "like": "prefers",
    "love": "prefers",
    "enjoy": "prefers",
    "recommend": "recommends",
    "go to": "frequents",
    "go with": "uses",
    "drink": "drinks",
    "eat": "eats",
    "read": "reads",
    "watch": "watches",
    "listen to": "listens_to",
    "play": "plays",
    "wear": "wears",
}


def _normalize_relation(verb: str) -> str:
    verb_lower = verb.lower().strip()
    return _RELATION_SLOTS.get(verb_lower, verb_lower)


def _fact_id(subject: str, relation: str, value: str, source_id: str) -> str:
    key = f"{subject}:{relation}:{value.lower()}:{source_id}"
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def _clean_value(val: str) -> str:
    val = val.strip().rstrip(".,!?;:")
    val = re.sub(r"\s+", " ", val)
    return val


# ---------------------------------------------------------------------------
# Fact extraction from text
# ---------------------------------------------------------------------------

def extract_facts(text: str, chunk_id: str, source_id: str) -> list[dict]:
    """Extract structured facts from a text chunk."""
    facts = []
    now = time.time()

    # State patterns — ongoing facts
    for pattern, rel_template, subject, value_group in _STATE_PATTERNS:
        for m in pattern.finditer(text):
            if rel_template == "{verb}":
                verb = m.group(1)
                relation = _normalize_relation(verb)
                value = _clean_value(m.group(value_group))
            elif "{slot}" in rel_template:
                slot = m.group(1).lower().replace(" ", "_")
                relation = rel_template.replace("{slot}", slot)
                value = _clean_value(m.group(value_group))
            elif "{activity}" in rel_template:
                activity = m.group(1).lower()
                relation = rel_template.replace("{activity}", activity)
                value = _clean_value(m.group(value_group))
            else:
                relation = rel_template
                value = _clean_value(m.group(value_group))

            if len(value) < 2 or len(value) > 100:
                continue

            facts.append({
                "id": _fact_id(subject, relation, value, source_id),
                "subject": subject,
                "relation": relation,
                "value": value,
                "chunk_id": chunk_id,
                "source_id": source_id,
                "created_at": now,
                "valid_until": None,
                "superseded_by": None,
                "confidence": 0.8,
                "evidence": m.group(0).strip(),
                "is_transition": False,
            })

    # Transition patterns — these invalidate prior state
    for pattern, rel_type, subject in _TRANSITION_PATTERNS:
        for m in pattern.finditer(text):
            groups = [g for g in m.groups() if g]
            if not groups:
                continue

            match_text = m.group(0).lower()

            # For "instead of" patterns: first group = new, second = old
            # For "switched from X to Y": first group = old, last = new
            # For "replaced X with Y": first = old, last = new
            if "instead of" in match_text:
                new_value = _clean_value(groups[0])
                old_value = _clean_value(groups[1]) if len(groups) > 1 else None
            elif "from" in match_text and len(groups) > 1:
                old_value = _clean_value(groups[0])
                new_value = _clean_value(groups[-1])
            elif "replaced" in match_text and len(groups) > 1:
                old_value = _clean_value(groups[0])
                new_value = _clean_value(groups[-1])
            else:
                new_value = _clean_value(groups[-1])
                old_value = _clean_value(groups[0]) if len(groups) > 1 else None

            if len(new_value) < 2 or len(new_value) > 100:
                continue

            # Don't duplicate if same value already extracted
            if any(f["value"].lower() == new_value.lower() and f["is_transition"] for f in facts):
                continue

            facts.append({
                "id": _fact_id(subject, rel_type, new_value, source_id),
                "subject": subject,
                "relation": rel_type,
                "value": new_value,
                "old_value": old_value,
                "chunk_id": chunk_id,
                "source_id": source_id,
                "created_at": now,
                "valid_until": None,
                "superseded_by": None,
                "confidence": 0.9,
                "evidence": m.group(0).strip(),
                "is_transition": True,
            })

    return facts


# ---------------------------------------------------------------------------
# Conflict detection — the core lifecycle logic
# ---------------------------------------------------------------------------

_SLOT_EQUIVALENCES = {
    "uses": {"uses", "prefers", "switched_to", "changed_to", "replaced"},
    "prefers": {"uses", "prefers", "switched_to", "changed_to"},
    "drinks": {"drinks", "switched_to", "changed_to"},
    "eats": {"eats", "switched_to", "changed_to"},
    "frequents": {"frequents", "switched_to", "changed_to"},
    "listens_to": {"listens_to", "switched_to", "changed_to"},
    "watches": {"watches", "switched_to", "changed_to"},
    "reads": {"reads", "switched_to", "changed_to"},
    "wears": {"wears", "switched_to", "changed_to"},
    "works_at": {"works_at", "switched_to", "changed_to"},
    "located_in": {"located_in", "switched_to", "changed_to"},
    "switched_to": {"uses", "prefers", "drinks", "eats", "frequents",
                    "listens_to", "watches", "reads", "wears", "works_at"},
    "changed_to": {"uses", "prefers", "drinks", "eats", "frequents",
                   "listens_to", "watches", "reads", "wears", "works_at"},
    "stopped": {"uses", "prefers", "drinks", "eats", "frequents",
                "listens_to", "watches", "reads", "wears"},
}


def _relations_compatible(rel_a: str, rel_b: str) -> bool:
    """Check if two relations could refer to the same semantic slot."""
    if rel_a == rel_b:
        return True
    equiv_a = _SLOT_EQUIVALENCES.get(rel_a, set())
    equiv_b = _SLOT_EQUIVALENCES.get(rel_b, set())
    return rel_b in equiv_a or rel_a in equiv_b


def _values_refer_to_same_slot(fact_a: dict, fact_b: dict) -> float:
    """Score how likely two facts refer to the same semantic slot.

    Returns 0-1: 1 = definitely same slot, 0 = unrelated.

    Strategy:
      - If transition has explicit old_value, use substring match first (fast).
      - Only fall back to embedding if substring fails (rare).
      - Same-relation state facts never conflict with each other.
    """
    rel_a = fact_a["relation"]
    rel_b = fact_b["relation"]

    if not _relations_compatible(rel_a, rel_b):
        return 0.0

    # Path 1: Transition with explicit old_value — precise match only
    if fact_b.get("is_transition") and fact_b.get("old_value"):
        old_val = fact_b["old_value"].lower().strip()
        a_val = fact_a["value"].lower().strip()

        # Exact substring match (handles "VS Code" in "VS Code as my editor")
        if old_val in a_val or a_val in old_val:
            return 0.95

        # Tokenized overlap — cheaper than embedding
        old_tokens = set(old_val.split())
        a_tokens = set(a_val.split())
        if old_tokens and a_tokens:
            overlap = len(old_tokens & a_tokens) / max(len(old_tokens), 1)
            if overlap >= 0.5:
                return 0.85

        return 0.0

    # Path 2: Transition WITHOUT explicit old_value
    if fact_b.get("is_transition"):
        if rel_a == rel_b:
            return 0.7
        return 0.0

    return 0.0


def detect_conflicts(new_facts: list[dict], existing_facts: list[dict]) -> list[tuple[dict, dict, float]]:
    """Find (existing_fact, new_fact, score) triples where new supersedes existing.

    A conflict occurs when:
      1. Same subject
      2. Facts refer to same semantic slot (high slot score)
      3. Values are different
      4. New fact is more recent

    Only transition facts (switched_to, changed_to, stopped, replaced) can
    supersede. Plain state facts ("I use X") don't invalidate prior state —
    they might coexist (user uses multiple things).
    """
    conflicts = []

    active_existing = [f for f in existing_facts if f.get("valid_until") is None]

    for new_f in new_facts:
        # Only transitions trigger supersession
        if not new_f.get("is_transition"):
            continue

        for old_f in active_existing:
            if new_f["subject"] != old_f["subject"]:
                continue
            if new_f["value"].lower() == old_f["value"].lower():
                continue
            if new_f["id"] == old_f["id"]:
                continue
            # Don't supersede other transitions
            if old_f.get("is_transition"):
                continue

            slot_score = _values_refer_to_same_slot(old_f, new_f)

            if slot_score >= 0.65:
                conflicts.append((old_f, new_f, slot_score))

    conflicts.sort(key=lambda x: x[2], reverse=True)
    return conflicts


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def ingest_facts(text: str, chunk_id: str, source_id: str) -> dict:
    """Extract facts from text, detect conflicts, update lifecycle state.

    Returns summary of what happened.
    """
    facts_store = _load_facts()

    new_facts = extract_facts(text, chunk_id, source_id)
    if not new_facts:
        return {"extracted": 0, "conflicts": 0, "superseded": 0}

    # Check conflicts against both existing store AND other new facts in this batch
    all_candidates = facts_store + [f for f in new_facts if not f.get("is_transition")]
    conflicts = detect_conflicts(new_facts, all_candidates)

    superseded_ids = set()
    for old_f, new_f, score in conflicts:
        for stored in facts_store:
            if stored["id"] == old_f["id"] and stored.get("valid_until") is None:
                stored["valid_until"] = time.time()
                stored["superseded_by"] = new_f["id"]
                superseded_ids.add(stored["id"])
                break

    # Mark new non-transition facts that were superseded within the same batch
    for old_f, new_f, score in conflicts:
        for nf in new_facts:
            if nf["id"] == old_f["id"] and nf.get("valid_until") is None:
                nf["valid_until"] = time.time()
                nf["superseded_by"] = new_f["id"]
                superseded_ids.add(nf["id"])

    # Add new facts (skip duplicates)
    existing_ids = {f["id"] for f in facts_store}
    added = 0
    for f in new_facts:
        if f["id"] not in existing_ids:
            facts_store.append(f)
            existing_ids.add(f["id"])
            added += 1

    _save_facts(facts_store)

    return {
        "extracted": len(new_facts),
        "added": added,
        "conflicts": len(conflicts),
        "superseded": len(superseded_ids),
    }


def get_chunk_fact_signals() -> tuple[dict[str, float], dict[str, list[str]]]:
    """Return (penalties, active_values) for chunks based on fact lifecycle.

    penalties: chunk_id -> 0-1 staleness ratio
    active_values: chunk_id -> list of active fact values (for query-aware boosting)
    """
    facts_store = _load_facts()

    chunk_stale: dict[str, int] = {}
    chunk_active: dict[str, int] = {}
    chunk_active_values: dict[str, list[str]] = {}

    for f in facts_store:
        cid = f["chunk_id"]
        if f.get("valid_until") is not None:
            chunk_stale[cid] = chunk_stale.get(cid, 0) + 1
        else:
            chunk_active[cid] = chunk_active.get(cid, 0) + 1
            if cid not in chunk_active_values:
                chunk_active_values[cid] = []
            chunk_active_values[cid].append(f["value"].lower())

    penalties = {}
    for cid, stale_count in chunk_stale.items():
        active_count = chunk_active.get(cid, 0)
        total = stale_count + active_count
        penalties[cid] = stale_count / total

    return penalties, chunk_active_values


def get_superseded_chunk_ids() -> dict[str, float]:
    penalties, _ = get_chunk_fact_signals()
    return penalties


def get_active_facts(subject: str = None, relation: str = None) -> list[dict]:
    """Get currently-valid facts, optionally filtered."""
    facts_store = _load_facts()
    active = [f for f in facts_store if f.get("valid_until") is None]

    if subject:
        active = [f for f in active if f["subject"] == subject]
    if relation:
        active = [f for f in active if f["relation"] == relation]

    return active


def get_fact_history(subject: str = None, relation: str = None) -> list[dict]:
    """Get full fact timeline including superseded ones, ordered by time."""
    facts_store = _load_facts()

    results = facts_store
    if subject:
        results = [f for f in results if f["subject"] == subject]
    if relation:
        results = [f for f in results if f["relation"] == relation]

    results.sort(key=lambda f: f["created_at"])
    return results


def invalidate_fact(fact_id: str) -> bool:
    """Manually mark a fact as no longer valid."""
    facts_store = _load_facts()

    for f in facts_store:
        if f["id"] == fact_id and f.get("valid_until") is None:
            f["valid_until"] = time.time()
            _save_facts(facts_store)
            return True

    return False
