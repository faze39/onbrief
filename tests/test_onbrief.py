from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from onbrief import checks, commands
from onbrief.guard import Guard, extract_texts, tool_matches
from onbrief.rules import RulesError, load_file, load_text
from onbrief.xlength import x_length

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


class MemState:
    def __init__(self):
        self.d = {}

    def get(self, key, default=None):
        return self.d.get(key, default)

    def set(self, key, value):
        self.d[key] = value


def make_guard(tmp_path, rules=None, example=None, **settings):
    path = tmp_path / "rules.yaml"
    if example:
        path.write_text((EXAMPLES / f"{example}.yaml").read_text())
    elif rules is not None:
        path.write_text(rules)
    clock = {"now": datetime(2026, 11, 11, 18, 0, tzinfo=timezone.utc)}
    g = Guard(setting=lambda k, d=None: settings.get(k, d), state=MemState(),
              default_rules_path=lambda: path, clock=lambda: clock["now"])
    return g, clock


GOOD_COSPLAY = ("ask before you film a cosplayer, shoot at eye level, then share the shot with them\n\n"
                "#CosplayAndCameras #FilmingEtiquette")


# rules -----------------------------------------------------------------------

def test_examples_load():
    for p in EXAMPLES.glob("*.yaml"):
        assert load_file(p)


def test_unknown_key_rejected():
    with pytest.raises(RulesError, match="unknown keys"):
        load_text("campaigns:\n  - name: a\n    requried_hashtags: [x]\n")


def test_bad_regex_rejected():
    with pytest.raises(RulesError, match="valid regex"):
        load_text("campaigns:\n  - name: a\n    banned_patterns:\n      - pattern: '('\n")


def test_json_rules_and_tag_normalising():
    camps = load_text('{"campaigns": [{"name": "a", "required_hashtags": ["Tag"], '
                      '"required_mentions": ["NousResearch"]}]}', ".json")
    assert camps[0].required_hashtags == ["#Tag"]
    assert camps[0].required_mentions == ["@NousResearch"]


def test_duplicate_campaign_names_rejected():
    with pytest.raises(RulesError, match="duplicate"):
        load_text("campaigns:\n  - name: a\n  - name: a\n")


# checks ------------------------------------------------------------------------

def test_x_length_counts_urls_as_23_and_wide_chars_as_2():
    assert x_length("hi https://example.com/a/very/long/path/that/goes/on") == 3 + 23
    assert x_length("日本") == 4
    assert x_length("abc") == 3


def test_cosplay_rules():
    camp = load_file(EXAMPLES / "cosplay-etiquette.yaml")[0]
    assert checks.evaluate(GOOD_COSPLAY, camp) == []
    bad = "kids at the cosplay con, film them however you like \u2014 #CosplayAndCameras"
    rules = {v.rule for v in checks.evaluate(bad, camp)}
    assert rules == {"required_hashtag", "banned_word", "banned_pattern"}


def test_banned_word_whole_word_and_case_insensitive():
    camp = load_text("campaigns:\n  - name: a\n    banned_words: [kids]\n")[0]
    assert checks.evaluate("KIDS everywhere", camp)
    assert checks.evaluate("#kids", camp)
    assert not checks.evaluate("kidsmeal is one word", camp)


def test_hashtag_match_is_whole_tag():
    camp = load_text("campaigns:\n  - name: a\n    required_hashtags: [Cosplay]\n")[0]
    assert checks.evaluate("#CosplayAndCameras", camp)
    assert not checks.evaluate("#cosplay", camp)


def test_suggest_fix_appends_tags_and_replaces_dash():
    camp = load_file(EXAMPLES / "hermes-hackathon-demo.yaml")[0]
    draft = "my plugin blocks off-brief posts \u2014 github.com/faze39/onbrief"
    fixed = checks.suggest_fix(draft, camp)
    assert fixed is not None
    assert "@NousResearch" in fixed and "\u2014" not in fixed
    assert checks.evaluate(fixed, camp) == []


def test_suggest_fix_refuses_banned_words():
    camp = load_file(EXAMPLES / "cosplay-etiquette.yaml")[0]
    assert checks.suggest_fix("film the kids at cosplay", camp) is None


def test_length_limit_and_links():
    camp = load_text("campaigns:\n  - name: a\n    max_length: 10\n    require_link: true\n"
                     "    allowed_link_domains: [github.com]\n")[0]
    rules = {v.rule for v in checks.evaluate("this is far too long without a link", camp)}
    assert rules == {"max_length", "require_link"}
    rules = {v.rule for v in checks.evaluate("see evil.example.com", camp)}
    assert "link_domain" in rules
    assert not {v.rule for v in checks.evaluate("github.com/a", camp)} & {"link_domain", "require_link"}


