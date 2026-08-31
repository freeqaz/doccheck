"""Regression pins for doccheck.

The `phrases` block below pins the retired-wording rule's windowing. Both
false-positive cases were live findings on a real tree: the gating run reported
two, and in both the prose was correct. Satisfying the check as written would
have meant deleting a correct repudiation and mangling a correct sentence about
an unrelated sense of the same word -- the exact failure mode where a lint
pushes an author to make a document worse.
"""

import json
import re

import doccheck

#: An example project vocabulary, standing in for whatever a real project
#: retires. "only a proxy" is wrong when it is said about *coverage* and
#: ordinary English about a network proxy, which is what `requires` is for.
EXAMPLE_PHRASES = {
    "phrases": [
        {"pattern": r"only a proxy", "label": '"coverage is only a proxy"',
         "requires": r"coverage"},
        {"pattern": r"the only measure of quality",
         "label": '"the only measure of quality"'},
    ],
    "phrases_allow": r"misreading|is a valid metric|first-class|retired|superseded",
}


def phrase_hits(text, cfg=EXAMPLE_PHRASES):
    """Run just the phrase rule over `text`, returning (line_no, label) pairs."""
    with doccheck.config_applied(cfg):
        lines = text.splitlines()
        hits = []
        for i, line in enumerate(lines, 1):
            ctx = doccheck.context_window(lines, i - 1)
            if doccheck.LOGLINE_RE.match(line) or doccheck.PHRASE_ALLOW.search(ctx):
                continue
            for pat, label, requires in doccheck.PHRASE_PATTERNS:
                if re.search(pat, line, re.I) and (
                        requires is None or re.search(requires, ctx, re.I)):
                    hits.append((i, label))
                    break
        return hits


# --- the phrase rule is inert until it is configured ------------------------

def test_no_phrases_are_configured_by_default():
    """A retired-wording list belongs to one project, so this ships empty."""
    assert doccheck.PHRASE_PATTERNS == []
    assert doccheck.STALE_PATTERNS == []
    assert phrase_hits("Coverage is only a proxy for quality.\n", cfg={}) == []


def test_configured_phrases_reach_the_check_end_to_end(tmp_path):
    (tmp_path / "README.md").write_text(
        "# root\n\nCoverage is only a proxy, so we ignore it.\n")
    findings = doccheck.check(str(tmp_path), max_lines=10_000,
                              config=EXAMPLE_PHRASES)
    assert [x["line"] for x in findings["phrases"]] == [3], findings["phrases"]


def test_config_is_restored_after_the_block():
    """Config is applied by mutating module globals, so leaking it would make
    one test's vocabulary another test's silent failure."""
    with doccheck.config_applied({"hubs": ["x.md"]}):
        assert doccheck.HUBS == ("x.md",)
    assert doccheck.HUBS == ("README.md", "docs/README.md")


def test_an_unknown_config_key_is_an_error_not_a_silent_no_op():
    """A typo'd key would disable the rule its author meant to write, and that
    reads as a clean tree."""
    try:
        doccheck.apply_config({"phraze": []})
    except ValueError as exc:
        assert "phraze" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_config_file_beside_the_root_is_picked_up(tmp_path):
    (tmp_path / doccheck.DEFAULT_CONFIG_NAME).write_text(json.dumps(EXAMPLE_PHRASES))
    cfg = doccheck.load_config(str(tmp_path))
    assert cfg["phrases"][0]["requires"] == "coverage"
    assert doccheck.load_config(str(tmp_path), use_default=False) == {}


# --- the two false positives that motivated the windowed allow-list ---------

def test_repudiation_wrapped_onto_next_line_is_not_a_violation():
    """The trigger and its exculpating clause are on different lines because the
    prose is hard-wrapped. A line-scoped allow-list cannot see the rescue.
    """
    text = (
        '**What this is NOT.** It is not "coverage is only a proxy" -- that is a\n'
        "misreading. Coverage did the work here: the whole verdict turns on a\n"
        "distribution and its deltas.\n"
    )
    assert phrase_hits(text) == []


