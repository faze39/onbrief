"""Text commands shared by the /onbrief slash command and `hermes onbrief`."""

from __future__ import annotations

import shlex
import shutil
from pathlib import Path

from .guard import Guard
from .rules import RulesError

EXAMPLES_DIR = Path(__file__).parent / "examples"

USAGE = """usage: onbrief <command>
  status                 mode, rules file, campaigns, active campaign
  check <text>           check a draft against the rules (nothing is sent)
  use <campaign>|auto    force one campaign, or go back to auto matching
  log [n]                last n guard decisions (default 10)
  forget                 clear the sent-post history used for duplicate checks
  examples               list bundled example rules files
  init <example>         copy a bundled example to the rules file path"""


def _examples() -> list[str]:
    return sorted(p.stem for p in EXAMPLES_DIR.glob("*.yaml"))


def render_report(report: dict) -> str:
    if not report["campaigns"]:
        return "no campaign applies to this text, so nothing to check"
    out = []
    for r in report["results"]:
        limit = f"/{r['max_length']}" if r["max_length"] is not None else ""
        if r["passed"]:
            out.append(f"PASS  {r['campaign']}  ({r['length']}{limit} chars)")
            continue
        out.append(f"BLOCK {r['campaign']}  ({r['length']}{limit} chars)")
        for v in r["violations"]:
            out.append(f"  x {v['detail']} -> {v['fix']}")
        if r["suggested_text"]:
            out.append("  suggested:")
            out.extend(("    " + ln).rstrip() for ln in r["suggested_text"].splitlines())
    return "\n".join(out)


def run(guard: Guard, raw: str) -> str:
    raw = (raw or "").strip()
    cmd, _, rest = raw.partition(" ")
    cmd = cmd.lower() or "status"
    rest = rest.strip()
    try:
        if cmd in ("help", "-h", "--help"):
            return USAGE
        if cmd == "status":
            path = guard.rules_path()
            lines = [f"mode: {guard.mode}", f"rules file: {path}"]
            if path.exists():
                camps = guard.load_campaigns()
                lines.append("campaigns: " + ", ".join(c.name for c in camps))
            else:
                lines.append("campaigns: none (no rules file yet; try `init <example>`)")
            lines.append(f"active: {guard.active_campaign() or 'auto'}")
            lines.append(f"guarded tools: {', '.join(guard.guarded_tools)}")
            lines.append(f"sent posts remembered: {len(guard.history())} "
                         f"(last {guard.duplicate_window_days} days)")
            return "\n".join(lines)
        if cmd == "check":
            if not rest:
                return "usage: onbrief check <text>"
            text = rest.replace("\\n", "\n")
            return render_report(guard.check_text(text))
        if cmd == "use":
            if not rest:
                return "usage: onbrief use <campaign>|auto"
            if rest == "auto":
                guard.set_active_campaign(None)
                return "campaign: auto (matched per post)"
            names = [c.name for c in guard.load_campaigns()]
            if rest not in names:
                return f"no campaign named '{rest}' (have: {', '.join(names)})"
            guard.set_active_campaign(rest)
            return f"campaign: {rest} (every guarded post is checked against it)"
        if cmd == "log":
            n = int(rest) if rest.isdigit() else 10
            items = guard.decisions()[-n:]
            if not items:
                return "no decisions yet"
            return "\n".join(
                f"{str(i['at'])[:16].replace('T', ' ')} UTC  {i['verdict']:<8} {i['tool']}  {','.join(i.get('campaigns') or []) or '-'}"
                f"  {i.get('preview', '')[:36]!r}" for i in items)
        if cmd == "forget":
            return f"cleared {guard.clear_history()} remembered posts"
        if cmd == "examples":
            return "\n".join(_examples())
        if cmd == "init":
            name = shlex.split(rest)[0] if rest else ""
            if name not in _examples():
                return f"usage: onbrief init <example>  (have: {', '.join(_examples())})"
            dest = guard.rules_path()
            if dest.exists():
                return f"{dest} already exists; edit it or remove it first"
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(EXAMPLES_DIR / f"{name}.yaml", dest)
            return f"wrote {dest} from example '{name}'"
        return USAGE
    except RulesError as exc:
        return f"rules error: {exc}"
