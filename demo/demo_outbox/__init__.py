"""Demo only. A fake 'create post' tool so onbrief can be shown without a real
social account. It writes to <hermes home>/plugin-data/demo_outbox/outbox.jsonl."""

import json
from datetime import datetime, timezone

SCHEMA = {
    "name": "outbox_create_post",
    "description": "Publish a social post (demo: appends it to a local outbox file).",
    "parameters": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
}


def _post(args, **kwargs):
    from plugins.plugin_storage import plugin_data_dir

    path = plugin_data_dir("demo_outbox") / "outbox.jsonl"
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"at": datetime.now(timezone.utc).isoformat(), "text": args.get("text", "")}) + "\n")
    return json.dumps({"posted": True, "outbox": str(path)})


def register(ctx):
    ctx.register_tool(name="outbox_create_post", toolset="demo_outbox", schema=SCHEMA, handler=_post)