def test_the_unrelated_sense_of_the_same_words_is_not_the_retired_framing():
    """"only a proxy" here is an HTTP proxy -- ordinary usage, and the sense the
    `requires` context deliberately carves out."""
    text = (
        "The build reaches the package index through only a proxy cache, so a\n"
        "cold machine never talks to the network directly.\n"
    )
    assert phrase_hits(text) == []


# --- the rule must still catch what it exists to catch ----------------------

def test_real_violation_still_fires():
    text = "We treat coverage as only a proxy, so a 30->99 climb is not progress.\n"
    hits = phrase_hits(text)
    assert len(hits) == 1
    assert hits[0][0] == 1


def test_real_violation_fires_when_the_required_word_is_on_a_neighbouring_line():
    """The context requirement is windowed, so a wrapped violation is still
    caught -- the requirement narrows the unrelated sense, it is not an escape
    hatch for splitting a sentence across two lines."""
    text = (
        "The team keeps quoting coverage in the readouts, which is a mistake:\n"
        "it is only a proxy and nothing more.\n"
    )
    hits = phrase_hits(text)
    assert len(hits) == 1
    assert hits[0][0] == 2


def test_patterns_without_a_requirement_are_unconditional():
    text = "Byte counts are the only measure of quality we report.\n"
    assert len(phrase_hits(text)) == 1


def test_allow_window_is_bounded():
    """An unrelated 'superseded' far enough away must not rescue a violation --
    otherwise the windowed allow-list would silently disarm the rule on any
    page that mentions a superseded doc."""
    filler = "\n".join(f"filler line {n}" for n in range(doccheck.ALLOW_RADIUS + 3))
    text = f"This claim is superseded.\n{filler}\nWe treat coverage as only a proxy.\n"
    assert len(phrase_hits(text)) == 1


# --- links to deliberately-gitignored artifacts ----------------------------

def test_gitignored_targets_are_filtered_from_links_and_assets(tmp_path):
    """A link to an ignored path is a pointer to a regenerable artifact.

    Without this the same tree is green in a working checkout (where the
    artifact happens to sit on disk) and red in CI (where it never does) --
    findings no author can act on, in the same gate as real ones.
    """
    import subprocess as sp
    sp.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / ".gitignore").write_text("papers/\nlogs/\n")
    (tmp_path / "README.md").write_text(
        "# root\n\n"
        "- [paper](papers/x.pdf)\n"     # ignored asset -> filtered
        "- [logs](logs/)\n"             # ignored dir link -> filtered
        "- [real](missing/y.pdf)\n"     # not ignored -> still reported
    )
    findings = doccheck.check(str(tmp_path), max_lines=10_000)
    msgs = [x["msg"] for x in findings["assets"] + findings["links"]]
    assert not any("x.pdf" in m for m in msgs), msgs
    assert not any("logs/" in m for m in msgs), msgs
    assert any("y.pdf" in m for m in msgs), msgs


def test_gitignored_degrades_safely_outside_a_repo(tmp_path):
    """No git, no repo, no crash -- and nothing silently filtered."""
    assert doccheck.gitignored({"anything"}, str(tmp_path)) == set()


def test_gitignored_on_empty_input_makes_no_subprocess_call():
    assert doccheck.gitignored(set(), ".") == set()


# --- links out of the repo tree entirely ------------------------------------

