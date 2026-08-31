#!/usr/bin/env python3
"""navstamp — insert the one-line `**Hub:**` navigation header, in scope.

The header shape is the one doccheck's `nav` and `hubdist` checks measure:

    **Hub:** [<hub>](<rel>) › [<tier-2 index>](<rel>) — <5-12 words>

`**Hub:**` is only the default: the token and the window scanned for it follow
doccheck's `nav_token` / `nav_scan_lines` config keys, so the stamper and the
`nav` check can never disagree about what a stamped file looks like.

`--scope` is MANDATORY and there is no way to ask for the whole tree. Several
doc streams typically share one working tree at once, so an unscoped pass would
rewrite every file every other stream is mid-edit on, and the recovery is a
manual diff review of thousands of files. Each stream stamps its own prefix and
nothing else.

Two more properties this tool must have, and does:

  idempotent   any file already containing `**Hub:**` is skipped untouched. The
               token is on 0 docs at introduction, so presence == stamped.
  additive     it inserts its own line plus one blank separator at line 1 and
               rewrites no other byte. Frozen provenance (doccheck's
               `frozen_marks` and `size_exempt`) is skipped entirely -- it is
               not ours to edit, and the ONE header line is not worth the
               exception.

Dry-run is the default; `--apply` is required to write. Review the diff on one
directory before running it over a whole prefix.

  python3 navstamp.py --root DOCS_ROOT --scope docs/guides
  python3 navstamp.py --root DOCS_ROOT --scope docs/guides --apply
  python3 navstamp.py --root DOCS_ROOT --scope docs/guides/one.md --gloss "..." --apply
"""
from __future__ import annotations

import json
import argparse
import os
import posixpath
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import doccheck  # noqa: E402
import docgraph  # noqa: E402

#: Re-exported from doccheck and forwarded live (PEP 562) rather than bound at
#: import: `nav_token` is configurable, and a stale copy here would stamp one
#: token while doccheck's `nav` check reported another. Internal uses must spell
#: `doccheck.NAV_TOKEN` — a bare name would miss this hook and raise NameError.
_FORWARDED = {"STAMP_TOKEN": "NAV_TOKEN"}


def __getattr__(name):
    if name in _FORWARDED:
        return getattr(doccheck, _FORWARDED[name])
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


GLOSS_MAX_WORDS = 12
_H1_RE = re.compile(r"^#\s+(.*?)\s*$")
_BANNER_RE = re.compile(r"^\s*[>*_#]|^\s*\*\*")


def skippable(path: str) -> str:
    """Reason this path is not ours to stamp, or "" if it is."""
    marked = "/" + path
    if any(m in marked for m in doccheck.FROZEN_MARKS):
        return "frozen provenance"
    if any(x in marked for x in doccheck.SIZE_EXEMPT):
        return "size-exempt provenance"
    return ""


def _first_prose(lines: list) -> str:
    """First real sentence of the body — the fallback gloss source.

    Skips the H1, banners, blockquotes and table rows: those either restate the
    title or are status furniture, and neither says what the doc establishes.
    """
    for line in lines[:40]:
        s = line.strip()
        if not s or s.startswith(("#", ">", "|", "-", "*", "!", "```")):
            continue
        if _BANNER_RE.match(line):
            continue
        return s
    return ""


def _clip(text: str, limit: int = GLOSS_MAX_WORDS) -> str:
    """Trim to a gloss-length phrase, preferring a natural break over a hard cut.

    A hard word-count cut leaves fragments like "... so the engine", which reads
    worse than no gloss at all. Cut at the first clause boundary that still
    leaves a usable phrase; only fall back to truncation when there isn't one.
    """
    text = re.sub(r"[`*\[\]|]", "", text).strip()
    text = re.sub(r"\s+", " ", text)
    for sep in (". ", " — ", " – ", "; ", ": ", ", "):
        head = text.split(sep)[0]
        n = len(head.split())
        if doccheck.GLOSS_MIN_WORDS <= n <= limit:
            return head.rstrip(".;:, ")
    return " ".join(text.split()[:limit]).rstrip(".;:, ")


