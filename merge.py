#!/usr/bin/env python3
"""Merge shard JSONL files (from both machines) into one deduped products.jsonl.

  python merge.py data/products.shard0.jsonl pc2/products.shard1.jsonl -o data/products.jsonl

Dedupes by product_id, preferring valid (ok=true) records and the newest scrape.
Tombstones (ok=false) are dropped from the final file but counted.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser(description="Merge + dedupe iHerb shard JSONL files")
    ap.add_argument("inputs", nargs="+", help="shard JSONL files")
    ap.add_argument("-o", "--out", default="data/products.jsonl")
    args = ap.parse_args()

    best: dict[str, dict] = {}
    seen = tombs = 0
    for path in args.inputs:
        p = Path(path)
        if not p.exists():
            print(f"WARN: {p} not found, skipping", file=sys.stderr)
            continue
        with p.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                pid = str(rec.get("product_id") or "")
                if not pid:
                    continue
                seen += 1
                if not rec.get("ok", True):
                    tombs += 1
                prev = best.get(pid)
                if prev is None:
                    best[pid] = rec
                    continue
                # prefer ok=true, then newest scrape
                better = (rec.get("ok", True), rec.get("scraped_at", 0))
                prevk = (prev.get("ok", True), prev.get("scraped_at", 0))
                if better > prevk:
                    best[pid] = rec

    final = [r for r in best.values() if r.get("ok", True)]
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as f:
        for r in final:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    print(f"Read {seen} lines across {len(args.inputs)} file(s); "
          f"{len(best)} unique products ({tombs} tombstones).")
    print(f"Wrote {len(final)} valid products -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
