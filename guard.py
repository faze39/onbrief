"""The policy guard: decides whether an outbound tool call may run.

Hermes-independent so it can be unit tested; ``__init__.py`` wires it to the
plugin context (settings, state and hooks).
"""

from __future__ import annotations

import fnmatch
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Protocol

from . import checks
from .rules import Campaign, RulesError, load_file

logger = logging.getLogger(__name__)

DEFAULT_GUARDED_TOOLS = [
    "*create_post*", "*create_tweet*", "*post_tweet*", "*send_tweet*",
    "*publish*", "*send_message*", "*send_dm*",
]
DEFAULT_TEXT_FIELDS = ["text", "content", "message", "body", "caption", "status", "post", "tweet"]
MODES = ("block", "approve", "warn")
LOG_SIZE = 200


class StateStore(Protocol):
    def get(self, key: str, default: Any = None) -> Any: ...
    def set(self, key: str, value: Any) -> None: ...


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def extract_texts(args: Any, fields: list[str]) -> list[str]:
    """Every non-empty string under one of ``fields``, at any depth (threads, batches)."""
    wanted = {f.lower() for f in fields}
    found: list[str] = []

    def walk(node: Any, key: str | None) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, str(k).lower())
        elif isinstance(node, list):
            for v in node:
                walk(v, key)
        elif isinstance(node, str) and key in wanted and node.strip():
            found.append(node)

    walk(args, None)
    return found


def tool_matches(tool_name: str, patterns: list[str]) -> bool:
    name = (tool_name or "").lower()
    return any(fnmatch.fnmatchcase(name, p.lower()) for p in patterns)


