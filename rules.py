"""Load and validate an onbrief rules file (YAML or JSON).

A rules file holds one or more campaigns. Each campaign lists what an outbound
post must contain and must not contain. Unknown keys are rejected so a typo in
a rule name fails loudly instead of silently turning that rule off.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

CAMPAIGN_KEYS = {
    "name", "description", "match", "required_hashtags", "required_mentions",
    "required_phrases", "banned_words", "banned_patterns", "max_length",
    "length_counting", "max_hashtags", "require_link", "required_links",
    "allowed_link_domains", "no_duplicates",
}
MATCH_KEYS = {"tools", "contains_any"}


class RulesError(ValueError):
    """The rules file is missing, unreadable or malformed."""


@dataclass
class BannedPattern:
    pattern: re.Pattern
    label: str
    replace_with: str | None = None


@dataclass
class Campaign:
    name: str
    description: str = ""
    match_tools: list[str] = field(default_factory=list)
    match_contains_any: list[str] = field(default_factory=list)
    required_hashtags: list[str] = field(default_factory=list)
    required_mentions: list[str] = field(default_factory=list)
    required_phrases: list[str] = field(default_factory=list)
    banned_words: list[str] = field(default_factory=list)
    banned_patterns: list[BannedPattern] = field(default_factory=list)
    max_length: int | None = None
    length_counting: str = "x"
    max_hashtags: int | None = None
    require_link: bool = False
    required_links: list[str] = field(default_factory=list)
    allowed_link_domains: list[str] = field(default_factory=list)
    no_duplicates: bool = True


def _str_list(value: Any, where: str) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list) or not all(isinstance(v, str) and v.strip() for v in value):
        raise RulesError(f"{where} must be a list of non-empty strings")
    return [v.strip() for v in value]


def _norm_tag(tag: str, prefix: str) -> str:
    tag = tag.strip()
    return tag if tag.startswith(prefix) else prefix + tag


def _campaign(raw: Any, index: int) -> Campaign:
    if not isinstance(raw, dict):
        raise RulesError(f"campaigns[{index}] must be a mapping")
    unknown = set(raw) - CAMPAIGN_KEYS
    if unknown:
        raise RulesError(f"campaigns[{index}] has unknown keys: {', '.join(sorted(unknown))}")
    name = raw.get("name")
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", name or ""):
        raise RulesError(f"campaigns[{index}].name must be 1-64 chars of letters, digits, . _ -")
    where = f"campaign '{name}'"
    match = raw.get("match") or {}
    if not isinstance(match, dict) or set(match) - MATCH_KEYS:
        raise RulesError(f"{where}: match may only contain {sorted(MATCH_KEYS)}")

    patterns = []
    for j, item in enumerate(raw.get("banned_patterns") or []):
        if not isinstance(item, dict) or "pattern" not in item:
            raise RulesError(f"{where}: banned_patterns[{j}] needs a 'pattern'")
        try:
            rx = re.compile(item["pattern"], re.IGNORECASE)
        except re.error as exc:
            raise RulesError(f"{where}: banned_patterns[{j}] is not a valid regex: {exc}") from exc
        replace = item.get("replace_with")
        if replace is not None and not isinstance(replace, str):
            raise RulesError(f"{where}: banned_patterns[{j}].replace_with must be a string")
        patterns.append(BannedPattern(rx, str(item.get("label") or item["pattern"]), replace))

    def _opt_int(key: str) -> int | None:
        v = raw.get(key)
        if v is None:
            return None
        if not isinstance(v, int) or isinstance(v, bool) or v < 0:
            raise RulesError(f"{where}: {key} must be a non-negative integer")
        return v

    counting = raw.get("length_counting", "x")
    if counting not in ("x", "chars"):
        raise RulesError(f"{where}: length_counting must be 'x' or 'chars'")

    for key in ("require_link", "no_duplicates"):
        if key in raw and not isinstance(raw[key], bool):
            raise RulesError(f"{where}: {key} must be true or false")

    return Campaign(
        name=name,
        description=str(raw.get("description") or ""),
        match_tools=_str_list(match.get("tools"), f"{where}: match.tools"),
        match_contains_any=_str_list(match.get("contains_any"), f"{where}: match.contains_any"),
        required_hashtags=[_norm_tag(t, "#") for t in _str_list(raw.get("required_hashtags"), f"{where}: required_hashtags")],
        required_mentions=[_norm_tag(t, "@") for t in _str_list(raw.get("required_mentions"), f"{where}: required_mentions")],
        required_phrases=_str_list(raw.get("required_phrases"), f"{where}: required_phrases"),
        banned_words=_str_list(raw.get("banned_words"), f"{where}: banned_words"),
        banned_patterns=patterns,
        max_length=_opt_int("max_length"),
        length_counting=counting,
        max_hashtags=_opt_int("max_hashtags"),
        require_link=bool(raw.get("require_link", False)),
        required_links=_str_list(raw.get("required_links"), f"{where}: required_links"),
        allowed_link_domains=[d.lower() for d in _str_list(raw.get("allowed_link_domains"), f"{where}: allowed_link_domains")],
        no_duplicates=bool(raw.get("no_duplicates", True)),
    )


def parse_rules(data: Any) -> list[Campaign]:
    if not isinstance(data, dict) or not isinstance(data.get("campaigns"), list) or not data["campaigns"]:
        raise RulesError("rules file must be a mapping with a non-empty 'campaigns' list")
    campaigns = [_campaign(c, i) for i, c in enumerate(data["campaigns"])]
    names = [c.name for c in campaigns]
    dupes = {n for n in names if names.count(n) > 1}
    if dupes:
        raise RulesError(f"duplicate campaign names: {', '.join(sorted(dupes))}")
    return campaigns


def load_text(text: str, suffix: str = ".yaml") -> list[Campaign]:
    if suffix.lower() == ".json":
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise RulesError(f"invalid JSON: {exc}") from exc
    else:
        from ruamel.yaml import YAML
        from ruamel.yaml.error import YAMLError
        try:
            data = YAML(typ="safe", pure=True).load(text)
        except YAMLError as exc:
            raise RulesError(f"invalid YAML: {exc}") from exc
    return parse_rules(data)


def load_file(path: str | Path) -> list[Campaign]:
    p = Path(path).expanduser()
    if not p.is_file():
        raise RulesError(f"rules file not found: {p}")
    try:
        text = p.read_text(encoding="utf-8")
    except OSError as exc:
        raise RulesError(f"cannot read rules file {p}: {exc}") from exc
    return load_text(text, p.suffix)
