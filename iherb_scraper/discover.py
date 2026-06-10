"""Product URL discovery via iHerb's XML sitemaps (fetched from inside a
Cloudflare-cleared page context so the XML isn't 403'd)."""
from __future__ import annotations

import re
from typing import Callable

HOME = "https://www.iherb.com/"
SITEMAP_INDEX = "https://www.iherb.com/sitemap_index.xml"
PRODUCT_SITEMAP_RE = re.compile(r"products-\d+-www-\d+\.xml")
LOC_RE = re.compile(r"<loc>([^<]+)</loc>")
# iHerb product URLs look like /pr/<slug>/<numericId>
PR_RE = re.compile(r"/pr/[^\s<>\"']+?/(\d+)")


async def _fetch_text(page, url: str) -> str:
    return await page.evaluate(
        "async (u) => { const r = await fetch(u); return await r.text(); }", url
    )


async def discover_product_urls(page, *, log: Callable[..., None] = print) -> dict[str, str]:
    """Return {product_id: product_url} across all product sitemaps.

    Navigates the page to the iHerb homepage first so that (a) Cloudflare clears
    and (b) the in-page fetch() of the sitemap XML is same-origin (a fetch from
    about:blank would be cross-origin and get blocked).
    """
    await page.goto(HOME, wait_until="domcontentloaded", timeout=90_000)
    for _ in range(30):
        t = (await page.title()).lower()
        if "just a moment" not in t and "security" not in t and "attention" not in t:
            break
        await page.wait_for_timeout(1500)
    await page.wait_for_timeout(800)

    index_xml = await _fetch_text(page, SITEMAP_INDEX)
    child_sitemaps = [u for u in LOC_RE.findall(index_xml) if PRODUCT_SITEMAP_RE.search(u)]
    log(f"  sitemap index -> {len(child_sitemaps)} product sitemap(s)")

    urls: dict[str, str] = {}
    for sm in child_sitemaps:
        try:
            xml = await _fetch_text(page, sm)
        except Exception as exc:  # noqa: BLE001
            log(f"  WARN: failed to fetch {sm}: {exc}")
            continue
        added = 0
        for loc in LOC_RE.findall(xml):
            m = PR_RE.search(loc)
            if m:
                pid = m.group(1)
                if pid not in urls:
                    urls[pid] = loc
                    added += 1
        log(f"  {sm.split('/')[-1]}: +{added} products (running total {len(urls)})")
    return urls
