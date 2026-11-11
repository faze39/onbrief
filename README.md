# onbrief

A Hermes Agent plugin that keeps outbound posts on the campaign brief.

When the agent calls a posting or messaging tool, onbrief reads the post text
from the call, checks it against your rules file and blocks the call if any
rule fails. The agent gets every failing rule with a fix (and a ready-to-use
rewrite when the fix is mechanical), rewrites the post and tries again. Posts
that pass go through, and onbrief remembers them so a near copy is caught next
time.

It is a `pre_tool_call` hook, so it does not depend on the model remembering
the brief. A prompt or a skill can ask the model to add the hashtags; onbrief
refuses the call when it does not.

```
$ hermes chat -q "post our cosplay filming etiquette update"
⚡ Outbox_create_post ask before you film a cosplayer. shoot at eye level, then send them the shot ...
☤ Hermes
onbrief blocked my first draft:
- missing #FilmingEtiquette -> add #FilmingEtiquette
- contains banned word "kids" -> rewrite without "kids"
- contains an em dash -> replace with ", "
I rewrote it and the fixed post went out.
```

(Output from `tests/test_hermes_e2e.py`, which runs the real Hermes agent loop
with a scripted local model and a demo outbox tool. See [Demo](#demo).)

## What it checks

Per campaign, all optional:

| Rule | Example | Notes |
|---|---|---|
| `required_hashtags` | `["#CosplayAndCameras", "#FilmingEtiquette"]` | whole tag, case-insensitive (`#Cosplay` does not satisfy `#CosplayAndCameras` and the other way round) |
| `required_mentions` | `["@NousResearch"]` | case-insensitive |
| `required_phrases` | `["built at"]` | substring, case-insensitive |
| `banned_words` | `[children, kids]` | whole word, case-insensitive, also inside hashtags (`#kids`) |
| `banned_patterns` | `{pattern: "\\u2014", label: "an em dash", replace_with: ", "}` | regex; `replace_with` lets onbrief offer a fixed text |
| `max_length` + `length_counting` | `280`, `x` | `x` uses X's weighted count (links count 23, CJK and emoji count 2); `chars` counts code points |
| `max_hashtags` | `4` | |
| `require_link` / `required_links` | `true` / `["github.com/"]` | |
| `allowed_link_domains` | `[github.com]` | subdomains allowed |
| `no_duplicates` | `true` (default) | blocks a post at or above `duplicate_similarity` to one sent in the last `duplicate_window_days` |

A campaign applies to a post when its `match` block fits (`tools`: tool name
globs, `contains_any`: words in the text), or always when it has no `match`.
`/onbrief use <name>` forces one campaign until `/onbrief use auto`.

Unknown keys, bad regexes and duplicate campaign names are rejected when the
file loads, so a typo cannot silently switch a rule off. If the rules file is
present but broken, guarded calls are blocked (fail closed) with the parse
error. If there is no rules file yet, guarded calls pass and a warning is
logged.

## Install

```bash
hermes plugins install https://github.com/faze39/onbrief
hermes plugins enable onbrief
hermes onbrief init cosplay-etiquette      # or hermes-hackathon-demo, multi-campaign
hermes onbrief status
```

Then edit the rules file `status` prints
(`<hermes home>/plugin-data/onbrief/rules.yaml` by default) or point the
`rules_file` setting at your own YAML or JSON file.

## Which tools are guarded

Any tool whose name matches `guarded_tools` (globs, MCP tools included). The
default covers common names: `*create_post*`, `*create_tweet*`,
`*post_tweet*`, `*send_tweet*`, `*publish*`, `*send_message*`, `*send_dm*`.
The text is read from argument names in `text_fields` (`text`, `content`,
`message`, `body`, `caption`, `status`, `post`, `tweet`) at any depth, so a
thread (`posts: [{text: ...}, ...]`) has every post checked.

If your posting tool uses other names, add them:

```bash
hermes config set plugins.entries.onbrief.settings.guarded_tools '["mcp__social__*_publish", "*create_post*"]'
```

onbrief does not post anything itself and ships no posting integration. It
guards whatever posting tool you already have connected.

## Settings

| Key | Default | |
|---|---|---|
| `rules_file` | `""` | empty uses `<hermes home>/plugin-data/onbrief/rules.yaml` |
| `mode` | `block` | `block` refuses the call; `approve` sends it to the Hermes approval prompt with the reasons (Hermes fails closed where nobody can answer, such as cron); `warn` only logs |
| `guarded_tools` | see above | |
| `text_fields` | see above | |
| `duplicate_similarity` | `0.9` | 0.5 to 1.0, difflib ratio on normalised text (links removed) |
| `duplicate_window_days` | `30` | 0 compares against everything remembered |

## Commands

The same commands work as `/onbrief ...` in a session and `hermes onbrief ...`
in a terminal.

```
status                 mode, rules file, campaigns, active campaign
check <text>           check a draft against the rules (nothing is sent)
use <campaign>|auto    force one campaign, or go back to auto matching
log [n]                last n guard decisions
forget                 clear the sent-post history used for duplicate checks
examples               list bundled example rules files
init <example>         copy a bundled example to the rules file path
```

The model also gets an `onbrief_check` tool to check a draft before sending.
It sends nothing and returns JSON with each failing rule.

## Disclosure

- No network calls, no model calls, no shell commands, no background
  processes, no telemetry.
- Reads the rules file you configure (the default lives in the plugin's own
  data directory). Reads nothing else outside its own data.
- Stores in its Hermes plugin state (`ctx.state`, profile-scoped): the active
  campaign, the last 200 guard decisions (time, tool, campaign, verdict, the
  first 80 characters of the post) and the last 500 sent posts (time, tool, a
  hash, normalised text for similarity, the first 80 characters).
  `onbrief forget` clears the sent-post history.
- Only posts whose tool call returned `ok` are remembered.
- No credentials are used or stored.

## Limits

- Text is read from the argument names above. A tool that puts the post text
  somewhere else is not checked until you add that name to `text_fields`.
- `contains_any` matching is a plain substring match.
- X length is the weighted count from X's published rules. Multi-code-point
  emoji (skin tones, ZWJ sequences) are counted per code point, so they can
  count longer than X does; that errs on the side of blocking.
- Duplicate detection compares text only, not media.

## Demo

`demo/` holds what the demo and the end-to-end test use, and none of it loads
with the plugin:

- `demo_outbox/`: a stand-in posting tool, `outbox_create_post`, that appends
  posts to a local file instead of publishing.
- `scripted_model.py`: a small OpenAI-compatible endpoint that plays a fixed
  script (post a bad draft, then the fixed one), so the real Hermes agent loop
  runs without an API key. With a real model the agent writes its own rewrite
  from onbrief's message.

## Tests

```bash
pip install pytest "ruamel.yaml>=0.18,<0.19"
pytest -q                                    # unit tests
ONBRIEF_E2E=1 pytest -q tests/test_hermes_e2e.py   # real Hermes loop, needs hermes on PATH
hermes plugins validate . --install-deps
hermes plugins doctor . --ci
```

Requires Hermes 0.23.0 or newer. Tested on Hermes v0.23.1.

## License

MIT
