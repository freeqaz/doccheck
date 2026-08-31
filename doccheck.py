#!/usr/bin/env python3
"""doccheck — structural validation for a Markdown documentation tree.

Checks that a human reviewer cannot do at few-thousand-doc scale.

Deterministic (a finding is always a defect) — reasonable to gate in CI:

  links       relative .md links that do not resolve from the linking doc
  rootpath    links written as repo-root paths (resolve from /, not from the doc)
  anchors     #section links whose heading does not exist in the target
  assets      non-.md link targets (tools, configs) that no longer exist
  index       an index row calling a doc CURRENT/LIVE while the doc's own
              header says SUPERSEDED/RETRACTED
  abspath     absolute machine paths (/home/<user>/, /Users/<user>/) in doc
              bodies. Frozen provenance (`abspath_exempt` — dated readouts,
              handoffs, sessions) is exempt: it is not ours to edit, and the
              path a run happened on is a fact about that run.
  latestptr   a link whose text says "newest"/"latest" but whose target is a
              dated file. Such a pointer is stamped into many docs at once and
              rots in every copy the day the next dated file lands (measured:
              93 copies needing a sweep commit per new dated handoff). Point at
              the living index that pins the newest instead. The one lawful
              carrier is the dated file's own directory index (README/INDEX) --
              that IS the living surface the others must point at.
  phrases     wording your project has retired, configured in `phrases`.
              EMPTY by default, so this check is inert until you configure it.

Reported, never gated (a finding may be legitimate mid-workstream):

  unreachable docs reachable from no declared root in `roots`
  orphans     docs nothing links to at all
  coverage    docs their own directory's index does not list
  size        docs over --max-lines; frozen provenance is exempt (`size_exempt`)
  stale       state markers that outlived their ruling, configured in `stale`;
              EMPTY by default. Dated append-only log lines are exempt and must
              never be edited.
  nav         active doc with no `**Hub:**` breadcrumb header
  gloss       index row whose link carries no reason to follow it
  hubdist     active doc more than `hubdist_max` hops from any hub in `hubs`

Everything project-specific -- roots, hubs, frozen/exempt path markers, the
retired-wording and staleness pattern lists -- lives in a JSON config, not in
this file. `.doccheck.json` beside the docs root is picked up automatically;
`--config PATH` names one explicitly and `--no-config` ignores both. Run
`--dump-config` to see the effective settings.

Usage:
  python3 doccheck.py --root DOCS_ROOT              # all checks, human output
  python3 doccheck.py --root DOCS_ROOT --only links,anchors
  python3 doccheck.py --root DOCS_ROOT --json       # machine-readable
  python3 doccheck.py --root DOCS_ROOT --fail-on links,rootpath,anchors,assets

Exit code is non-zero only for categories named in --fail-on, so the
judgement-dependent checks can run without blocking CI.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import subprocess
import sys
from collections import Counter, defaultdict

# Directories that are never ours to lint.
EXCLUDE_DIRS = {
    ".git", "node_modules", "__pycache__", ".pytest_cache", ".ruff_cache",
    "worktrees", "site-packages", ".mypy_cache",
}
EXCLUDE_PATH_PARTS = (
    "/.venv", "/venv/", "/node_modules/", "/site-packages/",
    "/out/", "/tmp/",
    # Generated scratch trees, typically gitignored. They hold machine-written
    # READMEs whose relative links point out of a tree that only ever existed on
    # the box that made them, so they report as broken links that CI -- which
    # sees a clean checkout -- never sees. A finding nobody downstream can
    # reproduce is noise that hides real ones.
    "/runs/", "/work/",
)
# Unedited provenance: link *targets* inside it still count, but we never report
# problems found in it.
ARCHIVE_MARK = "/archive/"

LINK_RE = re.compile(r"\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
# Same shape but keeps the link text — only latestptr needs it.
LINKTEXT_RE = re.compile(r"\[([^\]]*)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
LATEST_TEXT_RE = re.compile(r"\b(newest|latest)\b", re.I)
HEADING_RE = re.compile(r"^#{1,6}\s+(.*?)\s*$", re.M)
FENCE_RE = re.compile(r"(`{3,}|~{3,})")
# An inline code span is quotation, not markup: `[m.config.model_type](model=m)`
# in a table row is a code fragment being cited, and treating it as a link
# reported two phantom targets on a real index. Stripped from link extraction
# only — the phrase/staleness checks keep the full line, same reasoning as the
# fence rule below.
CODESPAN_RE = re.compile(r"`[^`\n]+`")


def out_of_tree(relpath):
    """True when a normalized root-relative path escapes the docs root.

    Links into sibling repos (`../sibling-repo/...`) are a common sanctioned
    convention, and whether the sibling is checked out beside us is a property
    of the machine running the check, not of the documentation. Verifying them
    made the gate green on a dev box and red in CI over the same tree, so they
    are pointers, not checkable links: same rule as gitignored targets.
    """
    return relpath == ".." or relpath.startswith(".." + os.sep)


#: Wording the project has retired, as (pattern, label, requires) triples. The
#: third element is a regex that must ALSO appear in the context window, or
#: None -- it exists because a phrase is usually only wrong in one sense (the
#: same words can be correct domain vocabulary elsewhere), and requiring a
#: disambiguating term nearby is what keeps the rule off the correct sense.
#: Populated from the `phrases` config key; EMPTY here on purpose, since a
#: retired-wording list is by definition specific to one project.
PHRASE_PATTERNS: list = []
#: Prose that legitimately quotes retired phrasing in order to retire it.
#: `phrases_allow` in the config. The default matches nothing.
PHRASE_ALLOW = re.compile(r"(?!x)x")

# Docs are usually hard-wrapped, so the clause that exculpates a line routinely
# lands on the NEXT line -- a sentence reading `It is not "<retired phrase>" --
# that is a` / `doctrinal error.` gets reported for exactly this reason, and the
# only way to satisfy a line-scoped check would be to delete a correct
# repudiation. Allow-lists and context requirements are therefore matched over a
# window, not a line. The cost is real and accepted: a genuine violation within
# ALLOW_RADIUS lines of an unrelated "retired"/"superseded" is now missed. That
# is the better error -- a false positive here pushes an author to mangle
# correct prose, a false negative leaves one stale phrase on a page.
ALLOW_RADIUS = 2


def context_window(lines, idx, radius=None):
    """Lines around the 0-based idx, joined, for allow/require matching."""
    r = ALLOW_RADIUS if radius is None else radius
    return "\n".join(lines[max(0, idx - r):idx + r + 1])


def gitignored(paths, root):
    """Subset of `paths` (root-relative) that .gitignore deliberately excludes.

    A link to an ignored path is not a broken link. It points at a regenerable
    artifact -- a downloaded PDF, a run log dir -- that the docs describe on
    purpose and that git is told not to carry. Whether the file happens to be
    on the machine running the check is not a property of the documentation:
    it is present in a working checkout and absent in CI, so without this the
    same tree is green locally and red in CI, and every clean checkout reports
    findings no author can act on.

    Degrades to "nothing is ignored" if git is unavailable or this is not a
    repo -- the check then behaves as it did before, which is the safe way to
    be wrong.
    """
    if not paths:
        return set()
    # Both spellings of every path. A dir-only rule (`logs/`) does NOT match the
    # bare path when the directory is absent from disk -- and absent is exactly
    # the case that matters, since a dir holding only ignored files never exists
    # in a clean checkout. git resolves the trailing-slash form as a directory
    # without consulting the filesystem, so ask for both and accept either.
    query = sorted({p for p in paths} | {p.rstrip("/") + "/" for p in paths})
    try:
        proc = subprocess.run(
            ["git", "check-ignore", "--stdin", "-z"],
            input="\0".join(query) + "\0",
            capture_output=True, text=True, cwd=root, timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return set()
    # rc 0 = some ignored, 1 = none ignored, 128 = not a repo / git missing.
    if proc.returncode not in (0, 1):
        return set()
    hits = {p.rstrip("/") for p in proc.stdout.split("\0") if p}
    return {p for p in paths if p.rstrip("/") in hits}

# A path that only exists on the box that wrote it. Nothing checked for these
# until ~50 machine-generated index files were minted in one run and a reviewer
# caught the seven that leaked. Scanned over the WHOLE line including code
# fences -- same rule as phrases/stale below, and for a sharper reason: a
# machine path inside a runnable ```bash``` block is the copy-paste nobody else
# can run. Placeholders (`/home/<user>/`, `/Users/$USER/`) do not match, since
# `<` and `$` are outside the username class.
ABSPATH_RE = re.compile(r"/(?:home|Users)/[A-Za-z0-9_-]+/")

#: State markers that outlived the decision behind them, as (pattern, label)
#: pairs. Populated from the `stale` config key; EMPTY here, since which claims
#: have gone stale is a fact about one project's history.
STALE_PATTERNS: list = []
#: Prose that already acknowledges the marker is historical. `stale_allow`.
STALE_ALLOW = re.compile(
    r"superseded|predates|subsequently|scoped to|no longer|was ratified", re.I)
# A dated line in an append-only log is correct provenance, not stale state.
# Editing one would falsify the log, so never report it.
LOGLINE_RE = re.compile(r"^\s*[-*|]?\s*\(?20\d\d-\d\d-\d\d")

#: Roots the tree must be navigable from (`roots`). Reachability is the property
#: indexes are built to provide; raw in-degree is satisfiable by two orphans
#: pointing at each other.
ROOTS = ("README.md", "docs/README.md")

#: Long is correct for these: verbatim transcriptions, generated corpora and
#: dated frozen readouts are provenance, not reference docs anyone navigates.
#: `size_exempt`; empty by default because the shape is project-specific --
#: dated filenames are already exempt below, with no configuration.
SIZE_EXEMPT: tuple = ()
# A date in the filename marks a dated readout: frozen provenance, and splitting
# one churns history for no findability gain.
DATED_NAME_RE = re.compile(r"20\d\d-\d\d-\d\d")

#: Frozen provenance: navigability is not a property we ask of it, and editing
#: it is usually forbidden, so the nav/hubdist/gloss checks must not report on
#: it. Shared with docgraph and navstamp so the three cannot disagree about what
#: is frozen. `frozen_marks`.
FROZEN_MARKS = ("/archive/", "/history/", "/testfixtures/")

#: Dated records: a handoff, a session log, a dated readout. The path they were
#: produced on is a FACT about that run, so abspath must not report them -- but
#: they are still navigated, so this is deliberately NOT folded into
#: FROZEN_MARKS: widening that would also silence nav/gloss/hubdist on every one
#: of them. Scoped to abspath, the collateral is zero. `abspath_exempt`.
ABSPATH_EXEMPT_EXTRA = ("/handoff/", "/handoffs/", "/sessions/", "/readouts/")
#: Derived: FROZEN_MARKS + SIZE_EXEMPT + ABSPATH_EXEMPT_EXTRA. Recomputed
#: whenever config is applied.
ABSPATH_EXEMPT = FROZEN_MARKS + SIZE_EXEMPT + ABSPATH_EXEMPT_EXTRA


def abspath_frozen(path: str) -> bool:
    """True when a machine path in this doc is provenance, not a defect to fix."""
    p = "/" + path
    return (any(m in p for m in ABSPATH_EXEMPT)
            or bool(DATED_NAME_RE.search(os.path.basename(path))))

#: The domain hubs a reader is expected to hold in their head — a small CLOSED
#: set. Distance from these is the "did a cold landing find context" question;
#: distance from ROOTS is the different "is it reachable at all". `hubs`.
HUBS = ("README.md", "docs/README.md")

#: Display names for hubs, used in the breadcrumb navstamp writes. A path is a
#: poor label in a header line the reader is meant to skim. `hub_names`;
#: anything unnamed falls back to its directory name.
HUB_NAMES: dict = {}


def hub_label(hub: str) -> str:
    """Short breadcrumb name for a hub path."""
    if hub in HUB_NAMES:
        return HUB_NAMES[hub]
    return os.path.basename(os.path.dirname(hub)) or "home"

#: The nav breadcrumb token, and how far down we accept it. Written at line 1 by
#: navstamp; the window tolerates a hand-stamped doc that put it just under its
#: H1 rather than reporting a finding nobody would act on.
NAV_TOKEN = "**Hub:**"
NAV_SCAN_LINES = 5
#: A hub is >2 hops away = the reader took three guesses to arrive.
HUBDIST_MAX = 2

#: A link on an index row must carry >=5 words saying why to spend a hop on it.
#: Defined here, used by docgraph, so the reported check and the measurement
#: instrument apply one rule.
GLOSS_MIN_WORDS = 5
GLOSS_STRIP_RE = re.compile(r"[|\-–—:*`\[\]]")


def glossed(tail: str) -> bool:
    """True when the prose trailing an index-row link explains why to follow it."""
    return len(GLOSS_STRIP_RE.sub(" ", tail).split()) >= GLOSS_MIN_WORDS


def frozen(path: str) -> bool:
    return any(m in "/" + path for m in FROZEN_MARKS)


INDEX_NAMES = ("README.md", "INDEX.md")
# | [doc](path.md) | purpose | STATUS |
INDEX_ROW_RE = re.compile(r"^\s*\|\s*\[[^\]]+\]\(([^)#]+\.md)[^)]*\)\s*\|(.*)\|\s*$")
LIVE_LABELS = re.compile(r"\b(CURRENT|LIVE|GOVERNING|PLAN OF RECORD|OF RECORD)\b")
DEAD_LABELS = re.compile(r"\b(SUPERSEDED|RETIRED|ARCHIVED)\b")
# A target doc announcing its own death, in its first lines.
TARGET_DEAD_RE = re.compile(r"\bSUPERSEDED\b|\bSTALE-BANNER\b|\bRETRACTED\b|\bDO NOT CITE\b")


# --- configuration ----------------------------------------------------------
#
# Every project-specific value above is settable from JSON, so the check logic
# can be shared while the vocabulary stays with the project it describes.

DEFAULT_CONFIG_NAME = ".doccheck.json"

#: config key -> module global. Sequence values become tuples (a set for
#: exclude_dirs) so nothing downstream can mutate them by accident.
_SCALAR_KEYS = {
    "nav_token": "NAV_TOKEN",
    "nav_scan_lines": "NAV_SCAN_LINES",
    "hubdist_max": "HUBDIST_MAX",
    "gloss_min_words": "GLOSS_MIN_WORDS",
    "allow_radius": "ALLOW_RADIUS",
    "archive_mark": "ARCHIVE_MARK",
}
_TUPLE_KEYS = {
    "roots": "ROOTS",
    "hubs": "HUBS",
    "index_names": "INDEX_NAMES",
    "frozen_marks": "FROZEN_MARKS",
    "size_exempt": "SIZE_EXEMPT",
    "abspath_exempt": "ABSPATH_EXEMPT_EXTRA",
    "exclude_path_parts": "EXCLUDE_PATH_PARTS",
}
_DICT_KEYS = {"hub_names": "HUB_NAMES"}
CONFIG_KEYS = (sorted(_SCALAR_KEYS) + sorted(_TUPLE_KEYS) + sorted(_DICT_KEYS)
               + ["exclude_dirs", "phrases", "phrases_allow", "stale", "stale_allow"])


def load_config(root: str, path: str = "", use_default: bool = True) -> dict:
    """Config dict from `path`, else `<root>/.doccheck.json`, else {}.

    An explicitly named config that does not exist is an error, not a silent
    fallback to defaults -- a linter that quietly checks nothing is worse than
    one that refuses to start.
    """
    if path:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    if not use_default:
        return {}
    cand = os.path.join(root, DEFAULT_CONFIG_NAME)
    if os.path.exists(cand):
        with open(cand, encoding="utf-8") as fh:
            return json.load(fh)
    return {}


def _rederive() -> None:
    """Recompute the globals that are functions of other globals."""
    global ABSPATH_EXEMPT
    ABSPATH_EXEMPT = tuple(FROZEN_MARKS) + tuple(SIZE_EXEMPT) + tuple(ABSPATH_EXEMPT_EXTRA)


_MANAGED = (list(_SCALAR_KEYS.values()) + list(_TUPLE_KEYS.values())
            + list(_DICT_KEYS.values())
            + ["EXCLUDE_DIRS", "PHRASE_PATTERNS", "PHRASE_ALLOW",
               "STALE_PATTERNS", "STALE_ALLOW", "ABSPATH_EXEMPT"])


def apply_config(cfg: dict) -> dict:
    """Set module globals from `cfg`; returns the previous values, for restore.

    An unknown key is an error: a typo'd key silently disables the rule its
    author meant to write, and that reads as a clean tree.
    """
    unknown = set(cfg) - set(CONFIG_KEYS)
    if unknown:
        raise ValueError(
            f"unknown config key(s): {', '.join(sorted(unknown))}; "
            f"known: {', '.join(CONFIG_KEYS)}")
    g = globals()
    prev = {n: g[n] for n in _MANAGED}

    for key, name in _SCALAR_KEYS.items():
        if key in cfg:
            g[name] = cfg[key]
    for key, name in _TUPLE_KEYS.items():
        if key in cfg:
            g[name] = tuple(cfg[key])
    for key, name in _DICT_KEYS.items():
        if key in cfg:
            g[name] = dict(cfg[key])
    if "exclude_dirs" in cfg:
        g["EXCLUDE_DIRS"] = set(cfg["exclude_dirs"])
    if "phrases" in cfg:
        g["PHRASE_PATTERNS"] = [
            (p["pattern"], p.get("label", p["pattern"]), p.get("requires"))
            for p in cfg["phrases"]]
    if "phrases_allow" in cfg:
        g["PHRASE_ALLOW"] = re.compile(cfg["phrases_allow"], re.I)
    if "stale" in cfg:
        g["STALE_PATTERNS"] = [(p["pattern"], p.get("label", p["pattern"]))
                               for p in cfg["stale"]]
    if "stale_allow" in cfg:
        g["STALE_ALLOW"] = re.compile(cfg["stale_allow"], re.I)
    _rederive()
    return prev


def restore_config(prev: dict) -> None:
    globals().update(prev)


@contextlib.contextmanager
def config_applied(cfg: dict):
    """Apply `cfg` for the duration of the block, then put the old values back."""
    prev = apply_config(cfg or {})
    try:
        yield
    finally:
        restore_config(prev)


def effective_config() -> dict:
    """The settings currently in force, in config-file shape."""
    g = globals()
    out: dict = {}
    for key, name in _SCALAR_KEYS.items():
        out[key] = g[name]
    for key, name in _TUPLE_KEYS.items():
        out[key] = list(g[name])
    for key, name in _DICT_KEYS.items():
        out[key] = dict(g[name])
    out["exclude_dirs"] = sorted(EXCLUDE_DIRS)
    out["phrases"] = [{"pattern": p, "label": lab, "requires": req}
                      for p, lab, req in PHRASE_PATTERNS]
    out["phrases_allow"] = PHRASE_ALLOW.pattern
    out["stale"] = [{"pattern": p, "label": lab} for p, lab in STALE_PATTERNS]
    out["stale_allow"] = STALE_ALLOW.pattern
    return out


def slugs(text: str) -> set:
    """GitHub-style anchor slugs.

    GitHub drops punctuation in place, so "lives — and" leaves a double space
    and yields `lives--and`, but hand-written links often collapse it to
    `lives-and`. Both spellings are accepted rather than reporting a false
    positive on a link that resolves fine in the renderer.
    """
    s = text.strip().lower()
    s = re.sub(r"[`*_\[\]()]", "", s)
    s = re.sub(r"[^\w\s-]", "", s)
    literal = re.sub(r"\s", "-", s).strip("-")
    collapsed = re.sub(r"\s+", "-", s).strip("-")
    return {literal, collapsed, re.sub(r"-+", "-", literal)}


def discover(root: str) -> list:
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS and not d.startswith(".venv")]
        for fn in filenames:
            if not fn.endswith(".md"):
                continue
            p = os.path.relpath(os.path.join(dirpath, fn), root)
            if any(part in "/" + p for part in EXCLUDE_PATH_PARTS):
                continue
            out.append(p)
    return sorted(out)


def check(root: str, max_lines: int, config: dict = None) -> dict:
    if config:
        with config_applied(config):
            return check(root, max_lines)
    files = discover(root)
    known = set(files)
    headings: dict = {}
    findings: dict = defaultdict(list)
    indeg: Counter = Counter()
    edges: dict = defaultdict(set)
    by_dir: dict = defaultdict(list)

    def fenced_lines(lines):
        """1-based line numbers sitting inside a ``` or ~~~ code fence.

        Markdown link syntax inside a fence is code, not a link. A python block
        containing `FN[m.config.model_type](model=m)` parses as a link to
        `model=m`, which was reported as a missing asset -- a finding about the
        checker, not the doc. Closing fences must match the opener's character
        so a ``` inside a ~~~ block does not end it early.
        """
        out, fence = set(), None
        for n, line in enumerate(lines, 1):
            s = line.lstrip()
            m = FENCE_RE.match(s)
            if fence is None:
                if m:
                    fence = m.group(1)[0]
                    out.add(n)
            else:
                out.add(n)
                if m and m.group(1)[0] == fence and not s[m.end():].strip():
                    fence = None
        return out

    texts = {}
    for f in files:
        with open(os.path.join(root, f), encoding="utf-8", errors="replace") as fh:
            texts[f] = fh.read()
        by_dir[os.path.dirname(f)].append(f)
        headings[f] = set()
        for m in HEADING_RE.findall(texts[f]):
            headings[f] |= slugs(m)

    for f in files:
        editable = ARCHIVE_MARK not in "/" + f
        # Frozen provenance is not ours to edit, so a machine path in it is a
        # fact about the run that made it, not a defect we can act on.
        scan_abspath = editable and not abspath_frozen(f)
        text = texts[f]
        lines = text.splitlines()
        fenced = fenced_lines(lines)

        if (editable and len(lines) > max_lines
                and not any(x in "/" + f for x in SIZE_EXEMPT)
                and not DATED_NAME_RE.search(os.path.basename(f))):
            findings["size"].append({"file": f, "line": 1,
                                     "msg": f"{len(lines)} lines > {max_lines}; split into subject docs"})

        for i, line in enumerate(lines, 1):
            ctx = context_window(lines, i - 1)
            if scan_abspath:
                m = ABSPATH_RE.search(line)
                if m:
                    findings["abspath"].append({
                        "file": f, "line": i,
                        "msg": f"absolute machine path: {m.group(0)}... "
                               f"(use ~, $HOME, a flag or a repo-relative path)"})
            if editable and LOGLINE_RE.match(line):
                pass  # dated append-only log entry: provenance, never stale
            elif editable and not PHRASE_ALLOW.search(ctx):
                for pat, label, requires in PHRASE_PATTERNS:
                    if re.search(pat, line, re.I) and (
                            requires is None or re.search(requires, ctx, re.I)):
                        findings["phrases"].append({"file": f, "line": i,
                                                    "msg": f"retired framing: {label}"})
                        break
            if editable and not STALE_ALLOW.search(ctx) and not LOGLINE_RE.match(line):
                for pat, label in STALE_PATTERNS:
                    if re.search(pat, line, re.I):
                        findings["stale"].append({"file": f, "line": i, "msg": label})
                        break

            # Everything remaining in this loop body is link extraction, and
            # none of it applies inside a code fence. Deliberately NOT applied
            # to the phrase and staleness checks above: a retired framing
            # quoted in a block is still on the page.
            if i in fenced:
                continue

            if editable:
                for m in LINKTEXT_RE.finditer(CODESPAN_RE.sub("", line)):
                    ltext, target = m.group(1), m.group(2).partition("#")[0].strip()
                    if not target.endswith(".md") or not LATEST_TEXT_RE.search(ltext):
                        continue
                    if not DATED_NAME_RE.search(os.path.basename(target)):
                        continue
                    tgt = os.path.normpath(os.path.join(os.path.dirname(f), target))
                    # The dated file's own directory index is the living surface
                    # everyone else must point at -- it alone may name the newest.
                    if (os.path.basename(f) in INDEX_NAMES
                            and os.path.dirname(tgt) == os.path.dirname(f)):
                        continue
                    findings["latestptr"].append({
                        "file": f, "line": i,
                        "msg": f'"{ltext.strip()}" -> dated file {os.path.basename(tgt)}; '
                               "point at the living index that pins the newest"})

            for m in LINK_RE.finditer(CODESPAN_RE.sub("", line)):
                raw = m.group(1)
                if raw.startswith(("http://", "https://", "mailto:", "#")):
                    continue
                path, _, frag = raw.partition("#")
                path = path.strip()
                if not path:
                    continue
                # A directory link is a link to that directory's index. Without
                # this, `[sessions/](./sessions/)` leaves the whole cluster
                # reported unreachable even though a reader gets there fine.
                if path.endswith("/"):
                    d = os.path.normpath(os.path.join(os.path.dirname(f), path))
                    if out_of_tree(d):
                        continue
                    for name in INDEX_NAMES:
                        cand = os.path.join(d, name)
                        if cand in known:
                            indeg[cand] += 1
                            edges[f].add(cand)
                            break
                    else:
                        if editable and not os.path.isdir(os.path.join(root, d)):
                            findings["links"].append({
                                "file": f, "line": i, "target": d,
                                "msg": f"broken directory link: {path}"})
                    continue
                if not path.endswith(".md"):
                    if editable and not path.startswith("#") and "*" not in path:
                        cand = os.path.normpath(os.path.join(os.path.dirname(f), path))
                        if out_of_tree(cand):
                            continue
                        if not os.path.exists(os.path.join(root, cand)) and \
                                not os.path.exists(os.path.join(root, os.path.normpath(path))):
                            findings["assets"].append({"file": f, "line": i, "target": cand,
                                "msg": f"link target does not exist: {path}"})
                    continue
                tgt = os.path.normpath(os.path.join(os.path.dirname(f), path))
                if out_of_tree(tgt):
                    continue
                if tgt in known:
                    indeg[tgt] += 1
                    edges[f].add(tgt)
                elif os.path.normpath(path) in known:
                    indeg[os.path.normpath(path)] += 1
                    edges[f].add(os.path.normpath(path))
                    if editable:
                        findings["rootpath"].append({
                            "file": f, "line": i,
                            "msg": f"repo-root path used as relative link: {path}"})
                    tgt = os.path.normpath(path)
                elif os.path.exists(os.path.join(root, tgt)):
                    continue
                else:
                    if editable:
                        findings["links"].append({"file": f, "line": i, "target": tgt,
                                                  "msg": f"broken link: {path}"})
                    continue
                if frag and tgt in headings and not (slugs(frag) & headings[tgt]) and editable:
                    findings["anchors"].append({
                        "file": f, "line": i,
                        "msg": f"anchor #{frag} not found in {os.path.basename(tgt)}"})

    # --- reachability: can a reader GET here from a declared entry point? ---
    seen, queue = set(), [r for r in ROOTS if r in known]
    seen.update(queue)
    while queue:
        cur = queue.pop()
        for nxt in edges.get(cur, ()):
            if nxt in known and nxt not in seen:
                seen.add(nxt)
                queue.append(nxt)
    for f in files:
        if ARCHIVE_MARK in "/" + f or f in seen:
            continue
        if indeg[f]:
            findings["unreachable"].append({
                "file": f, "line": 1,
                "msg": f"linked ({indeg[f]}x) but not reachable from any root"})
        else:
            findings["orphans"].append({"file": f, "line": 1, "msg": "no incoming links"})

    # --- index consistency: a status column that contradicts its target ---
    for f in files:
        if os.path.basename(f) not in INDEX_NAMES or ARCHIVE_MARK in "/" + f:
            continue
        for i, line in enumerate(texts[f].splitlines(), 1):
            m = INDEX_ROW_RE.match(line)
            if not m:
                continue
            tgt = os.path.normpath(os.path.join(os.path.dirname(f), m.group(1)))
            if tgt not in texts:
                continue
            label = m.group(2).upper()
            head = "\n".join(texts[tgt].splitlines()[:30])
            if LIVE_LABELS.search(label) and not DEAD_LABELS.search(label) \
                    and TARGET_DEAD_RE.search(head):
                findings["index"].append({
                    "file": f, "line": i,
                    "msg": f"index calls {os.path.basename(tgt)} live, "
                           "but its own header says superseded/retracted"})

    # --- coverage: a doc its own directory's index does not list ---
    for d, members in by_dir.items():
        idx = [os.path.join(d, n) for n in INDEX_NAMES if os.path.join(d, n) in known]
        if not idx:
            continue
        listed = set()
        for ix in idx:
            # Same fence rule as above, for the same reason in reverse: a link
            # shape inside a code block in an index is not the index listing
            # that doc, and counting it would silently pass the coverage check.
            ixl = texts[ix].splitlines()
            ixf = fenced_lines(ixl)
            body = "\n".join(l for n, l in enumerate(ixl, 1) if n not in ixf)
            for mm in LINK_RE.finditer(body):
                p = mm.group(1).split("#")[0].strip()
                if p.endswith(".md"):
                    listed.add(os.path.normpath(os.path.join(d, p)))
        for mfile in members:
            if mfile in idx or mfile in listed or ARCHIVE_MARK in "/" + mfile:
                continue
            findings["coverage"].append({
                "file": mfile, "line": 1,
                "msg": f"not listed in its directory index ({os.path.basename(idx[0])})"})

    # --- navigability: nav / gloss / hubdist (REPORTED, never gated) ---
    # These three are judgement calls by construction -- a doc mid-workstream can
    # legitimately be unstamped, unglossed and far from a hub -- so they are
    # reported and CI never fails on them. They exist to make the navigation
    # layer measurable per scope while it is being built out.
    active = [f for f in files if ARCHIVE_MARK not in "/" + f and not frozen(f)
              and not any(x in "/" + f for x in SIZE_EXEMPT)]

    for f in active:
        if f in HUBS:
            continue  # a hub is the crumb's destination; a self-crumb is a wasted slot
        head = texts[f].splitlines()[:NAV_SCAN_LINES]
        if not any(NAV_TOKEN in l for l in head):
            findings["nav"].append({
                "file": f, "line": 1,
                "msg": f"no `{NAV_TOKEN}` header in the first {NAV_SCAN_LINES} lines"})

    for f in active:
        if os.path.basename(f) not in INDEX_NAMES:
            continue
        lines = texts[f].splitlines()
        gfenced = fenced_lines(lines)
        for i, line in enumerate(lines, 1):
            if i in gfenced:
                continue
            stripped = CODESPAN_RE.sub("", line)
            md = [m for m in LINK_RE.finditer(stripped)
                  if m.group(1).split("#")[0].endswith(".md")]
            if md and not glossed(stripped[md[-1].end():]):
                findings["gloss"].append({
                    "file": f, "line": i,
                    "msg": f"index row link with <{GLOSS_MIN_WORDS} words of reason to follow it"})

    hubseen, hubq = {}, [h for h in HUBS if h in known]
    for h in hubq:
        hubseen[h] = 0
    while hubq:
        cur = hubq.pop(0)
        for nxt in edges.get(cur, ()):
            if nxt in known and nxt not in hubseen:
                hubseen[nxt] = hubseen[cur] + 1
                hubq.append(nxt)
    for f in active:
        d = hubseen.get(f)
        if d is None:
            findings["hubdist"].append({
                "file": f, "line": 1, "msg": "reachable from no hub in HUBS"})
        elif d > HUBDIST_MAX:
            findings["hubdist"].append({
                "file": f, "line": 1, "msg": f"{d} hops from the nearest hub (>{HUBDIST_MAX})"})

    # A link whose target git is told to ignore points at a regenerable
    # artifact, not at a hole in the docs. Resolved in one batch call rather
    # than per finding.
    targets = {x["target"] for k in ("links", "assets") for x in findings[k] if x.get("target")}
    ignored = gitignored(targets, root)
    if ignored:
        for k in ("links", "assets"):
            findings[k] = [x for x in findings[k] if x.get("target") not in ignored]

    return findings


#: Every check category, in display order. --only validates against this list.
CATEGORIES = ["links", "rootpath", "anchors", "assets", "index", "phrases",
              "abspath", "latestptr",
              "stale", "unreachable", "coverage", "size", "orphans",
              "nav", "gloss", "hubdist"]


def parse_categories(value: str, flag: str, hint: str) -> set:
    """Parse a comma-separated category list, rejecting unknown names.

    Both --only and --fail-on name CHECK CATEGORIES, not paths, and both fail
    SILENTLY on a name that matches none: --only selects zero checks and prints
    "total findings: 0" -- a clean report that verified nothing -- while
    --fail-on gates zero categories and exits 0 forever, in the flag whose whole
    job is to make CI red. --only was hit for real: a path was passed instead of
    a category as the verification step for a whole doc lane, vacuously green
    every time. An empty selection must be an ERROR, not a success, so one
    helper serves both flags and they cannot drift apart.

    NOTE: validate against CATEGORIES, not findings.keys() -- findings is a
    defaultdict holding only non-empty categories, so a clean tree would
    spuriously reject valid names.

    Raises ValueError carrying the ready-to-print message.
    """
    want = {c.strip() for c in value.split(",") if c.strip()}
    unknown = want - set(CATEGORIES)
    if unknown:
        raise ValueError(
            f"{flag} names no known check category: {', '.join(sorted(unknown))}\n"
            f"  known: {', '.join(CATEGORIES)}\n"
            f"  ({hint})")
    return want


def add_config_args(ap: argparse.ArgumentParser) -> None:
    """The --root/--config/--no-config trio, shared by all three tools."""
    ap.add_argument("--root", default=os.getcwd(),
                    help="docs tree to read (default: the current directory)")
    ap.add_argument("--config", default="",
                    help=f"JSON settings file (default: <root>/{DEFAULT_CONFIG_NAME} if present)")
    ap.add_argument("--no-config", action="store_true",
                    help=f"ignore <root>/{DEFAULT_CONFIG_NAME}")


def config_from_args(a) -> dict:
    return load_config(a.root, a.config, use_default=not a.no_config)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    add_config_args(ap)
    ap.add_argument("--only", default="", help="comma-separated subset of checks")
    ap.add_argument("--fail-on", default="",
                    help="categories that set a non-zero exit code "
                         "(validated; empty means never gate)")
    ap.add_argument("--max-lines", type=int, default=600)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--dump-config", action="store_true",
                    help="print the effective settings as JSON and exit")
    ap.add_argument("--limit", type=int, default=25, help="findings shown per category")
    a = ap.parse_args()

    try:
        apply_config(config_from_args(a))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        sys.stderr.write(f"doccheck: {exc}\n")
        return 2

    if a.dump_config:
        print(json.dumps(effective_config(), indent=2, sort_keys=True))
        return 0

    # Both selections are parsed before check() runs, so a typo'd CI config
    # fails in milliseconds instead of after a full tree walk.
    try:
        want = parse_categories(
            a.only, "--only",
            "--only filters categories, e.g. 'links,anchors' -- it does not take paths")
        fail = parse_categories(
            a.fail_on, "--fail-on",
            "--fail-on gates the exit code on categories, e.g. 'links,anchors'")
    except ValueError as exc:
        sys.stderr.write(f"doccheck: {exc}\n")
        return 2

    findings = check(a.root, a.max_lines)
    if want:
        findings = {k: v for k, v in findings.items() if k in want}

    if a.json:
        print(json.dumps(findings, indent=1, sort_keys=True))
    else:
        for cat in CATEGORIES:
            rows = findings.get(cat) or []
            if not rows:
                continue
            print(f"\n=== {cat}: {len(rows)} ===")
            for r in rows[: a.limit]:
                print(f"  {r['file']}:{r['line']}  {r['msg']}")
            if len(rows) > a.limit:
                print(f"  ... {len(rows) - a.limit} more")
        total = sum(len(v) for v in findings.values())
        print(f"\ntotal findings: {total}")

    return 1 if any(findings.get(c) for c in fail) else 0


if __name__ == "__main__":
    sys.exit(main())
