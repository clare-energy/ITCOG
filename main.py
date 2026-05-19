from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from bs4 import BeautifulSoup, Comment
from urllib.parse import urljoin, urlparse
import httpx
import re
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(docs_url=None, redoc_url=None)

BASE = "https://www.irishtimes.com"
IT_HOSTS = {"www.irishtimes.com", "irishtimes.com"}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-IE,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
}

DECOMPOSE_TAGS = {
    "script", "style", "iframe", "svg", "canvas", "noscript",
    "video", "audio", "source", "track", "object", "embed",
    "picture", "form", "input", "button", "select", "textarea",
    "nav",   # we replace all navs with our own hardcoded one
}

CLUTTER_RE = re.compile(
    r"\b(advertisement|sponsored|piano|promo|popup|modal|"
    r"cookie.?banner|gdpr|newsletter|signup|sign-up|banner|sidebar|"
    r"related.?articles|trending|most.?read|recommended|social.?share|"
    r"share.?bar|comments?-section|commenting|disqus|sticky|overlay|"
    r"bottom.?bar|top.?bar|breaking.?bar|breaking-news-bar|"
    r"live.?blog.?controls|toolbar|arcad.?feature)\b",
    re.IGNORECASE,
)

ARTICLE_URL_RE = re.compile(r"/\d{4}/\d{2}/\d{2}/")

KEEP_ATTRS = {
    "a":    {"href"},
    "time": {"datetime"},
    "td":   {"colspan", "rowspan"},
    "th":   {"colspan", "rowspan"},
}

# Hardcoded section links — no scraping, no duplication
SECTIONS_NAV = (
    '<nav id="sections">'
    '<a href="/latest/">Latest</a>'
    '<a href="/ireland/">Ireland</a>'
    '<a href="/world/">World</a>'
    '<a href="/opinion/">Opinion</a>'
    '<a href="/business/">Business</a>'
    '<a href="/sport/">Sport</a>'
    '<a href="/politics/">Politics</a>'
    '<a href="/culture/">Culture</a>'
    '<a href="/your-money/">Your Money</a>'
    '<a href="/property/">Property</a>'
    '<a href="/food/">Food</a>'
    '<a href="/life-style/">Life &amp; Style</a>'
    '</nav>'
)

# ── CSS ───────────────────────────────────────────────────────────────────────

BASE_CSS = """\
:root { --red: #c00; }
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: Georgia, 'Times New Roman', serif;
  max-width: 860px;
  margin: 0 auto;
  padding: 0 1rem 3rem;
  background: #fff;
  color: #111;
  line-height: 1.65;
}
#site-bar {
  background: var(--red);
  color: #fff;
  padding: 0.5rem 1rem;
  margin: 0 -1rem 0;
  font-size: 0.95rem;
  font-family: Arial, sans-serif;
}
#site-bar a { color: #fff; font-weight: 700; text-decoration: none; letter-spacing: 0.02em; }
#sections {
  font-family: Arial, sans-serif;
  font-size: 0.82rem;
  font-weight: 600;
  padding: 0.45rem 0 0.55rem;
  margin-bottom: 1rem;
  border-bottom: 2px solid var(--red);
  display: flex;
  flex-wrap: wrap;
  gap: 0.1rem 0;
}
#sections a {
  color: #333;
  text-decoration: none;
  padding: 0.15rem 0.55rem;
  border-right: 1px solid #ddd;
}
#sections a:last-child { border-right: none; }
#sections a:hover { color: var(--red); }
a { color: var(--red); text-decoration: none; }
a:hover { text-decoration: underline; }
h1 { font-size: 1.75rem; line-height: 1.2; margin: 1rem 0 0.4rem; }
h2 { font-size: 1.3rem; line-height: 1.3; margin: 1rem 0 0.35rem; }
h3 { font-size: 1.1rem; line-height: 1.35; margin: 0.8rem 0 0.3rem; }
h4, h5, h6 { font-size: 1rem; margin: 0.6rem 0 0.25rem; }
p { margin: 0.55rem 0; }
ul, ol { padding-left: 1.4rem; margin: 0.4rem 0; }
li { margin: 0.15rem 0; }
time { display: block; color: #666; font-size: 0.82rem; margin: 0.3rem 0 0.6rem; font-family: Arial, sans-serif; }
blockquote {
  border-left: 3px solid var(--red);
  margin: 0.8rem 0;
  padding: 0.2rem 0 0.2rem 0.9rem;
  color: #444;
  font-style: italic;
}
article { border-bottom: 1px solid #e8e8e8; padding-bottom: 1rem; margin-bottom: 1rem; }
hr { border: none; border-top: 1px solid #e8e8e8; margin: 1rem 0; }
figure { margin: 0.5rem 0; }
figcaption { font-size: 0.8rem; color: #555; font-family: Arial, sans-serif; }
table { border-collapse: collapse; width: 100%; margin: 0.5rem 0; }
td, th { border: 1px solid #ddd; padding: 0.3rem 0.5rem; text-align: left; }
th { background: #f4f4f4; }
"""

ARTICLE_CSS = """\
body { max-width: 1290px; }
article p, article li, article blockquote { font-size: 1.5rem; line-height: 1.7; }
article h1 { font-size: 2.25rem; }
article h2 { font-size: 1.8rem; }
article h3 { font-size: 1.5rem; }
"""

