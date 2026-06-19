#!/usr/bin/env python3
"""iHerb catalog scraper -> JSONL (one shard per machine).

  python scrape.py --shard 0 --shards 2        # machine #1
  python scrape.py --shard 1 --shards 2        # machine #2

Flow: warm up (Cloudflare clearance) -> discover product URLs from the sitemaps
(cached) -> keep only this shard (product_id %% shards == shard) -> scrape each
product's JSON-LD + specs, streaming to data/products.shard<N>.jsonl with resume.

Resumable & self-healing: writes each product immediately and skips ones already
in the output; recycles the browser every --recycle products; retries Cloudflare
blocks up to --max-attempts passes; exits 0 only when the shard is fully captured.
Exit codes: 0 = shard complete, 2 = browser could not launch, 1 = other fatal.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import sys
import time
from pathlib import Path

# UTF-8 console + JSONL on Windows regardless of code page.
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from iherb_scraper.browser import browser_session, fetch_product, warm_up  # noqa: E402
from iherb_scraper.discover import discover_product_urls  # noqa: E402
from iherb_scraper.parser import is_valid, normalize  # noqa: E402

EXIT_OK = 0
EXIT_FATAL = 1
EXIT_NO_BROWSER = 2

RECYCLE_SIGNS = ("closed", "crash", "target page", "browser has been", "connection")


def log(*a):
    # stdout (not stderr) so PowerShell doesn't render progress lines as red errors.
    print(*a, flush=True)


def acquire_single_instance_lock(out_dir: Path, shard: int):
    """Stop two scrapers from running the SAME shard on one machine (which would
    fight over the JSONL/log and waste work). Returns the open lock-file handle —
    keep a reference alive for the whole process. Exits cleanly (code 0, so the
    watchdog stops) if another instance already holds the lock. The OS releases the
    lock automatically when the holder dies, so a crashed run never leaves it stuck."""
    lock_path = out_dir / f"shard{shard}.lock"
    lock_file = open(lock_path, "w")
    try:
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        log(f"Another scraper is already running for shard {shard} on this machine — "
            f"exiting to avoid a duplicate.")
        lock_file.close()
        sys.exit(0)
    return lock_file


def load_done(path: Path) -> set[str]:
    done: set[str] = set()
    if path.exists():
        with path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    pid = json.loads(line).get("product_id")
                    if pid:
                        done.add(str(pid))
                except json.JSONDecodeError:
                    continue
    return done


async def discover(args, jsonl_dir: Path) -> dict[str, str]:
    cache = jsonl_dir / "urls.json"
    if cache.exists() and not args.rediscover:
        urls = json.loads(cache.read_text(encoding="utf-8"))
        log(f"Loaded {len(urls)} product URLs from cache ({cache.name})")
        return urls
    log("Discovering product URLs from sitemaps...")
    async with browser_session(headless=not args.headed, channel=args.channel) as (_, ctx):
        page = await ctx.new_page()
        try:
            # discover_product_urls navigates to the homepage itself (clears CF +
            # makes the sitemap fetch same-origin).
            urls = await discover_product_urls(page, log=log)
        finally:
            await page.close()
    if urls:
        cache.write_text(json.dumps(urls, ensure_ascii=False), encoding="utf-8")
        log(f"Discovered {len(urls)} product URLs -> {cache.name}")
    return urls


async def run(args) -> int:
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    jsonl = out_dir / f"products.shard{args.shard}.jsonl"

    urls = await discover(args, out_dir)
    if not urls:
        log("FATAL: no product URLs discovered (Cloudflare block?). Will retry.")
        return EXIT_FATAL

    shard_items = [(pid, u) for pid, u in urls.items() if int(pid) % args.shards == args.shard]
    shard_items.sort(key=lambda x: int(x[0]))
    if args.limit:
        shard_items = shard_items[: args.limit]
    log(f"Shard {args.shard}/{args.shards}: {len(shard_items)} of {len(urls)} products")

    done = load_done(jsonl)
    attempts: dict[str, int] = {}
    log(f"Already in {jsonl.name}: {len(done)} | remaining: "
        f"{sum(1 for pid, _ in shard_items if pid not in done)}")

    fout = jsonl.open("a", encoding="utf-8")
    counters = {"ok": 0, "tomb": 0, "blocked": 0}

    def write(rec: dict):
        fout.write(json.dumps(rec, ensure_ascii=False) + "\n")
        fout.flush()

    def remaining() -> list[tuple[str, str]]:
        return [(pid, u) for pid, u in shard_items if pid not in done]

    try:
        while True:
            todo = remaining()
            if not todo:
                break
            chunk = todo[: args.recycle]
            try:
                async with browser_session(headless=not args.headed, channel=args.channel) as (_, ctx):
                    if not await warm_up(ctx):
                        log("WARN: warm-up did not clear Cloudflare this cycle")
                    sem = asyncio.Semaphore(args.concurrency)

                    async def work(pid: str, url: str):
                        async with sem:
                            page = await ctx.new_page()
                            try:
                                raw = await fetch_product(page, url, timeout_ms=args.timeout * 1000)
                            finally:
                                await page.close()
                            await asyncio.sleep(args.delay + random.uniform(0, args.delay * 0.5))
                            return pid, url, raw

                    tasks = [asyncio.create_task(work(pid, u)) for pid, u in chunk]
                    try:
                        for fut in asyncio.as_completed(tasks):
                            pid, url, raw = await fut
                            if pid in done:
                                continue
                            if raw is None:  # load/Cloudflare failure -> retry up to cap
                                attempts[pid] = attempts.get(pid, 0) + 1
                                counters["blocked"] += 1
                                if attempts[pid] >= args.max_attempts:
                                    write({"product_id": pid, "url": url, "ok": False,
                                           "reason": "blocked", "scraped_at": int(time.time())})
                                    done.add(pid)
                                continue
                            rec = normalize(raw, url, pid, args.shard)
                            if is_valid(rec):
                                rec["ok"] = True
                                write(rec)
                                done.add(pid)
                                counters["ok"] += 1
                                if counters["ok"] % 25 == 0:
                                    log(f"  ...{counters['ok']} ok, "
                                        f"{len(remaining())} left (shard {args.shard})")
                            else:  # page loaded but no product (discontinued/redirect) -> tombstone
                                write({"product_id": pid, "url": url, "ok": False,
                                       "reason": "no_product", "scraped_at": int(time.time())})
                                done.add(pid)
                                counters["tomb"] += 1
                    finally:
                        for t in tasks:
                            if not t.done():
                                t.cancel()
            except Exception as exc:  # noqa: BLE001
                msg = str(exc).lower()
                if "could not launch" in msg:
                    log(f"FATAL: {exc}")
                    return EXIT_NO_BROWSER
                if any(k in msg for k in RECYCLE_SIGNS):
                    log(f"browser recycle after: {exc}")
                    await asyncio.sleep(3)
                    continue
                raise
    finally:
        fout.close()

    log(f"DONE shard {args.shard}: ok={counters['ok']} tombstoned={counters['tomb']} "
        f"(blocked retries={counters['blocked']}). Total lines in {jsonl.name}: {len(done)}")
    return EXIT_OK


def main() -> int:
    ap = argparse.ArgumentParser(description="iHerb catalog scraper (sharded)")
    ap.add_argument("--shard", type=int, default=0, help="this machine's shard index (0-based)")
    ap.add_argument("--shards", type=int, default=2, help="total number of machines/shards")
    ap.add_argument("--out", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "data"))
    ap.add_argument("--concurrency", type=int, default=2, help="parallel product pages per browser")
    ap.add_argument("--delay", type=float, default=2.0, help="politeness delay per page (seconds, +50%% jitter)")
    ap.add_argument("--recycle", type=int, default=300, help="relaunch the browser every N products")
    ap.add_argument("--timeout", type=int, default=90, help="per-page navigation timeout (seconds)")
    ap.add_argument("--max-attempts", type=int, default=3, help="Cloudflare-block retry passes before giving up on an item")
    ap.add_argument("--limit", type=int, default=0, help="cap shard size (testing)")
    ap.add_argument("--rediscover", action="store_true", help="ignore cached urls.json and re-crawl sitemaps")
    ap.add_argument("--headed", action="store_true", help="show the browser window (debug / manual CF solve)")
    ap.add_argument("--channel", default="chrome", help="browser channel: chrome, msedge, or '' for bundled chromium")
    args = ap.parse_args()
    args.channel = args.channel or None

    if not (0 <= args.shard < args.shards):
        ap.error("--shard must be in [0, --shards)")

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    _lock = acquire_single_instance_lock(out_dir, args.shard)  # keep ref alive  # noqa: F841

    try:
        return asyncio.run(run(args))
    except KeyboardInterrupt:
        log("interrupted")
        return 130


if __name__ == "__main__":
    sys.exit(main())
