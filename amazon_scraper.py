"""
amazon_scraper.py — Fetches and parses live Amazon product pages.

Ports FLM_Scraper.php: rotating user agents, browser-like headers, and
regex-based HTML parsing for title/brand/price/rating/review count/buy
box seller/stock status/main image/bullet points. Detects bot-detection
responses (403/503/429) and CAPTCHA pages and reports them as distinct,
recognizable errors rather than silently returning empty data.

Honest note: this is the same approach the original WordPress plugin
used (direct HTTP + regex against Amazon's HTML), which is inherently
fragile — Amazon changes its markup periodically and can rate-limit or
block scraping outright. It's ported faithfully, not idealized.
"""

import random
import re

import requests

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
]

MARKETPLACES = {
    "US": "https://www.amazon.com/dp/",
    "CA": "https://www.amazon.ca/dp/",
    "UK": "https://www.amazon.co.uk/dp/",
    "DE": "https://www.amazon.de/dp/",
    "FR": "https://www.amazon.fr/dp/",
    "IT": "https://www.amazon.it/dp/",
    "ES": "https://www.amazon.es/dp/",
    "MX": "https://www.amazon.com.mx/dp/",
}


class ScrapeError(Exception):
    """Raised for anything that isn't a clean 200 + parseable page."""
    def __init__(self, kind: str, message: str):
        self.kind = kind  # 'blocked' | 'not_found' | 'http_error' | 'captcha' | 'parse_failed'
        super().__init__(message)


def fetch(asin: str, marketplace: str = "US", timeout: int = 30) -> dict:
    """Fetch and parse one ASIN's live listing. Raises ScrapeError on failure."""
    base_url = MARKETPLACES.get(marketplace, MARKETPLACES["US"])
    url = f"{base_url}{asin}?th=1&psc=1"
    headers = {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "Referer": "https://www.google.com/",
        "DNT": "1",
        "Upgrade-Insecure-Requests": "1",
    }

    try:
        resp = requests.get(url, headers=headers, timeout=timeout)
    except requests.RequestException as e:
        raise ScrapeError("http_error", f"Request failed for ASIN {asin}: {e}")

    if resp.status_code in (503, 429):
        raise ScrapeError("blocked", f"Amazon returned HTTP {resp.status_code} — bot detection triggered.")
    if resp.status_code == 404:
        raise ScrapeError("not_found", f"ASIN {asin} not found on Amazon ({marketplace}).")
    if resp.status_code != 200:
        raise ScrapeError("http_error", f"HTTP {resp.status_code} fetching ASIN {asin}")

    body = resp.text
    if ("Type the characters you see in this image" in body
            or "Enter the characters you see below" in body
            or "api-services-support@amazon.com" in body):
        raise ScrapeError("captcha", f"Amazon served a CAPTCHA for ASIN {asin}.")

    return parse(body, asin)


def _strip_tags(s: str) -> str:
    return re.sub(r"<[^>]+>", "", s or "").strip()


