"""Playwright Cloudflare-bypass fetcher for iHerb (cross-platform: macOS + Windows).

Mirrors the FragranceX harness: stealth Chromium + homepage warm-up to obtain
Cloudflare clearance cookies, then navigates product pages and extracts a
structured record *inside the page* (JSON-LD + rendered specs) via one JS call.
"""
from __future__ import annotations

import asyncio
import logging
import sys
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Optional

from playwright.async_api import Browser, BrowserContext, Page, async_playwright

logger = logging.getLogger(__name__)

# Windows: Playwright spawns the browser via a subprocess transport that requires
# the Proactor event loop. It is the default on Python 3.8+, but set it explicitly.
if sys.platform.startswith("win"):
    try:
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    except Exception:
        pass

HOME = "https://www.iherb.com/"

# Present a Chrome fingerprint that MATCHES the host OS — a Windows machine looks
# like Windows Chrome, a Mac like Mac Chrome — and strips the headless marker.
# Cloudflare checks internal consistency, so claiming the real OS is safest.
_IS_WIN = sys.platform.startswith("win")
if _IS_WIN:
    DEFAULT_UA = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
    _PLATFORM = "Win32"
    _WEBGL_VENDOR = "Google Inc. (Intel)"
    _WEBGL_RENDERER = "ANGLE (Intel, Intel(R) UHD Graphics Direct3D11 vs_5_0 ps_5_0, D3D11)"
else:
    DEFAULT_UA = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
    _PLATFORM = "MacIntel"
    _WEBGL_VENDOR = "Intel Inc."
    _WEBGL_RENDERER = "Intel Iris OpenGL Engine"

STEALTH_LAUNCH_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--disable-features=IsolateOrigins,site-per-process",
    "--no-sandbox",
    "--disable-dev-shm-usage",
]

# Fallback fingerprint masking, applied only if playwright-stealth is unavailable.
STEALTH_INIT_JS = """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
Object.defineProperty(navigator, 'plugins', {
  get: () => [
    { name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer' },
    { name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai' },
    { name: 'Native Client', filename: 'internal-nacl-plugin' },
  ],
});
window.chrome = { runtime: {}, app: {}, csi: () => {}, loadTimes: () => {} };
const getParameter = WebGLRenderingContext.prototype.getParameter;
WebGLRenderingContext.prototype.getParameter = function(param) {
  if (param === 37445) return '__WEBGL_VENDOR__';
  if (param === 37446) return '__WEBGL_RENDERER__';
  return getParameter.call(this, param);
};
"""
STEALTH_INIT_JS = STEALTH_INIT_JS.replace("__WEBGL_VENDOR__", _WEBGL_VENDOR).replace(
    "__WEBGL_RENDERER__", _WEBGL_RENDERER
)

# One-shot extraction run inside the cleared product page. Returns a raw dict that
# parser.normalize() turns into the final record.
EXTRACT_JS = r"""
() => {
  const out = {};

  // --- schema.org Product JSON-LD (primary source) ---
  let product = null;
  for (const s of document.querySelectorAll('script[type="application/ld+json"]')) {
    try {
      const j = JSON.parse(s.textContent);
      const arr = Array.isArray(j) ? j : (j['@graph'] ? j['@graph'] : [j]);
      for (const o of arr) { if (o && o['@type'] === 'Product') product = o; }
    } catch (e) {}
  }
  out.jsonld = product;

  const body = document.body ? document.body.innerText : '';
  out.title = document.title || '';
  out.h1 = (document.querySelector('h1') || {}).innerText || null;

  // --- displayed (possibly discounted) price ---
  let displayPrice = null;
  for (const el of document.querySelectorAll('[class*="price"],[data-testid*="price"],[id*="price"]')) {
    const m = (el.innerText || '').match(/\$\s*([\d,]+\.\d{2})/);
    if (m) { displayPrice = parseFloat(m[1].replace(/,/g, '')); break; }
  }
  if (displayPrice === null) {
    const m = body.match(/\$\s*([\d,]+\.\d{2})/);
    if (m) displayPrice = parseFloat(m[1].replace(/,/g, ''));
  }
  out.display_price = displayPrice;
  out.price_hidden = /why don.?t we show the price/i.test(body);

  // --- spec key/values from rendered product-details text ---
  const field = (re) => { const m = body.match(re); return m ? m[1].trim() : null; };
  out.specs = {
    upc: field(/UPC\s*:?\s*([0-9]{8,14})/i),
    product_code: field(/Product code\s*:?\s*([A-Za-z0-9\-]+)/i),
    package_quantity: field(/Package quantity\s*:?\s*([^\n]{1,40})/i),
    best_by: field(/Best by\s*:?\s*([0-9\/]{4,10})/i),
    first_available: field(/(?:Date )?[Ff]irst available\s*:?\s*([0-9\/]{4,10})/i),
    shipping_weight: field(/Shipping weight\s*:?\s*([^\n]{1,30})/i),
    dimensions: field(/Dimensions\s*:?\s*([^\n]{1,60})/i),
  };

  // --- text sections (capped) ---
  const sect = (label, maxLen) => {
    const lc = body.toLowerCase();
    const i = lc.indexOf(label.toLowerCase());
    if (i < 0) return null;
    return body.slice(i, i + (maxLen || 1500)).trim();
  };
  out.supplement_facts = /supplement facts/i.test(body) ? sect('Supplement Facts', 1800) : null;
  out.ingredients = sect('Other Ingredients', 700) || sect('Ingredients', 900);
  out.directions = sect('Suggested Use', 600) || sect('Directions', 600);
  out.warnings = sect('Warning', 600);

  // --- gallery images limited to this product's image folder ---
  let images = [];
  try {
    let main = '';
    if (product) main = (typeof product.image === 'string') ? product.image : ((product.image || [])[0] || '');
    const mm = main.match(/\/images\/([^/]+)\/([^/]+)\//);
    const prefix = mm ? ('/images/' + mm[1] + '/' + mm[2] + '/') : null;
    const seen = new Set();
    document.querySelectorAll('img[src*="images-iherb"]').forEach((img) => {
      const src = img.currentSrc || img.src || img.getAttribute('data-src') || '';
      if (!src) return;
      if (prefix && src.indexOf(prefix) < 0) return;
      const key = (src.split('/images/')[1] || src).replace(/\?.*$/, '');
      if (seen.has(key)) return;
      seen.add(key);
      images.push(src);
    });
    if (images.length === 0 && main) images = [main];
  } catch (e) {}
  out.images = images;

  return out;
}
"""


