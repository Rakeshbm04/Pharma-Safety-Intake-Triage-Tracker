"""Tokenisation and medical-synonym normalisation shared by the RAG stages."""
from __future__ import annotations

import re
from functools import lru_cache

# Keeps identifiers such as LOT-88392 and REF-2026-0001 as single tokens.
TOKEN_RE = re.compile(r"[a-z]+-\d+(?:-\d+)*|[a-z0-9]+(?:'[a-z]+)?")
ID_RE = re.compile(r"\b(?:LOT-\d{5}|REF-\d{4}-\d{4})\b", re.I)

STOPWORDS = frozenset(
    """a an and are as at be been by for from has have in into is it its of on or that the this to was were
    will with not no but if then than so such which who whom what when where why how do does did can could
    should would may might must any all some our we you your they their there here also per via""".split()
)
# Generic query words that carry no retrieval signal for the reranker coverage score.
QUERY_FILLER = STOPWORDS | frozenset(
    """show find list give tell me about please search lookup look up records record reports report reported
    reporting cases case any past previous historical history related regarding involving similar patients
    batch lot product we have
    patient people someone experienced experiencing having had""".split()
)

# Canonical concept -> surface forms (lay + clinical). Fictional training lexicon.
MEDICAL_SYNONYMS: dict[str, tuple[str, ...]] = {
    "dyspnea": ("dyspnea", "dyspnoea", "difficulty breathing", "trouble breathing", "shortness of breath",
                "short of breath", "breathlessness", "hard to breathe", "breathing difficulty", "can't breathe"),
    "urticaria": ("urticaria", "hives", "itchy welts", "skin welts"),
    "rash": ("rash", "skin eruption", "red spots", "exanthema"),
    "pruritus": ("pruritus", "itching", "itchy skin", "itchiness"),
    "pyrexia": ("pyrexia", "fever", "high temperature", "febrile"),
    "emesis": ("emesis", "vomiting", "throwing up", "threw up"),
    "nausea": ("nausea", "nauseous", "feeling sick", "queasy", "upset stomach"),
    "cephalalgia": ("cephalalgia", "headache", "head pain", "migraine"),
    "vertigo": ("vertigo", "dizziness", "dizzy", "lightheaded", "light-headed"),
    "anaphylaxis": ("anaphylaxis", "anaphylactic", "severe allergic reaction", "throat swelling"),
    "angioedema": ("angioedema", "facial swelling", "swollen lips", "swollen face", "lip swelling"),
    "syncope": ("syncope", "fainting", "fainted", "passed out", "loss of consciousness"),
    "tachycardia": ("tachycardia", "racing heart", "rapid heartbeat", "heart pounding", "palpitations"),
    "fatigue": ("fatigue", "tiredness", "exhaustion", "lethargy", "worn out"),
    "injection_site_reaction": ("injection site reaction", "injection site pain", "sore arm", "redness at injection site"),
    "discoloration": ("discoloration", "discolouration", "discolored", "discoloured", "color change", "colour change",
                      "changed color", "yellowing", "darkened"),
    "particulate": ("particulate", "particles", "particulate matter", "floating particles", "specks", "foreign matter"),
    "packaging_defect": ("packaging defect", "damaged packaging", "broken seal", "torn blister", "cracked vial",
                         "leaking", "leak", "leakage", "damaged box"),
    "tablet_defect": ("broken tablet", "chipped tablet", "crumbling", "crumbled", "cracked tablet", "split tablet"),
    "odor": ("odor", "odour", "smell", "foul smell", "unusual smell", "strange odor"),
    "mislabeling": ("mislabeling", "mislabelling", "wrong label", "incorrect label", "label error", "missing label"),
}


@lru_cache(maxsize=1)
def _synonym_patterns() -> list[tuple[re.Pattern[str], str]]:
    pairs = [(form, concept) for concept, forms in MEDICAL_SYNONYMS.items() for form in forms]
    pairs.sort(key=lambda p: len(p[0]), reverse=True)  # longest phrase first
    return [(re.compile(rf"(?<![a-z_]){re.escape(form)}(?![a-z_])"), f"c_{concept}") for form, concept in pairs]


def tokenize(text: str, drop_stopwords: bool = True) -> list[str]:
    tokens = TOKEN_RE.findall(text.lower())
    if drop_stopwords:
        tokens = [t for t in tokens if t not in STOPWORDS]
    return tokens


def normalize_medical(text: str) -> str:
    """Lower-case and replace lay/clinical synonyms with a shared concept token."""
    out = text.lower()
    for pattern, concept in _synonym_patterns():
        out = pattern.sub(f" {concept} ", out)
    return out


def concept_tokens(text: str) -> list[str]:
    """Tokens after synonym normalisation (concept tokens look like `c_dyspnea`)."""
    norm = normalize_medical(text)
    return [t for t in re.findall(r"c_[a-z_]+|[a-z]+-\d+(?:-\d+)*|[a-z0-9]+", norm) if t not in STOPWORDS]


def extract_ids(text: str) -> set[str]:
    return {m.upper() for m in ID_RE.findall(text)}


def split_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+|\n+", text)
    return [p.strip(" -*#\t") for p in parts if len(p.strip(" -*#\t")) > 3]