LISTING_CSS = """\
body { max-width: 1290px; }
.cards-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 1.25rem;
  margin-top: 0.5rem;
}
@media (max-width: 1000px) { .cards-grid { grid-template-columns: repeat(2, 1fr); } }
@media (max-width: 600px)  { .cards-grid { grid-template-columns: 1fr; } }
.card {
  border: 1px solid #e2e2e2;
  padding: 0.85rem 0.9rem;
  background: #fff;
}
.card-section {
  font-size: 1rem;
  font-family: Arial, sans-serif;
  text-transform: uppercase;
  font-weight: 700;
  color: var(--red);
  letter-spacing: 0.06em;
  margin-bottom: 0.35rem;
}
.card a {
  font-size: 1.4rem;
  line-height: 1.35;
  font-weight: 600;
  display: block;
  color: #111;
  text-decoration: none;
}
.card a:hover { color: var(--red); }
"""

# ── Helpers ───────────────────────────────────────────────────────────────────

def get_page_type(path: str) -> str:
    if ARTICLE_URL_RE.search(path):
        return "article"
    return "listing"  # homepage and all section/topic pages


def proxy_href(href: str, page_url: str) -> str:
    href = href.strip()
    if not href or href.startswith(("javascript:", "mailto:", "tel:", "#")):
        return href
    resolved = urljoin(page_url, href)
    parsed = urlparse(resolved)
    if not parsed.hostname or parsed.hostname in IT_HOSTS:
        path = parsed.path or "/"
        qs = ("?" + parsed.query) if parsed.query else ""
        return path + qs
    return resolved


def is_clutter(tag) -> bool:
    classes = " ".join(tag.get("class", []))
    return bool(CLUTTER_RE.search(classes + " " + tag.get("id", "")))


def extract_home_grid(soup: BeautifulSoup, page_url: str) -> str:
    """Extract article cards from the raw soup (call BEFORE any cleaning)."""
    cards = soup.find_all("div", class_="b-flex-promo-card")
    seen: set[str] = set()
    items: list[tuple[str, str, str]] = []

    for card in cards:
        # Image links carry aria-hidden="true" — skip them
        link = next(
            (a for a in card.find_all("a", href=True) if not a.get("aria-hidden")),
            None,
        )
        if not link:
            continue
        href = proxy_href(link.get("href", "").strip(), page_url)
        title = link.get_text(strip=True)
        if not title or not href or href in seen:
            continue
        seen.add(href)
        section = href.strip("/").split("/")[0].replace("-", " ").title()
        items.append((href, title, section))

    rows = "\n".join(
        f'<div class="card">'
        f'<div class="card-section">{section}</div>'
        f'<a href="{href}">{title}</a>'
        f"</div>"
        for href, title, section in items
    )
    return f'<div class="cards-grid">\n{rows}\n</div>' if rows else ""


def strip_attrs(soup: BeautifulSoup, page_url: str) -> None:
    for tag in soup.find_all(True):
        allowed = KEEP_ATTRS.get(tag.name, set())
        for attr in list(tag.attrs):
            if attr not in allowed:
                del tag.attrs[attr]
        if tag.name == "a" and tag.get("href"):
            tag["href"] = proxy_href(tag["href"], page_url)


# ── Core clean ────────────────────────────────────────────────────────────────

def clean(soup: BeautifulSoup, page_url: str, page_type: str) -> str:
    # For listing pages: extract cards from raw soup BEFORE anything is stripped
    grid_html = ""
    if page_type == "listing":
        grid_html = extract_home_grid(soup, page_url)

    for comment in soup.find_all(string=lambda t: isinstance(t, Comment)):
        comment.extract()

    # DECOMPOSE_TAGS includes 'nav' so all IT navs are removed;
    # we replace them with our own hardcoded SECTIONS_NAV
    for name in DECOMPOSE_TAGS:
        for tag in soup.find_all(name):
            tag.decompose()

    for tag in list(soup.find_all(True)):
        try:
            if is_clutter(tag):
                tag.decompose()
        except Exception:
            pass

    strip_attrs(soup, page_url)

    if page_type == "listing":
        return grid_html

    body = soup.find("body")
    return body.decode_contents() if body else str(soup)


# ── Page wrapper ──────────────────────────────────────────────────────────────

def wrap(inner_html: str, page_type: str) -> str:
    extra = ARTICLE_CSS if page_type == "article" else (LISTING_CSS if page_type == "listing" else "")
    return (
        '<!DOCTYPE html>\n<html lang="en">\n<head>\n'
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width,initial-scale=1">\n'
        '<title>Irish Times — Reader</title>\n'
        f'<style>{BASE_CSS}{extra}</style>\n'
        '</head>\n<body>\n'
        '<div id="site-bar"><a href="/">&#9632; Irish Times — Reader View</a></div>\n'
        + SECTIONS_NAV + "\n"
        + inner_html
        + '\n</body>\n</html>'
    )


# ── Route ─────────────────────────────────────────────────────────────────────

@app.get("/{path:path}", response_class=HTMLResponse)
async def proxy(request: Request, path: str = ""):
    target = BASE + "/" + path
    if request.url.query:
        target += "?" + request.url.query

    logger.info("Fetching %s", target)

    async with httpx.AsyncClient(follow_redirects=True, timeout=20, headers=HEADERS) as client:
        try:
            resp = await client.get(target)
        except httpx.RequestError as exc:
            return HTMLResponse(wrap(f"<p>Could not reach Irish Times: {exc}</p>", "section"), status_code=502)

    if resp.status_code >= 400:
        return HTMLResponse(
            wrap(f"<p>Irish Times returned HTTP {resp.status_code}.</p>", "section"),
            status_code=resp.status_code,
        )

    page_type = get_page_type(path)
    soup = BeautifulSoup(resp.text, "lxml")
    inner = clean(soup, str(resp.url), page_type)
    return HTMLResponse(wrap(inner, page_type))
