#!/usr/bin/env python3
"""Navigability metrics for a docs tree — the before/after instrument.

`doccheck` answers "is this doc broken?". This answers "can a reader who landed
on this doc find their way?", which is the property hub/back-edge work is trying
to move. Reported only; nothing here gates.

Metrics, per run:

  orphans        docs no other doc links to
  sinks          docs with no outbound resolving .md link (the back-edge deficit)
  hubdist        hops from the nearest DOMAIN hub (HUBS), and the >2-hop count
  rootdist       hops from the declared ROOTS doccheck already uses
  scancost       links a reader must scan on the shortest root->doc path
                 (sum of out-degree over the path) — the "O(n)" number
  gloss          index rows whose link carries a >=5-word reason to follow it
  fanout         largest index row counts

Link parsing, file discovery, ROOTS and HUBS are IMPORTED from doccheck so the
two instruments cannot disagree about what a link is — including after a config
file has been applied, which is why they are forwarded at attribute-access time
rather than copied at import.

  python3 docgraph.py --root DOCS_ROOT             # human summary
  python3 docgraph.py --root DOCS_ROOT --json      # machine baseline
  python3 docgraph.py --root DOCS_ROOT --scope docs/guides   # one slice
"""
from __future__ import annotations

import argparse
import json
import os
import posixpath
import statistics
import sys
from collections import Counter, deque

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import doccheck  # noqa: E402

#: Names re-exported from doccheck. Forwarded live (PEP 562) rather than bound
#: at import: a config file can change any of them, and a stale copy here would
#: let this measurement and doccheck's reported `hubdist`/`gloss` checks drift
#: apart silently — the one failure this module is built not to have.
_FORWARDED = ("HUBS", "ROOTS", "FROZEN_MARKS", "GLOSS_MIN_WORDS")


