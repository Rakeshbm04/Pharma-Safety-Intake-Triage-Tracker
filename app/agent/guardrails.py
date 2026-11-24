"""Scope guardrails: block clinical / regulatory decision requests before any tool or RAG call.

Policy (layered, deterministic):

1. ALWAYS refused, even inside "what does the SOP say ..." questions:
     - case-level decision questions about causality, seriousness or reportability
       ("did X cause ...", "is this case serious", "must we notify the regulator")
     - dosing changes and personal medical advice ("stop taking", "safe to use", "see a doctor")
     - product safety / market decisions ("is the drug safe", "should it be withdrawn")
2. Refused unless the message is a meta-question about recorded SOP content:
     - strong reportability vocabulary (reportable, expedited, SUSAR, MedWatch, 15-day reporting)
     - regulator notification wording, therapy/treatment vocabulary, diagnosis/prognosis
3. DEFAULT-DENY for clinical questions: a question that mentions a clinical term is refused unless
   it is phrased as a records search ("reports/cases/complaints of ..."). A records search can
   only return verbatim recorded text via the extractive RAG pipeline, never an assessment.

Product-quality vocabulary (discoloration, packaging, particles ...) is not clinical, so quality
wording such as "damaged due to shipping" or "root cause investigation" stays in scope.
"""
from __future__ import annotations

import re
from typing import Optional

from app.rag.text import MEDICAL_SYNONYMS, normalize_medical

SCOPE_REFUSAL = (
    "I can't help with that. This training tool does not assess causality, recommend or discuss "
    "medical treatment or dosing, judge seriousness or product safety, or determine regulatory "
    "reportability. Those decisions belong to qualified pharmacovigilance professionals using validated "
    "enterprise systems. I can search recorded reports and SOP text (e.g. \"reports of rash for "
    "LOT-31904\"), log an intake, or update a report's workflow status."
)

I = re.I
PQC_CONCEPTS = {"discoloration", "particulate", "packaging_defect", "tablet_defect", "odor", "mislabeling"}
CLINICAL_CONCEPTS = tuple(f"c_{c}" for c in MEDICAL_SYNONYMS if c not in PQC_CONCEPTS)
CLINICAL_WORDS = re.compile(
    r"\b(rash(es)?|reactions?|symptoms?|side[- ]effects?|illness|sick|pain|aches?|swelling|swollen|death|died|"
    r"hospital\w*|allerg\w*|breath\w*|seizures?|bleeding|injur\w*|harm(ed|ful)?|patients?|dizzy|itch\w*|"
    r"sore|cough\w*|diarrh\w*|insomnia|anxiety|depress\w*|blood pressure|heart|liver|kidney|pregnan\w*)\b", I)

QUESTION = re.compile(
    r"\?\s*$|^\s*(is|are|was|were|did|does|do|could|can|should|must|would|will|has|have|why|what|how|which|"
    r"may|might|shall)\b", I)
DECISION_Q = re.compile(
    r"(^|[,:;]\s*|\b(sop|sop-\d{3}|procedure)\b[^?]*?)\b(is|are|was|were|did|does|do|could|can|should|must|would|"
    r"will|might|may)\b(?! .{0,40}\bsay\b)", I)
RECORDS_SEARCH = re.compile(
    r"\b(reports?|cases?|complaints?|records?|entries|intakes?|history|submissions?|lot-\d{5}|batch(es)?)\b"
    r"|^\s*(show|list|find|search|look ?up|get|any|count|how many)\b", I)
META_SOP = re.compile(r"\b(sops?|sop-\d{3}|procedures?|process|policy|guidance|guidelines?|what does .{0,40} say|"
                      r"according to|per sop)\b", I)

# -- 1. always refused ---------------------------------------------------------
STRONG_CAUSAL = re.compile(
    r"\b(caus(e|ed|es|ing|ality|al|ative)|relatedness|attributab\w*|(drug|product|medicine|vaccine|treatment)[- ]related|"
    r"to blame|responsible for|provok\w*|induc(ed|es|ing))\b", I)
WEAK_CAUSAL = re.compile(
    r"\b(due to|because of|linked to|link between|connection between|connected (to|with)|associated with|"
    r"association between|related to|(lead|leads|led|leading) to|result(ed|s|ing)? (from|in)|(a |the )?result of|"
    r"trigger\w*|behind (the|this|her|his)|explain\w*|from (taking|using)?|make (her|him|them|me|people|patients)|"
    r"made (her|him|them|me)|produc(e|es|ed|ing))\b", I)
SEVERITY_TERMS = re.compile(r"\b(serious(ness)?|life[- ]threatening|saes?|susars?|reportab\w*|expedit\w*|"
                            r"(7|15|90)[- ]day (report\w*|deadline|timeline|clock)|qualif\w* for)\b", I)
REG_ACTION = re.compile(
    r"\b(tell|inform|notify|report(ed|ing)?|submit(ted|ting)?|send|sent|file[ds]?|filing|escalat\w+|go(es)?)\b"
    r".{0,40}\b(fda|ema|mhra|pmda|regulators?|authorit\w+|competent authorit\w+|health authorit\w+)\b|"
    r"\b(file|submit) an? (medwatch|cioms|icsr|e2b)\b|\bdeadline for (submitting|reporting)\b", I)