class Guard:
    def __init__(self, setting: Callable[[str, Any], Any], state: StateStore | Callable[[], StateStore],
                 default_rules_path: Callable[[], Path], clock: Callable[[], datetime] = _now):
        self._setting = setting
        self._state = state
        self._default_rules_path = default_rules_path
        self._clock = clock

    @property
    def state(self) -> StateStore:
        # Accept a store or a zero-arg callable returning one, so the plugin can
        # resolve ctx.state lazily (it is not available during capability probes).
        return self._state() if callable(self._state) and not hasattr(self._state, "get") else self._state

    # settings ---------------------------------------------------------------
    @property
    def mode(self) -> str:
        mode = str(self._setting("mode", "block") or "block").lower()
        return mode if mode in MODES else "block"

    @property
    def guarded_tools(self) -> list[str]:
        v = self._setting("guarded_tools", None)
        return [str(x) for x in v] if isinstance(v, list) and v else list(DEFAULT_GUARDED_TOOLS)

    @property
    def text_fields(self) -> list[str]:
        v = self._setting("text_fields", None)
        return [str(x) for x in v] if isinstance(v, list) and v else list(DEFAULT_TEXT_FIELDS)

    @property
    def duplicate_threshold(self) -> float:
        try:
            v = float(self._setting("duplicate_similarity", 0.9))
        except (TypeError, ValueError):
            v = 0.9
        return min(max(v, 0.5), 1.0)

    @property
    def duplicate_window_days(self) -> int:
        try:
            return max(int(self._setting("duplicate_window_days", 30)), 0)
        except (TypeError, ValueError):
            return 30

    def rules_path(self) -> Path:
        configured = str(self._setting("rules_file", "") or "").strip()
        return Path(configured).expanduser() if configured else self._default_rules_path()

    def load_campaigns(self) -> list[Campaign]:
        return load_file(self.rules_path())

    # state ------------------------------------------------------------------
    def active_campaign(self) -> str | None:
        v = self.state.get("active_campaign", None)
        return v if isinstance(v, str) and v else None

    def set_active_campaign(self, name: str | None) -> None:
        self.state.set("active_campaign", name or "")

    def history(self) -> list[dict]:
        items = self.state.get("history", []) or []
        if not isinstance(items, list):
            return []
        days = self.duplicate_window_days
        if not days:
            return [i for i in items if isinstance(i, dict)]
        cutoff = self._clock() - timedelta(days=days)
        keep = []
        for item in items:
            try:
                if datetime.fromisoformat(item["at"]) >= cutoff:
                    keep.append(item)
            except (KeyError, TypeError, ValueError):
                continue
        return keep

    def clear_history(self) -> int:
        n = len(self.state.get("history", []) or [])
        self.state.set("history", [])
        return n

    def decisions(self) -> list[dict]:
        items = self.state.get("log", []) or []
        return items if isinstance(items, list) else []

    def _log(self, entry: dict) -> None:
        items = self.decisions()
        items.append(entry)
        self.state.set("log", items[-LOG_SIZE:])

    def record_sent(self, tool_name: str, text: str) -> None:
        items = self.state.get("history", []) or []
        if not isinstance(items, list):
            items = []
        items.append({"at": self._clock().isoformat(), "tool": tool_name,
                      "fp": checks.fingerprint(text), "norm": checks.normalize(text),
                      "preview": text[:80]})
        self.state.set("history", items[-500:])

    # evaluation -------------------------------------------------------------
    def campaigns_for(self, campaigns: list[Campaign], tool_name: str, text: str) -> list[Campaign]:
        active = self.active_campaign()
        if active:
            return [c for c in campaigns if c.name == active]
        picked = []
        for c in campaigns:
            if c.match_tools and not tool_matches(tool_name, c.match_tools):
                continue
            if c.match_contains_any and not any(k.lower() in text.lower() for k in c.match_contains_any):
                continue
            picked.append(c)
        return picked

    def check_text(self, text: str, tool_name: str = "", campaign_name: str | None = None,
                   campaigns: list[Campaign] | None = None) -> dict:
        campaigns = campaigns if campaigns is not None else self.load_campaigns()
        if campaign_name:
            chosen = [c for c in campaigns if c.name == campaign_name]
            if not chosen:
                raise RulesError(f"no campaign named '{campaign_name}' "
                                 f"(have: {', '.join(c.name for c in campaigns)})")
        else:
            chosen = self.campaigns_for(campaigns, tool_name, text)
        history = self.history()
        results = []
        for c in chosen:
            violations = checks.evaluate(text, c, history, self.duplicate_threshold)
            results.append({
                "campaign": c.name,
                "passed": not violations,
                "length": checks.length_of(text, c),
                "max_length": c.max_length,
                "violations": [v.as_dict() for v in violations],
                "suggested_text": checks.suggest_fix(text, c, history, self.duplicate_threshold)
                if violations else None,
            })
        return {"ok": all(r["passed"] for r in results), "campaigns": [c.name for c in chosen],
                "results": results}

    def pre_tool_call(self, tool_name: str, args: Any) -> dict | None:
        if not tool_matches(tool_name, self.guarded_tools):
            return None
        texts = extract_texts(args, self.text_fields)
        if not texts:
            return None
        try:
            campaigns = self.load_campaigns()
        except RulesError as exc:
            if not self.rules_path().exists():
                logger.warning("onbrief: no rules file at %s; %s not checked", self.rules_path(), tool_name)
                return None
            msg = f"onbrief blocked {tool_name}: the rules file is broken ({exc}). Fix it, then retry."
            self._log({"at": self._clock().isoformat(), "tool": tool_name, "verdict": "blocked",
                       "reason": "rules_error"})
            return {"action": "block", "message": msg}

        failures = []
        names: list[str] = []
        for idx, text in enumerate(texts):
            report = self.check_text(text, tool_name, campaigns=campaigns)
            names = report["campaigns"] or names
            for r in report["results"]:
                if not r["passed"]:
                    failures.append((idx, len(texts), r))
        entry = {"at": self._clock().isoformat(), "tool": tool_name, "campaigns": names,
                 "preview": texts[0][:80]}
        if not failures:
            self._log({**entry, "verdict": "allowed"})
            return None

        message = format_block(tool_name, failures, self.mode)
        verdict = {"block": "blocked", "approve": "sent_for_approval", "warn": "warned"}[self.mode]
        self._log({**entry, "verdict": verdict,
                   "violations": [v["rule"] for _, _, r in failures for v in r["violations"]]})
        if self.mode == "block":
            return {"action": "block", "message": message}
        if self.mode == "approve":
            return {"action": "approve", "message": message}
        logger.warning("%s", message)
        return None

    def post_tool_call(self, tool_name: str, args: Any, status: str | None) -> None:
        if status != "ok" or not tool_matches(tool_name, self.guarded_tools):
            return
        for text in extract_texts(args, self.text_fields):
            self.record_sent(tool_name, text)


def format_block(tool_name: str, failures: list[tuple[int, int, dict]], mode: str) -> str:
    head = {"block": "onbrief blocked", "approve": "onbrief needs approval for",
            "warn": "onbrief warning for"}[mode]
    lines = [f"{head} {tool_name}: the post breaks the campaign rules."]
    for idx, total, r in failures:
        where = f" (post {idx + 1} of {total})" if total > 1 else ""
        lines.append(f"campaign {r['campaign']}{where}:")
        for v in r["violations"]:
            lines.append(f"  x {v['detail']} -> {v['fix']}")
        if r.get("suggested_text"):
            lines.append("  suggested text that passes every rule:")
            lines.extend("    " + ln for ln in r["suggested_text"].splitlines())
    if mode == "block":
        lines.append("Rewrite the post to fix every item, then call the tool again.")
    return "\n".join(lines)
