"""Deterministic, local classification of request intent: does ``request_text`` authorize deletion?

``DELETION``      the person asks for account/personal-data deletion, or asks us to verify/finish one already requested.
``NON_DELETION``  access/export, correction, login help, marketing opt-out, or an explicit refusal of deletion.
``UNCERTAIN``     no cue in any supported language (escalate; never silently treated as either).

Method (no case IDs, no model calls):
1. explicit negations of deletion ("do not close the account", "not asking for deletion", "without removing", ...)
   always win: the person is saying what they do NOT want;
2. otherwise a deletion verb in any supported language, or deletion-status wording ("the profile is gone", "still
   existed"), authorizes deletion - except a verb whose only object is marketing ("remove me from marketing lists");
3. otherwise non-deletion cues (copy/download/access, correction, login, opt-out) mean NON_DELETION;
4. otherwise UNCERTAIN.

Allowances inside a deletion request ("you may keep anonymous statistics", "retain backups if required", "keeping an
internal deleted-account reference is fine") are *not* negations: a negation must target the account/profile itself.

``CLASSIFY_REQUEST`` activity rows are corroboration only (see ``intent_hints.py``): they can flag a conflict, but the
decision always rests on the text.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import List, Optional

DELETION, NON_DELETION, UNCERTAIN = "DELETION", "NON_DELETION", "UNCERTAIN"

_DEL_VERB = re.compile(
    r"(?:\bdelet\w*|\berase\w*|\berasure\b|\bremov\w*|\bclos(?:e|ed|ing|ure)\b|\bwip(?:e|ed|ing)\b|\bpurg\w*|\bdestro\w*|\bterminat\w*"
    r"|\belimin\w*|\bborr\w*|\bcierr\w*|\bsuprim\w*"                                                     # es/it
    r"|\bexclu\w*|\bexclus\w*|\bapag\w*|\bencerr\w*"                                                     # pt
    r"|\bsupprim\w*|\bsuppression\b|\beffac\w*|\bferm\w*"                                               # fr
    r"|\blösch\w*|\bloesch\w*|\bentfern\w*|\bschließ\w*|\bschliess\w*"                                   # de
    r"|\bcancell\w*|\brimuov\w*|\bchiud\w*"                                                              # it
    r"|\bverwijder\w*|\bsluit\w*"                                                                        # nl
    r"|削除|消去|解約|退会|삭제|탈퇴|폐쇄|删除|刪除|注销|удал\w*|حذف)",
    re.I,
)
_STATUS = re.compile(
    r"(?:\b(?:profile|account)\s+(?:is|was|has been)\s+gone\b|\bis gone\b|\bno longer exists?\b|\bstill exist(?:s|ed)?\b"
    r"|\bhas already been removed\b|\bhave already been removed\b|\bhave been removed\b|\bfully removed\b)", re.I)
_NEGATION = [re.compile(p, re.I) for p in (
    # a refusal must target the account/profile/everything itself; "do not delete my invoices" is an allowance, not a refusal
    r"\b(?:do not|don't|dont|never)\s+(?:\w+\s+){0,2}(?:close|delete|erase|remove|cancel|deactivate)\w*\s+(?:or\s+\w+\s+)?(?:(?:my|the|this|any)\s+)?(?:account|profile|anything|it|this)\b(?!\s+(?:before|until|unless|after|when|while)\b)",
    r"\bnot\s+(?:asking|requesting|wanting|a request)\s+(?:for\s+)?(?:the\s+)?(?:deletion|erasure|removal|closure|to\s+(?:delete|erase|close|remove))",
    r"\bnot\s+a\s+request\s+to\s+(?:delete|erase|close|remove)",
    r"\bwithout\s+(?:removing|deleting|closing|erasing)\b",
    r"\b(?:keep|leave|retain|preserve)\s+(?:my|the|this)\s+(?:account|profile)\b",
    r"\bleave\s+my\s+profile\b",
    r"\bstill\s+(?:need|use)\s+(?:the|my|this)\s+(?:account|profile|service)\b(?!\s+(?:deleted|closed|removed|erased))",
    r"\bi\s+still\s+use\s+it\b",
    r"\bcontinue\s+using\b",
    r"\bkeep\s+the\s+account\b",
    r"\bno\s+(?:elimin|borr|cierr)\w*\s+(?:mi|la|el)\b", r"\bn[ãa]o\s+(?:exclu|apag|remov)\w*", r"\bne\s+(?:supprim|ferm)\w*\s+pas\b",
    r"\bnicht\s+(?:löschen|loeschen|schließen)\b", r"削除しないで", r"解約しないで", r"삭제하지\s*(?:마|말)",
)]
# a request that has not decided yet does not authorize destructive action
_HEDGE = re.compile(
    r"\b(?:decide|decision|think(?:ing)? about|consider(?:ing)?|might|maybe|not sure|undecided)\b[^.?!]{0,40}\b(?:delet\w*|clos\w*|eras\w*|remov\w*)", re.I)
_MARKETING = re.compile(r"\b(?:marketing|promotional|promotion|newsletter|unsubscribe|opt[- ]?out|mailing list)", re.I)
_ACCOUNT_OBJ = re.compile(r"\b(?:account|profile|personal (?:data|information)|my data|cuenta|conta|compte)\b|アカウント|계정|账户|账号", re.I)
_NONDEL_CUES = [
    ("access/export", re.compile(r"\b(?:copy of|download\w*|portable format|what (?:personal )?information|information (?:you )?hold|send me a copy|data export|export my)\b", re.I)),
    ("correction", re.compile(r"\b(?:misspell\w*|correct (?:it|my|the)|out of date|replace it|change the (?:email|address|name|phone)|update my|wrong (?:name|email|address))\b", re.I)),
    ("login help", re.compile(r"\b(?:can'?t access|cannot access|reset\w*|log ?in|password)\b", re.I)),
    ("marketing opt-out", _MARKETING),
]


@dataclass
class Intent:
    label: str
    reasons: List[str] = field(default_factory=list)

    @property
    def authorizes_deletion(self) -> bool:
        return self.label == DELETION


def _sentences(text: str) -> List[str]:
    return [s for s in re.split(r"(?<=[.!?。！？])\s+|\n+", text) if s.strip()]


def classify_intent(text: str) -> Intent:
    t = unicodedata.normalize("NFKC", text or "").replace("’", "'")
    for p in _NEGATION:
        m = p.search(t)
        if m:
            return Intent(NON_DELETION, [f"explicit refusal of deletion: {m.group(0)!r}"])
    m = _HEDGE.search(t)
    if m:
        return Intent(NON_DELETION, [f"undecided/hedged wording, no deletion authorized: {m.group(0)!r}"])
    for s in _sentences(t):
        m = _DEL_VERB.search(s)
        if m and not (_MARKETING.search(s) and not _ACCOUNT_OBJ.search(s)):
            return Intent(DELETION, [f"deletion cue {m.group(0)!r}"])
    m = _STATUS.search(t)
    if m:
        return Intent(DELETION, [f"deletion-status wording {m.group(0)!r}"])
    cues = [name for name, rx in _NONDEL_CUES if rx.search(t)]
    if cues:
        return Intent(NON_DELETION, ["non-deletion request: " + ", ".join(cues)])
    return Intent(UNCERTAIN, ["no deletion or non-deletion cue found"])


def decide(text: str, classify_hint_activity_ids: Optional[List[str]] = None) -> Intent:
    """Text decides. A CLASSIFY_REQUEST hint that disagrees is recorded as a conflict; it only breaks an UNCERTAIN tie
    towards NON_DELETION."""
    intent = classify_intent(text)
    hint = bool(classify_hint_activity_ids)
    if hint and intent.label == DELETION:
        intent.reasons.append(f"conflict: activity {classify_hint_activity_ids} classified a request as non-deletion; text wins")
    elif hint and intent.label == NON_DELETION:
        intent.reasons.append(f"corroborated by activity {classify_hint_activity_ids}")
    elif hint and intent.label == UNCERTAIN:
        return Intent(NON_DELETION, intent.reasons + [f"tie broken by activity {classify_hint_activity_ids}"])
    return intent