def test_out_of_tree_links_are_pointers_not_findings(tmp_path):
    """Linking into a sibling repo is common convention. Whether that repo is
    checked out beside us is a property of the machine, not the documentation:
    verifying it made the same tree green on a dev box and red in CI."""
    (tmp_path / "README.md").write_text(
        "# root\n\n"
        "- [sibling doc](../sibling/WHERE.md)\n"       # md link -> skipped
        "- [sibling asset](../sibling/runs/a.json)\n"  # asset -> skipped
        "- [sibling dir](../sibling/runs/)\n"          # dir link -> skipped
        "- [real](missing.md)\n"                       # in-tree -> reported
    )
    findings = doccheck.check(str(tmp_path), max_lines=10_000)
    msgs = [x["msg"] for x in findings["assets"] + findings["links"]]
    assert not any("sibling" in m for m in msgs), msgs
    assert any("missing.md" in m for m in msgs), msgs


def test_out_of_tree_is_about_escaping_root_not_containing_dotdot(tmp_path):
    """`a/../b.md` normalizes inside the tree and must still be verified."""
    (tmp_path / "README.md").write_text("[stays inside](a/../gone.md)\n")
    findings = doccheck.check(str(tmp_path), max_lines=10_000)
    assert any("gone.md" in x["msg"] for x in findings["links"])


# --- links inside inline code spans -----------------------------------------

def test_code_span_link_shape_is_quotation_not_a_link(tmp_path):
    """Verbatim shape of a real index row QUOTING a link-shaped code fragment,
    which produced two phantom asset findings."""
    (tmp_path / "README.md").write_text(
        "# root\n\n"
        "| C3 | Every relative **markdown link** (`[…](…)`) resolves — the\n"
        "single hit is `[m.config.model_type](model=m)`, a code fragment. |\n"
    )
    findings = doccheck.check(str(tmp_path), max_lines=10_000)
    assert findings["assets"] == [], findings["assets"]
    assert findings["links"] == [], findings["links"]


def test_real_link_beside_a_code_span_still_fires(tmp_path):
    (tmp_path / "README.md").write_text(
        "See `[quoted](fake.md)` and the real [broken](absent.md) link.\n"
    )
    findings = doccheck.check(str(tmp_path), max_lines=10_000)
    msgs = [x["msg"] for x in findings["links"]]
    assert not any("fake.md" in m for m in msgs), msgs
    assert any("absent.md" in m for m in msgs), msgs


# --- absolute machine paths in doc bodies -----------------------------------

def test_abspath_fires_on_a_machine_path_including_inside_a_code_fence(tmp_path):
    """A machine path in a runnable block is the copy-paste nobody else can run,
    so the check reads the whole line -- same rule as phrases/stale."""
    (tmp_path / "README.md").write_text(
        "**Hub:** root\n\n"
        "The default is `/home/someone/code/proj`.\n"      # prose
        "```bash\n"
        "cd /Users/someone/code/proj/tools\n"              # fenced
        "```\n"
    )
    findings = doccheck.check(str(tmp_path), max_lines=10_000)
    assert {x["line"] for x in findings["abspath"]} == {3, 5}, findings["abspath"]


def test_abspath_ignores_placeholders_and_portable_spellings(tmp_path):
    (tmp_path / "README.md").write_text(
        "**Hub:** root\n\n"
        "- `/home/<user>/code/proj` — substitute your own checkout\n"
        "- `/Users/$USER/code/proj`\n"
        "- `~/code/proj` and `$HOME/code/proj`\n"
        "- `docs/guides/README.md`, and /home is not a path on its own\n"
    )
    findings = doccheck.check(str(tmp_path), max_lines=10_000)
    assert findings["abspath"] == [], findings["abspath"]


def test_abspath_never_reports_frozen_provenance(tmp_path):
    """FROZEN_MARKS trees are unedited by policy: a machine path there is a fact
    about the run that wrote them, not a defect anyone can act on."""
    (tmp_path / "README.md").write_text("**Hub:** root\n")
    for mark in ("archive", "history", "testfixtures"):
        (tmp_path / mark).mkdir()
        (tmp_path / mark / "old.md").write_text("run dir: /home/someone/tmp/run\n")
    findings = doccheck.check(str(tmp_path), max_lines=10_000)
    assert findings["abspath"] == [], findings["abspath"]


