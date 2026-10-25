import re
from html.parser import HTMLParser

import httpx

import config

REQUEST_TIMEOUT = 25.0
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


def headers() -> dict:
    headers = {"Accept": "application/json"}
    if config.BROWSER_API_KEY:
        headers[config.BROWSER_API_KEY_HEADER] = config.BROWSER_API_KEY
    return headers


def normalize_results(payload) -> list[dict]:
    """Pull a list of {title, url, snippet} out of an arbitrary payload."""
    if not isinstance(payload, dict):
        return []

    container = payload.get("results", payload)
    web = []
    if isinstance(container, dict):
        web = container.get("web") or container.get("news") or []
    elif isinstance(container, list):
        web = container

    results = []
    for item in web:
        if not isinstance(item, dict):
            continue
        url = item.get("url") or item.get("link") or ""
        title = item.get("title") or item.get("name") or url
        snippet = item.get("description") or item.get("snippet") or ""
        if not snippet and isinstance(item.get("snippets"), list):
            snippet = item["snippets"][0] if item["snippets"] else ""
        results.append(
            {
                "title": str(title).strip(),
                "url": str(url).strip(),
                "snippet": str(snippet).strip(),
            }
        )
    return results


async def search(query: str, count: int = 10, offset: int = 0) -> list[dict]:
    """Run a web search and return normalized results."""
    body = {"query": query, "count": count, "offset": offset}
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        response = await client.post(
            config.BROWSER_API_URL, json=body, headers=headers()
        )
        response.raise_for_status()
        return normalize_results(response.json())


class TextExtractor(HTMLParser):
    """Very small HTML -> text converter (no external deps)."""

    _SKIP = {"script", "style", "noscript", "svg", "head", "template"}
    _BLOCK = {
        "p", "br", "div", "li", "ul", "ol", "tr", "section", "article",
        "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "pre",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP:
            self._skip += 1
        elif tag in self._BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self._SKIP and self._skip:
            self._skip -= 1
        elif tag in self._BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip and data.strip():
            self.parts.append(data.strip() + " ")

    def text(self) -> str:
        raw = "".join(self.parts)
        raw = re.sub(r"[ \t]+", " ", raw)
        raw = re.sub(r"\n[ \t]+", "\n", raw)
        raw = re.sub(r"\n{3,}", "\n\n", raw)
        return raw.strip()


async def fetch_text(url: str) -> str:
    """Fetch a URL and return readable text (HTML stripped if needed)."""
    async with httpx.AsyncClient(
        timeout=REQUEST_TIMEOUT, follow_redirects=True
    ) as client:
        response = await client.get(
            url, headers={"User-Agent": USER_AGENT, "Accept": "text/html,*/*"}
        )
        response.raise_for_status()
        content_type = response.headers.get("content-type", "").lower()
        body = response.text
        if "html" in content_type or "<html" in body[:1000].lower():
            parser = TextExtractor()
            parser.feed(body)
            return parser.text()
        return body.strip()
