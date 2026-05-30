"""
Dira Dashboard Server
Run: python server.py
Then open http://localhost:8080
"""
import sys, json, asyncio, os, webbrowser, time
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from playwright.async_api import async_playwright

sys.stdout.reconfigure(encoding="utf-8")

# On Render the PORT env-var is set automatically; RENDER=true signals production mode.
IS_PROD = bool(os.environ.get("RENDER"))
PORT    = int(os.environ.get("PORT", 8080))
HOST    = "0.0.0.0" if IS_PROD else "localhost"

def log(msg, indent=0):
    ts = datetime.now().strftime("%H:%M:%S")
    prefix = "  " * indent
    print(f"[{ts}] {prefix}{msg}", flush=True)

# Always resolve paths relative to this file, regardless of where Python is invoked from.
BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
CACHE_FILE = os.path.join(BASE_DIR, "dira_cache.json")
INDEX_FILE = os.path.join(BASE_DIR, "index.html")

_data        = None
_index_cache = None   # index.html served from memory after first read


async def _fetch_live():
    t0 = time.time()
    async with async_playwright() as p:
        log("Starting headless browser...", 1)
        browser = await p.chromium.launch(headless=True)
        page    = await browser.new_page()

        log("Opening dira.moch.gov.il/ProjectsList ...", 1)
        await page.goto(
            "https://dira.moch.gov.il/ProjectsList",
            wait_until="networkidle",
            timeout=30000,
        )
        log(f"Page loaded ({time.time()-t0:.1f}s) — waiting for app to settle ...", 1)
        await page.wait_for_timeout(1500)

        log("Calling projects API (PageSize=500) ...", 1)
        t1 = time.time()
        raw = await page.evaluate("""
            async () => {
                const param = encodeURIComponent(
                    '?firstApplicantIdentityNumber=&secondApplicantIdentityNumber=' +
                    '&ProjectStatus=4&Entitlement=1&PageNumber=1&PageSize=500&IsInit=true&'
                );
                const res = await fetch('/api/Invoker?method=Projects&param=' + param);
                return await res.text();
            }
        """)
        log(f"API responded ({time.time()-t1:.1f}s) — parsing JSON ...", 1)

        data  = json.loads(raw)
        items = data.get("ProjectItems", [])
        log(f"Done — {len(items)} projects fetched in {time.time()-t0:.1f}s total", 1)
        await browser.close()
    return items


def get_data(force=False):
    global _data
    if not force and _data:
        return _data

    if not force and os.path.exists(CACHE_FILE):
        with open(CACHE_FILE, encoding="utf-8") as f:
            _data = json.load(f)
        expected = _data[0].get("OpenLotteriesCount", 0) if _data else 0
        if expected > 0 and len(_data) < expected:
            log(f"Cache incomplete ({len(_data)} of {expected}) — discarding and re-fetching...")
            _data = None
            os.remove(CACHE_FILE)
        else:
            log(f"Loaded {len(_data)} of {expected} projects from cache")
            return _data

    log("No valid cache — fetching live data from dira.moch.gov.il ...")
    async def _fetch_with_timeout():
        try:
            return await asyncio.wait_for(_fetch_live(), timeout=120.0)
        except asyncio.TimeoutError:
            log("Fetch timed out after 120 seconds — try again later")
            return []
    _data = asyncio.run(_fetch_with_timeout())
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(_data, f, ensure_ascii=False)
    log(f"Cache saved ({len(_data)} projects)")
    return _data


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass  # handled manually below

    def do_GET(self):
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            self._send_file(INDEX_FILE, "text/html; charset=utf-8")
        elif path == "/api/data":
            log("Dashboard requested data")
            self._send_json(get_data())
        elif path == "/api/refresh":
            if IS_PROD:
                log("Refresh requested — not available in production (site is geo-restricted to Israel)")
                self._send_json(get_data())   # return cached data unchanged
            else:
                log("Manual refresh triggered — re-fetching live data ...")
                self._send_json(get_data(force=True))
        else:
            self.send_error(404)

    def _send_file(self, name, content_type):
        global _index_cache
        try:
            if name == INDEX_FILE and _index_cache is not None:
                data = _index_cache
            else:
                with open(name, "rb") as f:
                    data = f.read()
                if name == INDEX_FILE:
                    _index_cache = data
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.end_headers()
            self.wfile.write(data)
        except FileNotFoundError:
            self.send_error(404, f"{name} not found")

    def _send_json(self, obj):
        raw = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.end_headers()
        self.wfile.write(raw)


if __name__ == "__main__":
    print(f"\n{'='*42}")
    print(f"  Dira Dashboard  —  {'Render (production)' if IS_PROD else f'http://localhost:{PORT}'}")
    print(f"{'='*42}\n")
    get_data()
    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Server ready on {HOST}:{PORT}")
    if not IS_PROD:
        print("  Press Ctrl+C to stop.\n")
        webbrowser.open(f"http://localhost:{PORT}")
    try:
        HTTPServer((HOST, PORT), Handler).serve_forever()
    except KeyboardInterrupt:
        print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Server stopped.")