def test_abspath_exempts_dated_records_without_freezing_them_for_nav(tmp_path):
    """The abspath exemption is scoped to abspath: a handoff/session/dated readout
    keeps its machine paths, and still owes the nav header every active doc owes."""
    (tmp_path / "README.md").write_text("**Hub:** root\n")
    (tmp_path / "handoff").mkdir()
    for name, body in (("handoff/W_THING.md", "worktree: /home/someone/tmp/wt\n"),
                       ("SESSION_2026-01-02.md", "ran in /home/someone/p1_run\n")):
        (tmp_path / name).write_text(body)
    findings = doccheck.check(str(tmp_path), max_lines=10_000)
    assert findings["abspath"] == [], findings["abspath"]
    assert {x["file"] for x in findings["nav"]} == {"handoff/W_THING.md",
                                                   "SESSION_2026-01-02.md"}


def test_abspath_is_a_selectable_category(tmp_path):
    assert "abspath" in doccheck.CATEGORIES


def test_frozen_marks_are_configurable_and_rederive_the_abspath_exemption():
    """ABSPATH_EXEMPT is derived from three configurable lists, so changing one
    of them must not leave the derived value pointing at the old vocabulary."""
    with doccheck.config_applied({"frozen_marks": ["/frozen/"]}):
        assert doccheck.frozen("frozen/x.md")
        assert not doccheck.frozen("archive/x.md")
        assert "/frozen/" in doccheck.ABSPATH_EXEMPT
    assert "/archive/" in doccheck.ABSPATH_EXEMPT


# --- navigability checks: nav / gloss / hubdist (reported, never gated) ---

def test_nav_fires_only_on_a_doc_missing_the_hub_header(tmp_path):
    """`nav` pins the breadcrumb-header convention, and only its absence."""
    (tmp_path / "README.md").write_text(
        "**Hub:** [home](README.md) - the root hub\n\n[a](a.md)\n")
    (tmp_path / "a.md").write_text("# a\n\nno header here\n")
    findings = doccheck.check(str(tmp_path), max_lines=10_000)
    hit = {x["file"] for x in findings["nav"]}
    assert hit == {"a.md"}, hit


def test_nav_and_hubdist_never_report_frozen_provenance(tmp_path):
    """Frozen trees are unedited by policy, so a navigation finding there is noise."""
    (tmp_path / "README.md").write_text("**Hub:** x\n\nroot\n")
    (tmp_path / "archive").mkdir()
    (tmp_path / "archive" / "old.md").write_text("# old\n")
    (tmp_path / "history").mkdir()
    (tmp_path / "history" / "log.md").write_text("# log\n")
    findings = doccheck.check(str(tmp_path), max_lines=10_000)
    reported = {x["file"] for k in ("nav", "hubdist") for x in findings[k]}
    assert reported == set(), reported


def test_gloss_fires_on_a_bare_index_row_not_a_glossed_one(tmp_path):
    """An index row must say why to spend the hop; a bare filename link does not."""
    (tmp_path / "README.md").write_text(
        "**Hub:** root\n\n"
        "| [bare.md](bare.md) | design |\n"
        "| [good](good.md) | establishes the retry policy and the cases it drops |\n")
    (tmp_path / "bare.md").write_text("**Hub:** x\n")
    (tmp_path / "good.md").write_text("**Hub:** x\n")
    findings = doccheck.check(str(tmp_path), max_lines=10_000)
    lines = {x["line"] for x in findings["gloss"]}
    assert lines == {3}, findings["gloss"]


def test_hubdist_counts_hops_from_the_hub_set(tmp_path):
    """A doc three link-hops from every hub is reported; two hops is not."""
    (tmp_path / "README.md").write_text("**Hub:** root\n\n[one](one.md)\n")
    (tmp_path / "one.md").write_text("**Hub:** x\n\n[two](two.md)\n")
    (tmp_path / "two.md").write_text("**Hub:** x\n\n[three](three.md)\n")
    (tmp_path / "three.md").write_text("**Hub:** x\n")
    findings = doccheck.check(str(tmp_path), max_lines=10_000)
    assert {x["file"] for x in findings["hubdist"]} == {"three.md"}, findings["hubdist"]


