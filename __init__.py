"""onbrief: keeps the agent's outbound posts on the campaign brief.

A pre_tool_call hook checks the text of guarded posting/messaging tools against a
rules file and blocks (or sends to approval) any post that breaks a rule.
"""

from __future__ import annotations

import json
import logging

from . import commands, schemas
from .guard import Guard
from .rules import RulesError

logger = logging.getLogger(__name__)
PLUGIN = "onbrief"


def _make_guard(ctx) -> Guard:
    from plugins.plugin_storage import plugin_data_dir

    return Guard(
        setting=lambda key, default=None: ctx.get_config(key, default=default),
        state=lambda: ctx.state,
        default_rules_path=lambda: plugin_data_dir(PLUGIN) / "rules.yaml",
    )


def register(ctx):
    guard = _make_guard(ctx)

    def on_pre_tool_call(tool_name="", args=None, **kwargs):
        return guard.pre_tool_call(tool_name, args or {})

    def on_post_tool_call(tool_name="", args=None, status=None, **kwargs):
        guard.post_tool_call(tool_name, args or {}, status)

    def onbrief_check(args, **kwargs):
        text = (args or {}).get("text") or ""
        if not text.strip():
            return json.dumps({"error": "text is required"})
        try:
            return json.dumps(guard.check_text(text, campaign_name=(args or {}).get("campaign")))
        except RulesError as exc:
            return json.dumps({"error": f"rules error: {exc}"})
        except Exception as exc:  # never raise into the agent loop
            logger.exception("onbrief_check failed")
            return json.dumps({"error": f"onbrief_check failed: {exc}"})

    ctx.register_hook("pre_tool_call", on_pre_tool_call)
    ctx.register_hook("post_tool_call", on_post_tool_call)
    ctx.register_tool(name="onbrief_check", toolset="onbrief", schema=schemas.ONBRIEF_CHECK,
                      handler=onbrief_check, description="Check a draft post against campaign rules")
    ctx.register_command("onbrief", handler=lambda raw="", **kw: commands.run(guard, raw),
                         description="Campaign post rules: status, check, use, log",
                         args_hint="<status|check|use|log|forget|examples|init>")

    def _setup(subparser):
        subparser.add_argument("words", nargs="*", help="status | check <text> | use <name> | log | init <example>")
        subparser.set_defaults(func=_cli)

    def _cli(args):
        print(commands.run(guard, " ".join(getattr(args, "words", []) or [])))

    ctx.register_cli_command(name="onbrief", help="Campaign post rules for outbound posts",
                             setup_fn=_setup, handler_fn=_cli)