def _index_gloss(root: str, target: str, indexes: list) -> str:
    """Prose an existing index already wrote next to its link to `target`.

    The best available gloss is usually one somebody already hand-wrote: the
    index row that routes to this doc had to say why to follow it. Reusing it
    keeps the header and the index consistent instead of inventing a second,
    divergent one-liner.
    """
    for idx in indexes:
        cands: list = []
        try:
            with open(os.path.join(root, idx), encoding="utf-8", errors="replace") as fh:
                lines = fh.read().splitlines()
        except OSError:
            continue
        for n, line in enumerate(lines):
            stripped = doccheck.CODESPAN_RE.sub("", line)
            for m in doccheck.LINK_RE.finditer(stripped):
                tgt = m.group(1).split("#")[0]
                if not tgt.endswith(".md"):
                    continue
                resolved = posixpath.normpath(
                    posixpath.join(posixpath.dirname(idx), tgt))
                if resolved != target:
                    continue
                tail = re.sub(r"^[\s|\-–—:*]+", "", stripped[m.end():]).split("|")[0]
                # Hard-wrapped bullet indexes put the prose on the CONTINUATION
                # line, where the same-line gloss rule cannot see it. The tool
                # reads it anyway -- the reason a human wrote is the best gloss
                # available, and how it was wrapped is not the author's point.
                k = n + 1
                while len(tail.split()) < doccheck.GLOSS_MIN_WORDS and k < len(lines):
                    nxt = lines[k]
                    if not nxt.strip() or not nxt.startswith((" ", "\t")):
                        break
                    tail = (tail + " " + re.sub(r"^[\s|\-–—:*]+", "",
                                                doccheck.CODESPAN_RE.sub("", nxt))).strip()
                    k += 1
                if len(tail.split()) >= doccheck.GLOSS_MIN_WORDS:
                    cands.append(tail)
        # An index usually links a doc several times: passing cross-references in
        # prose, and once from the row that exists to describe it. Take the
        # richest tail rather than the first -- the first is typically a
        # cross-reference, whose trailing words are the sentence it sat in.
        # Resolved per index, nearest first, so the local index always wins.
        if cands:
            return _clip(max(cands, key=lambda t: len(t.split())))
    return ""


def _ancestor_indexes(path: str, known: set) -> list:
    """Directory indexes above `path`, nearest first."""
    out = []
    d = posixpath.dirname(path)
    while True:
        for name in doccheck.INDEX_NAMES:
            cand = posixpath.join(d, name) if d else name
            if cand in known and cand != path:
                out.append(cand)
        if not d:
            break
        d = posixpath.dirname(d)
    return out


def _index_label(root: str, index: str) -> str:
    """Short breadcrumb name for a tier-2 index.

    Its own H1 beats its directory name: a nested `foo/md/README.md` titles
    itself `foo/md`, and a breadcrumb reading `md` tells a reader nothing.
    """
    try:
        with open(os.path.join(root, index), encoding="utf-8", errors="replace") as fh:
            for line in (fh.read().splitlines()[:10]):
                m = _H1_RE.match(line)
                if m:
                    head = re.split(r"\s+[—–-]\s+", m.group(1).strip())[0]
                    head = re.sub(r"[`*\[\]|]", "", head).strip()
                    if 0 < len(head) <= 40:
                        return head
    except OSError:
        pass
    return posixpath.basename(posixpath.dirname(index)) or "index"


def _hub_for(path: str, hubdists: dict) -> str:
    """Deepest hub whose directory CONTAINS the doc; nearest by link distance if none.

    Containment beats link distance because the breadcrumb answers "where does
    this belong", not "what is the shortest path anyone ever wrote". Distance
    alone files a doc under whichever hub happens to link closest to it, which
    is true as a hop count and wrong as a heading.
    """
    best, best_pfx, best_d = "", -1, 10**6
    for hub, dist in hubdists.items():
        if hub == path:
            continue  # a hub's breadcrumb points UP, never at itself
        hdir = posixpath.dirname(hub)
        contains = (not hdir) or path.startswith(hdir + "/")
        pfx = len(hdir) if contains else -1
        d = dist.get(path, 10**6)
        if (-pfx, d) < (-best_pfx, best_d):
            best, best_pfx, best_d = hub, pfx, d
    return best or (doccheck.HUBS[0] if doccheck.HUBS else "README.md")


def _rel(frm: str, to: str) -> str:
    r = posixpath.relpath(to, posixpath.dirname(frm) or ".")
    return r