def test_navigability_checks_are_selectable_and_ungated_by_default(tmp_path):
    """They are real categories --only accepts, and nothing gates them."""
    for cat in ("nav", "gloss", "hubdist"):
        assert cat in doccheck.CATEGORIES
    assert doccheck.HUBS[0] == "README.md"
    # docgraph must measure the same hub set and the same gloss rule, or the
    # reported check and the before/after instrument can disagree -- including
    # after a config file moves them, which is why it forwards rather than copies.
    import docgraph
    assert docgraph.HUBS is doccheck.HUBS
    assert docgraph.GLOSS_MIN_WORDS == doccheck.GLOSS_MIN_WORDS
    with doccheck.config_applied({"hubs": ["docs/INDEX.md"]}):
        assert docgraph.HUBS == ("docs/INDEX.md",)


# --- latestptr: "newest/latest" links must target a living index ------------

def test_latestptr_fires_on_a_latest_link_to_a_dated_file(tmp_path):
    """A 'newest X' pointer at a dated file rots in every stamped copy the day
    the next dated file lands (93 copies per handoff, measured). The check must
    fire from any doc that is not the target's own directory index."""
    sess = tmp_path / "sessions"
    sess.mkdir()
    (sess / "SESSION_2026-08-20_TOPIC.md").write_text("# s\n")
    (sess / "README.md").write_text(
        "# sessions index\n\n"
        "| [newest session handoff](SESSION_2026-08-20_TOPIC.md) | resume here |\n")
    (tmp_path / "README.md").write_text(
        "# root\n\n"
        "- [sessions index](sessions/README.md)\n"
        "| [newest session handoff](sessions/SESSION_2026-08-20_TOPIC.md) | resume |\n")
    findings = doccheck.check(str(tmp_path), max_lines=10_000)
    files = [x["file"] for x in findings["latestptr"]]
    assert "README.md" in files, findings["latestptr"]
    # The dated file's own directory index is the lawful carrier.
    assert "sessions/README.md" not in files, findings["latestptr"]


def test_latestptr_ignores_undated_targets_and_plain_text(tmp_path):
    (tmp_path / "sessions").mkdir()
    (tmp_path / "sessions" / "README.md").write_text("# idx\n")
    (tmp_path / "README.md").write_text(
        "# root\n\n"
        "| [newest session handoff](sessions/README.md) | undated target: fine |\n"
        "- the latest thinking lives in SESSION_2026-08-20_TOPIC.md (prose, no link)\n"
        "- [an ordinary dated link](sessions/SESSION_2026-08-19_X.md) text has no latest word\n")
    (tmp_path / "sessions" / "SESSION_2026-08-19_X.md").write_text("# s\n")
    findings = doccheck.check(str(tmp_path), max_lines=10_000)
    assert findings["latestptr"] == [], findings["latestptr"]


def test_latestptr_skips_code_fences_and_archives(tmp_path):
    arch = tmp_path / "archive"
    arch.mkdir()
    (arch / "OLD.md").write_text(
        "| [latest roadmap](../ROADMAP_2026-01-01.md) | frozen provenance |\n")
    (tmp_path / "ROADMAP_2026-01-01.md").write_text("# r\n")
    (tmp_path / "README.md").write_text(
        "# root\n\n```\n| [latest roadmap](ROADMAP_2026-01-01.md) | fenced |\n```\n"
        "- [old](archive/OLD.md)\n- [r](ROADMAP_2026-01-01.md)\n")
    findings = doccheck.check(str(tmp_path), max_lines=10_000)
    assert findings["latestptr"] == [], findings["latestptr"]