def __getattr__(name):
    if name in _FORWARDED:
        return getattr(doccheck, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def _is_frozen(path: str) -> bool:
    return any(m in "/" + path for m in doccheck.FROZEN_MARKS)


def outlinks(root: str, path: str, known: set) -> tuple:
    """(resolving .md targets, link-bearing lines, lines whose link is glossed).

    A gloss is >=GLOSS_MIN_WORDS words of prose after the link on the same line
    — a table cell, an em-dash clause, a colon. A bare `[foo.md](foo.md)` row
    tells a reader nothing about whether to spend a hop on it.
    """
    targets: set = set()
    linked = glossed = 0
    try:
        with open(os.path.join(root, path), encoding="utf-8", errors="replace") as fh:
            lines = fh.read().splitlines()
    except OSError:
        return targets, 0, 0
    # doccheck's fence skip is nested inside check(); re-derived here (same rule:
    # a closing fence must match the opener's character) rather than imported.
    fence = None
    for n, line in enumerate(lines, 1):
        s = line.lstrip()
        fm = doccheck.FENCE_RE.match(s)
        if fence is None:
            if fm:
                fence = fm.group(1)[0]
                continue
        else:
            if fm and fm.group(1)[0] == fence and not s[fm.end():].strip():
                fence = None
            continue
        stripped = doccheck.CODESPAN_RE.sub("", line)
        found = list(doccheck.LINK_RE.finditer(stripped))
        md = [m for m in found if m.group(1).split("#")[0].endswith(".md")]
        if not md:
            continue
        linked += 1
        # Trailing prose after the LAST link on the line, plus any table cell
        # that follows it — either can carry the reason.
        if doccheck.glossed(stripped[md[-1].end():]):
            glossed += 1
        for m in md:
            tgt = m.group(1).split("#")[0]
            if tgt.startswith(("http://", "https://", "mailto:")):
                continue
            resolved = posixpath.normpath(
                posixpath.join(posixpath.dirname(path), tgt))
            if resolved in known:
                targets.add(resolved)
    return targets, linked, glossed


def bfs(sources, edges, known):
    dist = {s: 0 for s in sources if s in known}
    q = deque(dist)
    while q:
        cur = q.popleft()
        for nxt in edges.get(cur, ()):  # noqa: SIM118
            if nxt not in dist:
                dist[nxt] = dist[cur] + 1
                q.append(nxt)
    return dist


def _scan_costs(sources, edges, outdeg, known):
    """Links scanned on the shortest root->doc path, inclusive of the doc's own.

    BFS by hop count first (a reader follows the shortest chain they can see),
    then the cost of that chain is the fan-out they had to read on the way.
    """
    cost = {s: outdeg.get(s, 0) for s in sources if s in known}
    q = deque(cost)
    seen = set(cost)
    while q:
        cur = q.popleft()
        for nxt in edges.get(cur, ()):  # noqa: SIM118
            if nxt in seen:
                continue
            seen.add(nxt)
            cost[nxt] = cost[cur] + outdeg.get(nxt, 0)
            q.append(nxt)
    return cost


def is_index(path: str, tier2: bool) -> bool:
    """Does this file's rows count toward the index-gloss population?

    `doccheck.INDEX_NAMES` is ("README.md", "INDEX.md"), so a tier-2 layer of
    files named `INDEX_<SUBJECT>.md` beside a directory README is INVISIBLE to
    the gloss metric. Splitting a 143-row README into six subject indexes then
    reads as 143 index rows deleted, not as six new indexes. Off by default so a
    pinned baseline stays comparable while a restructuring is mid-flight; turn
    it on for the re-baseline afterwards.
    """
    base = os.path.basename(path)
    return base in doccheck.INDEX_NAMES or (
        tier2 and base.startswith("INDEX_") and base.endswith(".md"))


def measure(root: str, scope: str = "", tier2: bool = False,
            config: dict = None) -> dict:
    if config:
        with doccheck.config_applied(config):
            return measure(root, scope, tier2)
    files = doccheck.discover(root)
    known = set(files)
    edges: dict = {}
    outdeg: Counter = Counter()
    indeg: Counter = Counter()
    linked_lines = glossed_lines = 0
    per_index: list = []

    for f in files:
        tgts, nlink, nglos = outlinks(root, f, known)
        edges[f] = tgts
        outdeg[f] = len(tgts)
        for t in tgts:
            indeg[t] += 1
        if is_index(f, tier2) and not _is_frozen(f) \
                and (not scope or f.startswith(scope)):
            linked_lines += nlink
            glossed_lines += nglos
            per_index.append((nlink, f))

    active = [f for f in files if not _is_frozen(f)]
    if scope:
        active = [f for f in active if f.startswith(scope)]

    hubd = bfs(doccheck.HUBS, edges, known)
    rootd = bfs(doccheck.ROOTS, edges, known)
    cost = _scan_costs(doccheck.ROOTS, edges, outdeg, known)

    costs = sorted(cost[f] for f in active if f in cost)
    far = [f for f in active if hubd.get(f, 10**6) > doccheck.HUBDIST_MAX]
    sinks = [f for f in active if not edges.get(f)]
    orphans = [f for f in active if indeg[f] == 0 and f not in doccheck.ROOTS]

    def pct(n):
        return round(100.0 * n / len(active), 1) if active else 0.0

    return {
        "docs_total": len(files),
        "docs_active": len(active),
        "scope": scope or "(whole tree)",
        "orphans": len(orphans),
        "orphans_pct": pct(len(orphans)),
        "sinks": len(sinks),
        "sinks_pct": pct(len(sinks)),
        "hub_gt2_or_unreachable": len(far),
        "hub_gt2_pct": pct(len(far)),
        "hub_unreachable": len([f for f in active if f not in hubd]),
        "root_unreachable": len([f for f in active if f not in rootd]),
        "scancost_median": statistics.median(costs) if costs else 0,
        "scancost_p90": costs[int(0.9 * (len(costs) - 1))] if costs else 0,
        "scancost_max": costs[-1] if costs else 0,
        "index_rows_linked": linked_lines,
        "index_rows_glossed": glossed_lines,
        "index_gloss_pct": round(100.0 * glossed_lines / linked_lines, 1) if linked_lines else 0.0,
        "top_fanout": [{"doc": d, "links": n}
                       for n, d in sorted(per_index, reverse=True)[:10]],
        "worst_offenders": sorted(far)[:20],
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    doccheck.add_config_args(ap)
    ap.add_argument("--scope", default="", help="restrict the ACTIVE set to a path prefix")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--tier2", action="store_true",
                    help="also count rows in INDEX_<SUBJECT>.md tier-2 indexes "
                         "toward the gloss metric (off by default: a pinned "
                         "baseline was measured without them)")
    a = ap.parse_args()
    try:
        doccheck.apply_config(doccheck.config_from_args(a))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        sys.stderr.write(f"docgraph: {exc}\n")
        return 2
    m = measure(a.root, a.scope, a.tier2)
    if a.json:
        print(json.dumps(m, indent=2, sort_keys=True))
        return 0
    print(f"docgraph  scope={m['scope']}  active={m['docs_active']} of {m['docs_total']} tracked\n")
    print(f"  sinks (no outbound .md link)     {m['sinks']:5d}  ({m['sinks_pct']}%)")
    print(f"  orphans (no inbound link)        {m['orphans']:5d}  ({m['orphans_pct']}%)")
    print(f"  >2 hops from a hub, or no hub    {m['hub_gt2_or_unreachable']:5d}  ({m['hub_gt2_pct']}%)")
    print(f"    of which unreachable from any  {m['hub_unreachable']:5d}")
    print(f"  scan cost  median {m['scancost_median']}  p90 {m['scancost_p90']}  max {m['scancost_max']}")
    print(f"  index rows glossed               {m['index_gloss_pct']}%"
          f"  ({m['index_rows_glossed']}/{m['index_rows_linked']})")
    print("\n  largest index fan-out:")
    for row in m["top_fanout"][:6]:
        print(f"    {row['links']:4d}  {row['doc']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