def plan(root: str, scope: str, gloss_override: str = "",
         gloss_source: str = "auto", config: dict = None) -> list:
    """One row per in-scope doc: the header line it would get, or why not."""
    if config:
        with doccheck.config_applied(config):
            return plan(root, scope, gloss_override, gloss_source)
    files = doccheck.discover(root)
    known = set(files)
    edges: dict = {}
    for f in files:
        edges[f], _, _ = docgraph._outlinks(root, f, known)
    hubdists = {h: docgraph._bfs([h], edges, known)
                for h in doccheck.HUBS if h in known}

    # Scope is a path prefix, not a string prefix: scope "sub" must not reach
    # sibling "subway.md". A bare startswith would, and the whole safety story
    # is that one stream never writes into another stream's files.
    base = scope.rstrip("/")

    rows = []
    for f in files:
        if not (f == base or f.startswith(base + "/")):
            continue
        reason = skippable(f)
        if reason:
            rows.append({"file": f, "action": "skip", "why": reason})
            continue
        try:
            with open(os.path.join(root, f), encoding="utf-8", errors="replace") as fh:
                lines = fh.read().splitlines()
        except OSError as exc:
            rows.append({"file": f, "action": "skip", "why": f"unreadable: {exc}"})
            continue
        if any(doccheck.NAV_TOKEN in l for l in lines[:doccheck.NAV_SCAN_LINES]):
            rows.append({"file": f, "action": "skip", "why": "already stamped"})
            continue

        hub = _hub_for(f, hubdists)
        if hub == f or f in doccheck.HUBS:
            rows.append({"file": f, "action": "skip",
                         "why": "top of the hub tree — write its header by hand"})
            continue
        indexes = _ancestor_indexes(f, known)
        # Tier-2 sits BELOW the hub, never above it: an index that is itself a
        # hub is not a second breadcrumb, it is the same one. Omitted when the
        # doc's own directory index is the hub.
        tier2 = next((i for i in indexes if i != hub and i not in doccheck.HUBS), "")
        h1 = _clip(next((m.group(1) for m in
                         (_H1_RE.match(l) for l in lines[:10]) if m), ""))
        if gloss_source == "h1":
            gloss = gloss_override or h1
        elif gloss_source == "prose":
            gloss = gloss_override or _clip(_first_prose(lines)) or h1
        elif gloss_source == "index":
            gloss = gloss_override or _index_gloss(root, f, indexes)
        else:
            gloss = (gloss_override or _index_gloss(root, f, indexes)
                     or _clip(_first_prose(lines)) or h1)
        if not gloss:
            rows.append({"file": f, "action": "skip", "why": "no gloss derivable — write one by hand"})
            continue

        parts = [f"[{doccheck.hub_label(hub)}]({_rel(f, hub)})"]
        if tier2 and tier2 != hub:
            parts.append(f"[{_index_label(root, tier2)}]({_rel(f, tier2)})")
        header = f"{doccheck.NAV_TOKEN} " + " › ".join(parts) + f" — {gloss}"
        rows.append({"file": f, "action": "stamp", "header": header})
    return rows


def apply_rows(root: str, rows: list) -> int:
    n = 0
    for r in rows:
        if r["action"] != "stamp":
            continue
        p = os.path.join(root, r["file"])
        with open(p, encoding="utf-8") as fh:
            body = fh.read()
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(r["header"] + "\n\n" + body)
        n += 1
    return n


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    doccheck.add_config_args(ap)
    ap.add_argument("--scope", required=True,
                    help="REQUIRED path prefix. There is no whole-tree mode: the tree "
                         "is usually shared by several concurrent doc streams.")
    ap.add_argument("--gloss", default="",
                    help="use this gloss for every stamped file (single-file passes)")
    ap.add_argument("--gloss-source", choices=("auto", "index", "prose", "h1"),
                    default="auto",
                    help="auto: reuse the index row that routes here, else first prose, "
                         "else the H1. Use h1 for transcriptions and other docs whose "
                         "opening lines are front matter rather than a thesis.")
    ap.add_argument("--apply", action="store_true",
                    help="write the files; without it this is a dry run")
    ap.add_argument("--show-skips", action="store_true")
    a = ap.parse_args()

    try:
        doccheck.apply_config(doccheck.config_from_args(a))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        sys.stderr.write(f"navstamp: {exc}\n")
        return 2

    scope = a.scope.strip().lstrip("./")
    if not scope:
        sys.stderr.write("navstamp: --scope must name a path prefix, not the whole tree\n")
        return 2

    rows = plan(a.root, scope, a.gloss, a.gloss_source)
    stamp = [r for r in rows if r["action"] == "stamp"]
    skip = [r for r in rows if r["action"] == "skip"]
    if not rows:
        sys.stderr.write(f"navstamp: --scope {scope!r} matched no docs\n")
        return 2

    for r in stamp:
        print(f"  {r['file']}\n      {r['header']}")
    if a.show_skips:
        for r in skip:
            print(f"  SKIP {r['file']}  ({r['why']})")
    print(f"\nscope={scope}  would stamp {len(stamp)}  skip {len(skip)}"
          f"  ({'APPLIED' if a.apply else 'dry run — pass --apply to write'})")
    if a.apply:
        print(f"stamped {apply_rows(a.root, stamp)} files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
