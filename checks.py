"""Deterministic checks of one post against one campaign. No model calls."""

from __future__ import annotations

import difflib
import hashlib
import re
from dataclasses import dataclass
from urllib.parse import urlparse

from .rules import Campaign
from .xlength import char_length, find_urls, x_length

HASHTAG_RE = re.compile(r"(?<![\w&])#(\w+)", re.UNICODE)
MENTION_RE = re.compile(r"(?<![\w@])@(\w{1,50})", re.UNICODE)


@dataclass
class Violation:
    rule: str
    detail: str
    fix: str

    def as_dict(self) -> dict:
        return {"rule": self.rule, "detail": self.detail, "fix": self.fix}


def hashtags(text: str) -> list[str]:
    return ["#" + m.group(1) for m in HASHTAG_RE.finditer(text or "")]


def mentions(text: str) -> list[str]:
    return ["@" + m.group(1) for m in MENTION_RE.finditer(text or "")]


def normalize(text: str) -> str:
    text = (text or "").lower()
    for url in find_urls(text):
        text = text.replace(url.lower(), " ")
    return " ".join(re.sub(r"[^\w#@]+", " ", text).split())


def fingerprint(text: str) -> str:
    return hashlib.sha256(normalize(text).encode("utf-8")).hexdigest()[:16]


def _word_re(word: str) -> re.Pattern:
    return re.compile(r"(?<!\w)" + re.escape(word) + r"(?!\w)", re.IGNORECASE)


def _domain(url: str) -> str:
    if "://" not in url:
        url = "https://" + url
    host = (urlparse(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def length_of(text: str, campaign: Campaign) -> int:
    return x_length(text) if campaign.length_counting == "x" else char_length(text)


def find_duplicate(text: str, history: list[dict], threshold: float) -> dict | None:
    """Return the most similar recent post at or above ``threshold``, else None."""
    norm = normalize(text)
    fp = fingerprint(text)
    best, best_ratio = None, 0.0
    for item in history:
        if item.get("fp") == fp:
            return {**item, "similarity": 1.0}
        ratio = difflib.SequenceMatcher(None, norm, item.get("norm", "")).ratio()
        if ratio > best_ratio:
            best, best_ratio = item, ratio
    if best is not None and best_ratio >= threshold:
        return {**best, "similarity": round(best_ratio, 3)}
    return None


def evaluate(text: str, campaign: Campaign, history: list[dict] | None = None,
             duplicate_threshold: float = 0.9) -> list[Violation]:
    text = text or ""
    out: list[Violation] = []
    present_tags = {t.lower() for t in hashtags(text)}
    present_mentions = {m.lower() for m in mentions(text)}

    for tag in campaign.required_hashtags:
        if tag.lower() not in present_tags:
            out.append(Violation("required_hashtag", f"missing {tag}", f"add {tag}"))
    for handle in campaign.required_mentions:
        if handle.lower() not in present_mentions:
            out.append(Violation("required_mention", f"missing {handle}", f"tag {handle}"))
    for phrase in campaign.required_phrases:
        if phrase.lower() not in text.lower():
            out.append(Violation("required_phrase", f"missing the phrase \"{phrase}\"", f"include \"{phrase}\""))

    for word in campaign.banned_words:
        hits = _word_re(word).findall(text)
        if hits:
            out.append(Violation("banned_word", f"contains banned word \"{word}\"",
                                 f"rewrite without \"{word}\""))
    for bp in campaign.banned_patterns:
        if bp.pattern.search(text):
            fix = (f"replace with \"{bp.replace_with}\"" if bp.replace_with is not None
                   else "remove it")
            out.append(Violation("banned_pattern", f"contains {bp.label}", fix))

    if campaign.max_hashtags is not None:
        n = len(hashtags(text))
        if n > campaign.max_hashtags:
            out.append(Violation("max_hashtags", f"{n} hashtags, limit is {campaign.max_hashtags}",
                                 f"drop {n - campaign.max_hashtags} non-required hashtag(s)"))

    urls = find_urls(text)
    if campaign.require_link and not urls:
        out.append(Violation("require_link", "no link in the post", "add the campaign link"))
    for link in campaign.required_links:
        if not any(link.lower() in u.lower() for u in urls):
            out.append(Violation("required_link", f"missing link {link}", f"add {link}"))
    if campaign.allowed_link_domains:
        allowed = campaign.allowed_link_domains
        for u in urls:
            d = _domain(u)
            if not any(d == a or d.endswith("." + a) for a in allowed):
                out.append(Violation("link_domain", f"link to {d} is not on the allowed list",
                                     f"use a link on {', '.join(allowed)}"))

    if campaign.max_length is not None:
        n = length_of(text, campaign)
        if n > campaign.max_length:
            unit = "weighted chars (X counting)" if campaign.length_counting == "x" else "chars"
            out.append(Violation("max_length", f"{n} {unit}, limit is {campaign.max_length}",
                                 f"cut {n - campaign.max_length}"))

    if campaign.no_duplicates and history:
        dup = find_duplicate(text, history, duplicate_threshold)
        if dup:
            pct = int(dup["similarity"] * 100)
            when = str(dup.get("at") or "")[:16].replace("T", " ")
            when = f"on {when} UTC" if when else "recently"
            out.append(Violation("duplicate", f"{pct}% the same as a post sent {when}",
                                 "write a new post instead of reusing one"))
    return out


def suggest_fix(text: str, campaign: Campaign, history: list[dict] | None = None,
                duplicate_threshold: float = 0.9) -> str | None:
    """Mechanical fix: apply pattern replacements and append missing tags.

    Returns the fixed text only when it passes every rule. Banned words, wrong
    links and duplicates need a human or model rewrite, so those return None.
    """
    fixed = text or ""
    for bp in campaign.banned_patterns:
        if bp.replace_with is not None:
            fixed = bp.pattern.sub(bp.replace_with, fixed)
    present_tags = {t.lower() for t in hashtags(fixed)}
    present_mentions = {m.lower() for m in mentions(fixed)}
    tail = [m for m in campaign.required_mentions if m.lower() not in present_mentions]
    tail += [t for t in campaign.required_hashtags if t.lower() not in present_tags]
    if tail:
        fixed = fixed.rstrip() + "\n\n" + " ".join(tail)
    if fixed == text:
        return None
    if evaluate(fixed, campaign, history, duplicate_threshold):
        return None
    return fixed