def parse(html_body: str, asin: str) -> dict:
    """Parse the HTML body of an Amazon product page. Same regex strategy as the original."""
    data = {
        "asin": asin, "title": "", "brand": "", "price": "", "rating": "",
        "review_count": "", "buybox_seller": "", "in_stock": 1, "main_image_url": "",
        "bullet_1": "", "bullet_2": "", "bullet_3": "", "bullet_4": "", "bullet_5": "",
        "scrape_status": "ok",
    }

    # ---- Title ----
    m = re.search(r'<span[^>]*id=["\']productTitle["\'][^>]*>(.*?)</span>', html_body, re.S)
    if m:
        data["title"] = _strip_tags(m.group(1))

    # ---- Brand ----
    brand_raw = ""
    m = re.search(r'<a[^>]*id=["\']bylineInfo["\'][^>]*>(.*?)</a>', html_body, re.S)
    if m:
        brand_raw = _strip_tags(m.group(1))
    if not brand_raw:
        m = re.search(r'id=["\']brand["\'][^>]*>(.*?)<', html_body, re.S)
        if m:
            brand_raw = _strip_tags(m.group(1))
    if not brand_raw:
        m = re.search(r'Brand["\s]*:?["\s]*</th>.*?<td[^>]*>(.*?)</td>', html_body, re.S)
        if m:
            brand_raw = _strip_tags(m.group(1))
    if brand_raw:
        m = re.search(r"Visit the (.+?) Store", brand_raw, re.I)
        if m:
            brand_raw = m.group(1).strip()
        else:
            brand_raw = re.sub(r"^(Brand\s*:\s*|by\s+|Visit\s+the\s+|Store\s*$)", "", brand_raw, flags=re.I)
            brand_raw = re.sub(r"\s+Store$", "", brand_raw, flags=re.I).strip()
        data["brand"] = brand_raw

    # ---- Price ----
    price_found = False
    m = re.search(
        r'<span[^>]*(?:id=["\']corePrice_feature_div["\']|class=["\'][^"\']*(?:priceToPay|corePrice)[^"\']*["\'])'
        r'[^>]*>.*?<span[^>]*class=["\'][^"\']*a-offscreen[^"\']*["\'][^>]*>(.*?)</span>',
        html_body, re.S,
    )
    if m:
        price = _strip_tags(m.group(1))
        if price:
            data["price"] = price
            price_found = True

    if not price_found:
        for candidate in re.findall(r'<span[^>]*class=["\'][^"\']*a-offscreen[^"\']*["\'][^>]*>(.*?)</span>', html_body, re.S):
            t = _strip_tags(candidate)
            if re.match(r"^[$£€¥CA\s]*[\d,]+\.\d{2}$", t):
                data["price"] = t
                price_found = True
                break

    if not price_found:
        m = re.search(
            r'<span[^>]*class=["\'][^"\']*a-price-whole[^"\']*["\'][^>]*>([\d,]+)[^<]*</span>.*?'
            r'<span[^>]*class=["\'][^"\']*a-price-fraction[^"\']*["\'][^>]*>(\d+)</span>',
            html_body, re.S,
        )
        if m:
            whole = re.sub(r"[^\d,]", "", m.group(1))
            data["price"] = f"${whole}.{m.group(2)}"
            price_found = True

    if not price_found:
        for pid in ("priceblock_ourprice", "priceblock_dealprice", "priceblock_saleprice"):
            m = re.search(rf'<span[^>]*id=["\']{pid}["\'][^>]*>(.*?)</span>', html_body, re.S)
            if m:
                t = _strip_tags(m.group(1))
                if t:
                    data["price"] = t
                    price_found = True
                    break

    if not price_found:
        m = re.search(r'"displayPrice"\s*:\s*"([$£€¥][^"]+)"', html_body)
        if m:
            data["price"] = m.group(1)
            price_found = True

    if not price_found:
        m = re.search(r'class=["\'][^"\']*apexPriceToPay[^"\']*["\'][^>]*>.*?<span[^>]*>([$£€¥][\d,.]+)</span>', html_body, re.S)
        if m:
            data["price"] = m.group(1).strip()

    # ---- Buy box seller ----
    bb_found = False
    seller_blacklist = {"learn more about the seller", "learn more", "sold by", "ships from", "see all", "visit store"}

    m = re.search(r'id=["\']sellerProfileTriggerId["\'][^>]*>(.*?)</a>', html_body, re.S)
    if m:
        seller = _strip_tags(m.group(1))
        if seller and len(seller) < 200 and seller.strip().lower() not in seller_blacklist:
            data["buybox_seller"] = seller
            bb_found = True

    if not bb_found:
        m = re.search(
            r'(?:Sold by|Ships from)[^<]*</span>.*?<span[^>]*class=["\'][^"\']*(?:a-size-small|a-link-normal)[^"\']*["\'][^>]*>(.*?)</span>',
            html_body, re.S,
        )
        if m:
            seller = _strip_tags(m.group(1))
            if seller and len(seller) < 200:
                data["buybox_seller"] = seller
                bb_found = True

    if not bb_found:
        m = re.search(r'<div[^>]*id=["\']merchant-info["\'][^>]*>(.*?)</div>', html_body, re.S)
        if m:
            seller = _strip_tags(m.group(1))
            seller = re.sub(r"\s*(Ships from|Sold by|and|fulfilled by Amazon|\.)\s*", " ", seller, flags=re.I).strip()
            if seller and len(seller) < 200:
                data["buybox_seller"] = seller
                bb_found = True

    if not bb_found:
        m = re.search(r'id=["\']offer-display-feature-div["\'][^>]*>.*?Sold by.*?<a[^>]*>(.*?)</a>', html_body, re.S)
        if m:
            seller = _strip_tags(m.group(1))
            if seller and len(seller) < 200:
                data["buybox_seller"] = seller
                bb_found = True

    if not bb_found:
        m = re.search(r'"buyboxSellerName"\s*:\s*"([^"]{2,100})"', html_body)
        if m:
            data["buybox_seller"] = m.group(1)
            bb_found = True

    if not bb_found:
        m = re.search(r'"sellerName"\s*:\s*"([^"]{2,100})"', html_body)
        if m:
            data["buybox_seller"] = m.group(1)
            bb_found = True

    if not bb_found:
        m = re.search(r'(?:Dispatched from and sold by|Sold by)\s+<a[^>]*>(.*?)</a>', html_body, re.S)
        if m:
            seller = _strip_tags(m.group(1))
            if seller and len(seller) < 200:
                data["buybox_seller"] = seller

    # ---- In stock ----
    # Default in-stock; only flip to OOS on a clear signal in the FIRST clause of the
    # #availability text, so "other sellers may be unavailable" doesn't false-positive.
    data["in_stock"] = 1
    m = re.search(r'<div[^>]*id=["\']availability["\'][^>]*>(.*?)</div>', html_body, re.S)
    if m:
        av_full = re.sub(r"\s+", " ", _strip_tags(m.group(1))).strip()
        av_first = re.split(r"[,.]", av_full)[0].lower() if av_full else ""
        if ("currently unavailable" in av_first or "out of stock" in av_first
                or ("unavailable" in av_first and "in stock" not in av_first)):
            data["in_stock"] = 0
    elif re.search(r'<div[^>]*id=["\']outOfStock["\'][^>]*>', html_body, re.I):
        data["in_stock"] = 0

    # ---- Rating ----
    m = re.search(r'<span[^>]*id=["\']acrPopover["\'][^>]*title=["\']([0-9.]+) out of 5[^"\']*["\']', html_body)
    if not m:
        m = re.search(r'<span[^>]*title=["\']([0-9.]+) out of 5[^"\']*["\'][^>]*id=["\']acrPopover["\']', html_body)
    if not m:
        m = re.search(r'<div[^>]*id=["\']averageCustomerReviews["\'][^>]*>.*?([0-9]\.[0-9]) out of 5', html_body, re.S)
    if m:
        data["rating"] = f"{float(m.group(1)):.1f}"
    else:
        m = re.search(r'"ratingValue"\s*:\s*"?([0-9.]+)"?', html_body)
        if m:
            val = float(m.group(1))
            if 1.0 <= val <= 5.0:
                data["rating"] = f"{val:.1f}"

    # ---- Review count ----
    m = re.search(r'<span[^>]*id=["\']acrCustomerReviewText["\'][^>]*>(.*?)</span>', html_body, re.S)
    if m:
        data["review_count"] = _strip_tags(m.group(1))

    # ---- Main image ----
    m = re.search(r'"hiRes":"(https:[^"]+)"', html_body)
    if m:
        data["main_image_url"] = m.group(1)
    else:
        m = re.search(r'id=["\']landingImage["\'][^>]*data-old-hires=["\']([^"\']+)["\']', html_body)
        if m:
            data["main_image_url"] = m.group(1)
        else:
            m = re.search(r'id=["\']landingImage["\'][^>]*src=["\']([^"\']+)["\']', html_body)
            if m:
                data["main_image_url"] = m.group(1)

    # ---- Bullet points ----
    m = re.search(r'<div[^>]*id=["\']feature-bullets["\'][^>]*>(.*?)</div>', html_body, re.S)
    if m:
        bullets_html = m.group(1)
        raw_bullets = re.findall(r'<span[^>]*class=["\'][^"\']*a-list-item[^"\']*["\'][^>]*>(.*?)</span>', bullets_html, re.S)
        bullets = [_strip_tags(b) for b in raw_bullets]
        bullets = [b for b in bullets if b and len(b) > 5]
        for i in range(1, 6):
            data[f"bullet_{i}"] = bullets[i - 1] if i - 1 < len(bullets) else ""

    if not data["title"]:
        data["scrape_status"] = "parse_failed"

    return data


MONITORED_FIELDS = {
    "title": {"label": "Title", "severity": "high"},
    "price": {"label": "Price", "severity": "high"},
    "buybox_seller": {"label": "Buy box seller", "severity": "high"},
    "in_stock": {"label": "Stock status", "severity": "high"},
    "main_image_url": {"label": "Main image", "severity": "high"},
    "brand": {"label": "Brand", "severity": "medium"},
    "rating": {"label": "Star rating", "severity": "medium"},
    "review_count": {"label": "Review count", "severity": "low"},
    "bullet_1": {"label": "Bullet point 1", "severity": "medium"},
    "bullet_2": {"label": "Bullet point 2", "severity": "medium"},
    "bullet_3": {"label": "Bullet point 3", "severity": "medium"},
    "bullet_4": {"label": "Bullet point 4", "severity": "medium"},
    "bullet_5": {"label": "Bullet point 5", "severity": "medium"},
}