def _is_challenge(title: str) -> bool:
    t = (title or "").lower()
    return ("just a moment" in t) or ("security verification" in t) or ("attention required" in t)


@asynccontextmanager
async def browser_session(
    *, headless: bool = True, channel: Optional[str] = "chrome"
) -> AsyncIterator[tuple[Browser, BrowserContext]]:
    async with async_playwright() as p:
        attempts: list[dict] = []
        if channel:
            attempts.append({"channel": channel})  # system Google Chrome / Edge
        attempts.append({})  # bundled chromium (playwright install chromium)

        browser = None
        errors: list[str] = []
        for extra in attempts:
            try:
                browser = await p.chromium.launch(
                    headless=headless,
                    args=STEALTH_LAUNCH_ARGS,
                    ignore_default_args=["--enable-automation"],
                    **extra,
                )
                break
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{extra}: {exc}")

        if browser is None:
            raise RuntimeError(
                "Could not launch a browser. Install Google Chrome or run:\n"
                "  python -m playwright install chromium\n\n" + "\n".join(errors[-3:])
            )

        context = await browser.new_context(
            user_agent=DEFAULT_UA,
            locale="en-US",
            timezone_id="America/New_York",
            viewport={"width": 1366, "height": 768},
            extra_http_headers={
                "Accept-Language": "en-US,en;q=0.9",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            },
        )
        try:
            from playwright_stealth import Stealth

            await Stealth(navigator_platform_override=_PLATFORM).apply_stealth_async(context)
        except Exception as exc:  # noqa: BLE001
            logger.warning("playwright-stealth unavailable (%s); using manual init script", exc)
            await context.add_init_script(STEALTH_INIT_JS)

        try:
            yield browser, context
        finally:
            try:
                await context.close()
            finally:
                await browser.close()


async def _wait_cleared(page: Page, *, max_sec: float = 45) -> bool:
    steps = max(1, int(max_sec / 1.5))
    for _ in range(steps):
        try:
            title = await page.title()
        except Exception:
            return False
        if not _is_challenge(title):
            return True
        await page.wait_for_timeout(1500)
    return False


async def warm_up(context: BrowserContext) -> bool:
    """Visit the homepage to establish Cloudflare clearance cookies for the context."""
    page = await context.new_page()
    try:
        await page.goto(HOME, wait_until="domcontentloaded", timeout=90_000)
        ok = await _wait_cleared(page, max_sec=45)
        await page.wait_for_timeout(1500)
        return ok
    finally:
        await page.close()


async def fetch_product(page: Page, url: str, *, timeout_ms: int = 90_000) -> Optional[dict[str, Any]]:
    """Navigate to a product page and return the raw extraction dict, or None if
    the page could not be loaded / cleared (caller treats None as retryable)."""
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
    except Exception:
        return None
    if not await _wait_cleared(page, max_sec=45):
        return None
    try:
        await page.wait_for_load_state("networkidle", timeout=12_000)
    except Exception:
        pass
    await page.wait_for_timeout(1200)
    try:
        raw = await page.evaluate(EXTRACT_JS)
    except Exception:
        return None
    if isinstance(raw, dict):
        raw["url"] = url
    return raw