# guard -------------------------------------------------------------------------

def test_extract_texts_nested_and_tool_globs():
    args = {"posts": [{"text": "one"}, {"text": "two"}], "media": {"caption": "three"}, "id": "x"}
    assert extract_texts(args, ["text", "caption"]) == ["one", "two", "three"]
    assert tool_matches("mcp__x__create_post", ["*create_post*"])
    assert not tool_matches("read_file", ["*create_post*"])


def test_guard_blocks_then_allows_fixed_post(tmp_path):
    g, _ = make_guard(tmp_path, example="cosplay-etiquette")
    bad = {"text": "film the kids at cosplay events #CosplayAndCameras"}
    d = g.pre_tool_call("mcp__x__create_post", bad)
    assert d["action"] == "block"
    assert "missing #FilmingEtiquette" in d["message"]
    assert 'banned word "kids"' in d["message"]
    assert g.pre_tool_call("mcp__x__create_post", {"text": GOOD_COSPLAY}) is None
    assert [e["verdict"] for e in g.decisions()] == ["blocked", "allowed"]


def test_unguarded_tools_and_unmatched_text_pass(tmp_path):
    g, _ = make_guard(tmp_path, example="cosplay-etiquette")
    assert g.pre_tool_call("write_file", {"content": "kids"}) is None
    assert g.pre_tool_call("mcp__x__create_post", {"text": "nothing about the campaign"}) is None


def test_duplicate_blocked_after_send_and_expires(tmp_path):
    g, clock = make_guard(tmp_path, example="cosplay-etiquette", duplicate_window_days=7)
    assert g.pre_tool_call("outbox_create_post", {"text": GOOD_COSPLAY}) is None
    g.post_tool_call("outbox_create_post", {"text": GOOD_COSPLAY}, "ok")
    again = GOOD_COSPLAY.replace("then", "and then")
    d = g.pre_tool_call("outbox_create_post", {"text": again})
    assert d and "the same as a post sent" in d["message"]
    clock["now"] += timedelta(days=8)
    assert g.pre_tool_call("outbox_create_post", {"text": again}) is None


def test_failed_send_is_not_remembered(tmp_path):
    g, _ = make_guard(tmp_path, example="cosplay-etiquette")
    g.post_tool_call("outbox_create_post", {"text": GOOD_COSPLAY}, "error")
    assert g.history() == []


def test_thread_posts_each_checked(tmp_path):
    g, _ = make_guard(tmp_path, example="cosplay-etiquette")
    d = g.pre_tool_call("x_create_posts", {"posts": [{"text": GOOD_COSPLAY},
                                                     {"text": "cosplay part two, kids welcome"}]})
    assert d and "post 2 of 2" in d["message"]


def test_modes(tmp_path):
    bad = {"text": "cosplay with kids"}
    g, _ = make_guard(tmp_path, example="cosplay-etiquette", mode="approve")
    assert g.pre_tool_call("x_create_post", bad)["action"] == "approve"
    g, _ = make_guard(tmp_path, example="cosplay-etiquette", mode="warn")
    assert g.pre_tool_call("x_create_post", bad) is None
    assert g.decisions()[-1]["verdict"] == "warned"


def test_broken_rules_fail_closed_missing_rules_pass(tmp_path):
    g, _ = make_guard(tmp_path, rules="campaigns: nope")
    d = g.pre_tool_call("x_create_post", {"text": "hi"})
    assert d["action"] == "block" and "rules file is broken" in d["message"]
    g2 = Guard(setting=lambda k, d=None: d, state=MemState(),
               default_rules_path=lambda: tmp_path / "missing.yaml")
    assert g2.pre_tool_call("x_create_post", {"text": "hi"}) is None


def test_active_campaign_overrides_matching(tmp_path):
    g, _ = make_guard(tmp_path, example="multi-campaign")
    text = "new hermes plugin is live github.com/faze39/onbrief"
    assert {r["campaign"] for r in g.check_text(text)["results"]} == {"hermes-hackathon", "house-style"}
    g.set_active_campaign("cosplay-etiquette")
    assert g.check_text(text)["campaigns"] == ["cosplay-etiquette"]


def test_commands(tmp_path):
    g, _ = make_guard(tmp_path)
    assert "no rules file" in commands.run(g, "status")
    assert "wrote" in commands.run(g, "init cosplay-etiquette")
    out = commands.run(g, "check film the kids at cosplay")
    assert out.startswith("BLOCK cosplay-etiquette")
    assert commands.run(g, "check " + GOOD_COSPLAY.replace("\n", "\\n")).startswith("PASS")
    assert "campaign: cosplay-etiquette" in commands.run(g, "use cosplay-etiquette")
    assert "no campaign named" in commands.run(g, "use nope")