DOSING = re.compile(
    r"\b(increase|decrease|adjust|change|stop|reduce|lower|raise|double|halve|skip) (the |her |his |their |a )?(dose|dosage)\b|"
    r"\b(dose|dosage) (be |be safely )?(lowered|raised|increased|decreased|reduced|adjusted|changed|safe)\b|"
    r"\b(what|which|how much|how many) (dose|dosage|tablets?|mg)\b.{0,30}\b(is|are|should|would|can|safe)\b|\bdosing advice\b|"
    r"\b(safe|okay|ok|fine|alright) (for (her|him|them|me|the patient|a patient|patients) )?to (take|use|continue|keep|have|give)\b|"
    r"\b(stop|keep|continue|resume|quit) (taking|using)\b|\b(another|more|extra) (tablet|dose|pill|capsule)\b", I)
ADVICE = re.compile(
    r"\b(should|must|can|could|ought|may) (i|they|he|she|we|you|the patient|patients|the person|the reporter|the caller)"
    r"( still)? (take|stop|start|continue|keep|use|avoid|see|go|visit|call|get|do|give|have)\b|"
    r"\bwhat should (i|they|he|she|we|the patient|the person|the caller) do\b|"
    r"\b(see|visit|call|consult) (a|the|their|her|his) (doctor|gp|physician|pharmacist|specialist|nurse)\b|"
    r"\bgo to (the |a )?(hospital|er|a&e|emergency)\b|\bgive (her|him|them|the patient) \w+|"
    r"\b(what|which) (can|could|would|will) (relieve|help|ease|stop|fix|treat)\b|"
    r"\b(would|will|could|can|does|do) (an? |the |some )?[\w-]+ (help|work|relieve|ease)\b", I)
SAFETY_DECISION = re.compile(
    r"\b(is|are) (the |this |that )?(drug|product|medicine|medication|vaccine|tablets?|it|[a-z]+ \d+ ?mg|[a-z]{5,}) safe\b|"
    r"\bshould\b.{0,50}\b(withdrawn|recalled|pulled|banned|suspended|discontinued)\b|"
    r"\b(withdraw|recall|suspend|pull)\w* .{0,25}from (the )?(market|shelves)\b", I)

# -- 2. refused unless SOP meta-question --------------------------------------------
TREATMENT_TERMS = re.compile(
    r"\b(treat(ment|ments|ing|ed)?|therap(y|ies)|cure|remed(y|ies)|antidote|antihistamine\w*|steroids?|epinephrine|"
    r"epipen|ibuprofen|paracetamol|acetaminophen|painkillers?|medical advice|diagnos\w+|prognos\w+|"
    r"clinical (decision|judg?ement))\b", I)

ALLOWED_PHRASES = re.compile(r"\broot[- ]cause(s| analysis| investigation)?\b", I)


def _is_clinical(text: str) -> bool:
    if CLINICAL_WORDS.search(text):
        return True
    norm = normalize_medical(text)
    return any(c in norm for c in CLINICAL_CONCEPTS)


def _is_pqc_only(text: str) -> bool:
    norm = normalize_medical(text)
    pqc = any(f"c_{c}" in norm for c in PQC_CONCEPTS) or re.search(
        r"\b(packag\w*|vials?|blisters?|cartons?|labels?|seals?|humidity|shipping|manufactur\w*|defects?|colou?r)\b", text, I)
    return bool(pqc) and not _is_clinical(text)


def check_scope(message: str) -> Optional[str]:
    """Return the violated rule name, or None when the request is within scope."""
    text = ALLOWED_PHRASES.sub(" ", message).strip()
    clinical = _is_clinical(text)
    meta = bool(META_SOP.search(text))
    decision = bool(DECISION_Q.search(text))
    records = bool(RECORDS_SEARCH.search(text))

    # 1. always refused ---------------------------------------------------------------
    if DOSING.search(text) or (ADVICE.search(text) and (clinical or not meta)):
        return "treatment"
    if SAFETY_DECISION.search(text):
        return "safety_decision"
    if clinical and STRONG_CAUSAL.search(text) and (decision or not meta):
        return "causality"
    # "Was the event caused by the drug?": strong causal decision question, unless purely about a
    # product-quality defect ("was the discoloration caused by humidity?" is a quality question).
    if STRONG_CAUSAL.search(text) and decision and not _is_pqc_only(text):
        return "causality"
    if clinical and WEAK_CAUSAL.search(text) and decision:
        return "causality"
    if SEVERITY_TERMS.search(text) and decision:
        return "reportability"

    if meta:  # meta-question about recorded SOP content
        return None

    # 2. vocabulary refused outside SOP meta-questions -----------------------------------
    if REG_ACTION.search(text) or re.search(r"\b(reportab\w*|expedit\w*|susars?|medwatch|cioms)\b", text, I):
        return "reportability"
    if TREATMENT_TERMS.search(text):
        return "clinical_decision" if re.search(r"diagnos|prognos|clinical", text, I) else "treatment"

    # 3. default-deny: clinical question that is not a records search ---------------------
    if clinical and QUESTION.search(text) and not records:
        return "clinical_question"
    if clinical and STRONG_CAUSAL.search(text):
        return "causality"
    return None
