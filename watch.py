#!/usr/bin/env python3
"""Surveillance des prix / promos -> rapport Telegram (compatible bot Hermes).

La source de verite est products.json. L'historique est ecrit dans history.json
(commite par GitHub Actions a chaque execution).
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

BASE = Path(__file__).resolve().parent
PRODUCTS_FILE = BASE / "products.json"
HISTORY_FILE = BASE / "history.json"
CREDENTIALS_FILE = Path.home() / ".config/price-watch/telegram.env"
MAX_POINTS = 240      # points bruts conservés (sécurité)
RAW_DAYS = 7          # détails horaires récents
SERIES_DAYS = 92      # ~3 mois de courbe quotidienne
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
TIMEOUT = 30


def log(msg: str) -> None:
    print(f"[{datetime.now(timezone.utc).isoformat(timespec='seconds')}] {msg}", flush=True)


def to_float(value) -> float | None:
    """Parse '169,99', '1.234,56', 649 ou '649 EUR' -> float."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).replace("\u00a0", "").replace(" ", "").replace("€", "").strip()
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        s = s.replace(",", ".")
    m = re.search(r"-?\d+(?:\.\d+)?", s)
    return float(m.group()) if m else None


def money(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.2f} €"


def load_credentials() -> None:
    """Charge les secrets locaux (Telegram, ScraperAPI) depuis ~/.config/price-watch."""
    files = [CREDENTIALS_FILE, CREDENTIALS_FILE.parent / "scraper.env"]
    for path in files:
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.strip().startswith("#"):
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip())


def load_config() -> dict:
    with PRODUCTS_FILE.open(encoding="utf-8") as f:
        return json.load(f)


def load_history() -> dict:
    if not HISTORY_FILE.exists():
        return {"products": {}, "last_digest": None}
    try:
        with HISTORY_FILE.open(encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {"products": {}, "last_digest": None}


def compact_record(record: dict, now: datetime) -> None:
    """Consolide les vieux points en moyennes quotidiennes (courbe sur 3 mois)."""
    cutoff = (now - timedelta(days=RAW_DAYS)).date().isoformat()
    keep, old = [], []
    for point in record.get("points", []):
        day = str(point.get("t", ""))[:10]
        (old if day and day < cutoff else keep).append(point)
    if old:
        daily = record.setdefault("daily", [])
        by_day = {d["d"]: d for d in daily}
        for point in old:
            day = str(point.get("t", ""))[:10]
            price = point.get("price")
            if price is None:
                continue
            bucket = by_day.get(day)
            if bucket is None:
                bucket = {"d": day, "min": price, "max": price, "sum": price, "n": 1}
                daily.append(bucket)
                by_day[day] = bucket
            else:
                bucket["min"] = min(bucket["min"], price)
                bucket["max"] = max(bucket["max"], price)
                bucket["sum"] += price
                bucket["n"] += 1
        for bucket in daily:
            if bucket.get("n"):
                bucket["avg"] = round(bucket["sum"] / bucket["n"], 2)
            bucket.pop("sum", None)
        record["daily"] = sorted(daily, key=lambda d: d["d"])[-SERIES_DAYS:]
    record["points"] = keep[-MAX_POINTS:]
    prices = [p["price"] for p in record["points"] if p.get("price") is not None]
    prices += [d["max"] for d in record.get("daily", []) if d.get("max") is not None]
    if prices:
        record["highest"] = max(prices)


def save_history(history: dict) -> None:
    for record in history.get("products", {}).values():
        compact_record(record, datetime.now(timezone.utc))
    with HISTORY_FILE.open("w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)
        f.write("\n")


def shopify_offer(url: str, cfg: dict) -> dict | None:
    """Boutiques Shopify : /products/<handle>.js donne prix + stock fiables."""
    if "/products/" not in url:
        return None
    base = url.split("?")[0].split("#")[0].rstrip("/")
    try:
        r = requests.get(base + ".js", headers={"User-Agent": UA}, timeout=TIMEOUT)
        if not r.ok:
            return None
        start = r.text.find("{")
        if start < 0:
            return None
        data = json.loads(r.text[start:])
    except (requests.RequestException, json.JSONDecodeError, ValueError):
        return None
    variants = data.get("variants") or []
    if not variants:
        return None
    sku = str(cfg.get("variant_sku", "")) if cfg.get("variant_sku") else None
    chosen = next((v for v in variants if str(v.get("sku")) == sku), None) if sku else None
    if chosen is None:
        chosen = variants[0]
    price = chosen.get("price")
    if isinstance(price, (int, float)) and price > 1000:
        price = price / 100  # Shopify renvoie des centimes
    return {
        "price": price,
        "priceCurrency": "EUR",
        "availability": "InStock" if chosen.get("available") else "OutOfStock",
    }


def fetch_page(url: str, cfg: dict, allow_text: bool = True) -> dict:
    """Retourne {soup, text, source, price_hint}.

    1. GET direct + JSON-LD, 2. GET direct + texte, 3. lecteur de texte en repli.
    Deux tentatives à chaque étape : les IP GitHub Actions sont parfois limitées.
    """
    shop = shopify_offer(url, cfg)
    if shop:
        return {
            "soup": BeautifulSoup("", "html.parser"),
            "text": "",
            "source": "shopify",
            "price_hint": None,
            "offer_hint": shop,
        }

    errors: list[str] = []
    for attempt in range(2):
        try:
            r = requests.get(
                url,
                headers={
                    "User-Agent": UA,
                    "Accept-Language": "fr-BE,fr;q=0.9,nl;q=0.8,en;q=0.7",
                    "Accept": "text/html,application/xhtml+xml",
                },
                timeout=TIMEOUT,
            )
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "html.parser")
            text = soup.get_text(" ", strip=True)
            if jsonld_products(soup):
                return {"soup": soup, "text": text, "source": "direct", "price_hint": None,
                        "offer_hint": None}
            meta = meta_offer(soup)
            if meta and meta.get("price") is not None:
                return {"soup": soup, "text": text, "source": "direct-meta", "price_hint": None,
                        "offer_hint": meta}
            hint = text_price(text, cfg)
            if hint is not None:
                return {"soup": soup, "text": text, "source": "direct-texte", "price_hint": hint,
                        "offer_hint": None}
        except requests.RequestException as e:
            errors.append(f"direct: {type(e).__name__}: {str(e)[:80]}")
        if attempt == 0:
            time.sleep(1)

    # Repli optionnel : API de scraping (contourne DataDome/Cloudflare) si clé fournie.
    scraper_key = os.environ.get("SCRAPER_API_KEY")
    if scraper_key:
        forced = cfg.get("scraper_mode")
        modes = (forced,) if forced else ("plain", "render", "premium")
        for mode in modes:
            try:
                params = {"api_key": scraper_key, "url": url, "country_code": "be"}
                if mode in ("render", "premium"):
                    params["render"] = "true"
                if mode == "premium":
                    params["premium"] = "true"
                rs = requests.get("https://api.scraperapi.com", params=params, timeout=100)
                if not rs.ok or len(rs.text) < 500:
                    continue
                soup_s = BeautifulSoup(rs.text, "html.parser")
                meta_s = meta_offer(soup_s)
                if jsonld_products(soup_s) or meta_s:
                    return {
                        "soup": soup_s,
                        "text": soup_s.get_text(" ", strip=True),
                        "source": "scraper+" + mode,
                        "price_hint": None,
                        "offer_hint": meta_s,
                    }
            except requests.RequestException:
                continue

    for attempt in range(2):
        if not allow_text:
            break
        try:
            r = requests.get("https://r.jina.ai/" + url, timeout=60)
            r.raise_for_status()
            text = r.text
            return {
                "soup": BeautifulSoup(text, "html.parser"),
                "text": text,
                "source": "texte",
                "price_hint": None,
                "offer_hint": None,
            }
        except requests.RequestException as e:
            errors.append(f"texte: {type(e).__name__}: {str(e)[:80]}")
            if attempt == 0:
                time.sleep(3)
    raise RuntimeError("; ".join(errors))


def meta_offer(soup: BeautifulSoup) -> dict | None:
    """Prix/dispo dans les balises meta (utile quand il n'y a pas de JSON-LD)."""
    def content(*names):
        for name in names:
            tag = soup.find("meta", attrs={"property": name}) or soup.find("meta", attrs={"name": name})
            if tag and tag.get("content"):
                return tag["content"]
        return None

    price = to_float(content("product:price:amount", "og:price:amount", "product:sale_price:amount"))
    if price is None:
        return None
    availability = content("product:availability", "og:availability") or ""
    if not availability:
        m = re.search(
            r"schema\.org/(InStock|OutOfStock|PreOrder|BackOrder|SoldOut)", str(soup)
        )
        if m:
            availability = m.group(1)
    return {
        "price": price,
        "priceCurrency": content("product:price:currency", "og:price:currency") or "EUR",
        "availability": availability,
    }


STOCK_LABELS = (
    ("instock", "en stock"),
    ("in_stock", "en stock"),
    ("limitedavailability", "stock limité"),
    ("onlineonly", "en stock"),
    ("preorder", "précommande"),
    ("backorder", "précommande"),
    ("outofstock", "rupture"),
    ("out_of_stock", "rupture"),
    ("soldout", "rupture"),
)


def stock_state(raw) -> str:
    key = str(raw or "").lower()
    for needle, label in STOCK_LABELS:
        if needle in key:
            return label
    return "inconnue"


def text_price(text: str, cfg: dict) -> float | None:
    """Cherche le prix autour du nom produit dans une page en texte."""
    hint = cfg.get("text_match")
    regions = [text]
    if hint:
        regions = [
            text[m.start() : m.start() + 900]
            for m in re.finditer(re.escape(str(hint)), text, re.I)
        ] or [text]
    patterns = [
        # Prix soldé après un pourcentage de remise ("154,99 €* -23% 118,99 €").
        r"-\s*\d+\s*%[^0-9]{0,20}(\d{1,4}[.,]\d{2})\s*€",
        r"Sale price\s*€\s*(\d{1,4}[.,]\d{2})",
        r"€\s*(\d{1,4})[\s.,](\d{2})",
        r"(\d{1,4}[.,]\d{2})\s*EUR",
        r"(\d{1,4}[.,]\d{2})\s*€",
        r"€\s*(\d{1,4}[.,]\d{2})",
    ]
    for region in regions:
        for pattern in patterns:
            m = re.search(pattern, region, re.I)
            if m:
                if len(m.groups()) == 2:
                    return float(f"{m.group(1)}.{m.group(2)}")
                return to_float(m.group(1))
    return None


STOP_CODES = {
    "COUPON", "PROMO", "POSTAL", "LIVRAISON", "GRATUIT", "GRATUITE",
    "CADEAU", "CODE", "NOEL", "SOLDES",
}


def promo_from_text(text: str) -> list[str]:
    codes: list[str] = []
    patterns = [
        r"(?:discount\s+)?code\s*:?\s*([A-Z0-9]{4,16})",
        r"with\s+code\s*:?\s*([A-Z0-9]{4,16})",
        r"kortingscode\s*:?\s*([A-Z0-9]{4,16})",
        r"code\s+promo\s*:?\s*([A-Z0-9]{4,16})",
    ]
    for pattern in patterns:
        for m in re.finditer(pattern, text[:20000], re.I):
            code = m.group(1).upper()
            if code not in STOP_CODES and code not in codes:
                codes.append(code)
    return [f"code {c}" for c in codes[:5]]


def jsonld_products(soup: BeautifulSoup) -> list[dict]:
    found: list[dict] = []
    for tag in soup.find_all("script", type=re.compile(r"ld\+json", re.I)):
        raw = tag.string or tag.get_text() or ""
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue

        def walk(node):
            if isinstance(node, list):
                for item in node:
                    walk(item)
                return
            if not isinstance(node, dict):
                return
            if "Product" in str(node.get("@type", "")):
                found.append(node)
            for key, value in node.items():
                if key.startswith("@"):
                    continue
                if isinstance(value, (dict, list)):
                    walk(value)

        walk(data)
    return found


def offers_of(product: dict) -> list[dict]:
    offers = product.get("offers") or []
    return offers if isinstance(offers, list) else [offers]


def pick_offer(products: list[dict], cfg: dict, url: str) -> tuple[dict | None, dict | None, bool]:
    """Return (product, offer, variant_fallback)."""
    candidates = []
    for p in products:
        for o in offers_of(p):
            price = to_float(o.get("price"))
            if price is not None:
                candidates.append((p, o))

    if not candidates:
        return None, None, False

    sku = str(cfg.get("variant_sku", "")) if cfg.get("variant_sku") else None
    match = cfg.get("variant_match")

    def keep(p: dict, o: dict) -> bool:
        hay = json.dumps({"name": p.get("name"), "offer": o}, ensure_ascii=False)
        if sku and sku not in hay:
            return False
        if match and str(match).lower() not in hay.lower():
            return False
        return True

    selected = [(p, o) for p, o in candidates if keep(p, o)]
    fallback = not selected
    if fallback:
        selected = candidates

    # Priorise l'offre dont l'URL correspond a la page surveillee.
    base = url.split("#")[0]
    exact = [(p, o) for p, o in selected if base.split("?")[0] in str(o.get("url", ""))]
    if exact:
        selected = exact

    p, o = selected[0]
    return p, o, fallback


def scraper_offer(nodes: list[dict], cfg: dict) -> tuple[dict | None, dict | None]:
    """Choisit l'offre la moins chère parmi les variantes en stock (pages rendues)."""
    candidates = []
    for node in nodes:
        for offer in offers_of(node):
            price = to_float(offer.get("price"))
            if price is not None:
                candidates.append((node, offer, price))
    if not candidates:
        return None, None
    match = cfg.get("variant_match")
    if match:
        selected = [
            c for c in candidates
            if str(match).lower() in json.dumps(
                {"name": c[0].get("name"), "offer": c[1]}, ensure_ascii=False
            ).lower()
        ]
        if selected:
            candidates = selected
    in_stock = [
        c for c in candidates
        if "instock" in str(c[1].get("availability", "")).lower()
    ]
    pool = in_stock or candidates
    node, offer, _ = min(pool, key=lambda c: c[2])
    return node, offer


def promo_hints(soup: BeautifulSoup, offer: dict, product: dict) -> list[str]:
    hints: list[str] = []
    specs = offer.get("priceSpecification") or []
    if isinstance(specs, dict):
        specs = [specs]
    for spec in specs:
        if "Strikethrough" in str(spec.get("priceType", "")):
            old = to_float(spec.get("price"))
            if old:
                hints.append(f"prix barré {money(old)}")
    text = soup.get_text(" ", strip=True)[:20000]
    m = re.search(
        r"(code\s+promo|coupon|promo\s*code|kortingscode)\D{0,30}([A-Z0-9]{4,16})",
        text,
        re.I,
    )
    if m and m.group(2).upper() not in STOP_CODES:
        hints.append(f"code? {m.group(2)}")
    return sorted(set(hints))


def telegram(text: str) -> None:
    # Bot dédié au suivi prix. Le secret TELEGRAM_BOT_TOKEN du repo pointe sur
    # @FolioPrixBot ; PRICE_* permet de forcer un autre bot si besoin.
    token = os.environ.get("PRICE_TELEGRAM_BOT_TOKEN") or os.environ.get("TELEGRAM_BOT_TOKEN")
    chat = os.environ.get("PRICE_TELEGRAM_CHAT_ID") or os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        log("Telegram non configure (PRICE_TELEGRAM_BOT_TOKEN / PRICE_TELEGRAM_CHAT_ID absents)")
        return
    for chunk in [text[i : i + 3500] for i in range(0, len(text), 3500)]:
        try:
            r = requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                data={
                    "chat_id": chat,
                    "text": chunk,
                    "disable_web_page_preview": "true",
                },
                timeout=20,
            )
            if not r.ok:
                log(f"Telegram erreur {r.status_code}: {r.text[:200]}")
        except requests.RequestException as e:
            log(f"Telegram indisponible: {e}")


def check_once() -> int:
    load_credentials()
    cfg = load_config()
    history = load_history()
    settings = cfg.get("settings", {})
    report_mode = os.environ.get("REPORT_MODE", settings.get("report_mode", "changes"))
    digest_hour = int(os.environ.get("DIGEST_HOUR_UTC", settings.get("digest_hour_utc", 8)))
    now = datetime.now(timezone.utc)
    stamp = now.isoformat(timespec="seconds")
    today = now.date().isoformat()

    events: list[str] = []
    digest_rows: list[str] = []
    alerts = 0

    for product in cfg["products"]:
        pid = product["id"]
        name = product["name"]
        url = product["url"]
        record = history["products"].setdefault(
            pid, {"name": name, "url": url, "points": [], "lowest": None, "lowest_at": None}
        )
        record["name"] = name
        record["url"] = url

        scraper_key = os.environ.get("SCRAPER_API_KEY")
        throttled = False
        if product.get("manual"):
            if not product.get("scraper") or not scraper_key:
                last = record["points"][-1] if record["points"] else None
                if last and last.get("price") is not None:
                    digest_rows.append(f"• {name} : {money(last['price'])} (manuel)")
                log(f"{pid}: saisie manuelle, pas de lecture automatique")
                continue
            trial_until = settings.get("scraper_trial_until")
            if trial_until and today >= str(trial_until):
                # Après l'essai ScraperAPI : ~1 000 crédits/mois => 1 passe / 8 jours.
                min_hours = float(settings.get("scraper_after_hours", 192))
            else:
                min_hours = float(product.get("scraper_min_hours", settings.get("scraper_min_hours", 12)))
            last_auto = record.get("last_auto_at")
            if last_auto:
                try:
                    age = (now - datetime.fromisoformat(last_auto)).total_seconds() / 3600
                except ValueError:
                    age = min_hours + 1
                if age < min_hours:
                    throttled = True
            if throttled:
                last = record["points"][-1] if record["points"] else None
                if last and last.get("price") is not None:
                    digest_rows.append(f"• {name} : {money(last['price'])} (auto 2×/j)")
                log(f"{pid}: quota scraping atteint (prochaine lecture dans {min_hours:.0f} h)")
                continue
            record["last_auto_at"] = stamp

        try:
            page = fetch_page(url, product, allow_text=not product.get("scraper"))
            soup, text = page["soup"], page["text"]
            nodes = jsonld_products(soup)
            if str(page.get("source", "")).startswith("scraper"):
                pnode, offer = scraper_offer(nodes, product)
                fallback = False
            else:
                pnode, offer, fallback = pick_offer(nodes, product, url)
            if offer and to_float(offer.get("price")) is not None:
                price = to_float(offer.get("price"))
                currency = offer.get("priceCurrency", "EUR")
                promos = promo_hints(soup, offer, pnode or {})
                availability = stock_state(offer.get("availability"))
            elif page.get("offer_hint"):
                hinted = page["offer_hint"]
                price = to_float(hinted.get("price"))
                currency = hinted.get("priceCurrency", "EUR")
                promos = promo_from_text(text)
                availability = stock_state(hinted.get("availability"))
            else:
                price = page.get("price_hint") or text_price(text, product)
                if price is None:
                    # Diagnostic ciblé : la page diffère souvent selon la sortie (IP Actions).
                    log(
                        f"{pid}: DIAG source={page.get('source')} " +
                        f"jsonld={len(jsonld_products(page['soup']))} " +
                        f"meta={meta_offer(page['soup'])} " +
                        f"textlen={len(text)} extrait={text[:180]!r}"
                    )
                    raise ValueError("aucun prix trouvé (JSON-LD, meta et texte)")
                currency = "EUR"
                promos = promo_from_text(text)
                # Pas d'inférence de stock depuis le texte : une page contient
                # souvent "épuisé"/"en stock" pour d'autres variantes.
                availability = "inconnue"
            previous = record["points"][-1] if record["points"] else None
            prev_price = previous.get("price") if previous else None
            prev_avail = previous.get("availability") if previous else None
            prev_stock = stock_state(prev_avail) if prev_avail else "inconnue"
            low = record.get("lowest")

            record["points"].append(
                {
                    "t": stamp,
                    "price": price,
                    "currency": currency,
                    "availability": availability,
                    "stock": availability,
                    "promo": promos,
                    "source": page["source"],
                }
            )
            record["points"] = record["points"][-MAX_POINTS:]

            is_new_low = low is None or price < low
            if is_new_low:
                record["lowest"] = price
                record["lowest_at"] = stamp

            if is_new_low and prev_price is not None:
                events.append(f"📉 NOUVEAU PLUS BAS — {name} : {money(price)} (préc. {money(prev_price)}) — {url}")
                alerts += 1
            elif prev_price is not None and abs(price - prev_price) > 0.001:
                events.append(f"💰 {name} : {money(prev_price)} → {money(price)} — {url}")
                alerts += 1
            if (
                prev_stock != "inconnue"
                and availability != "inconnue"
                and prev_stock != availability
            ):
                if availability == "en stock" and prev_stock in ("rupture", "précommande"):
                    events.append(f"✅ {name} : de nouveau disponible ({prev_stock} → en stock) — {url}")
                elif availability == "rupture":
                    events.append(f"⛔ {name} : en rupture de stock — {url}")
                else:
                    events.append(f"📦 {name} : {prev_stock} → {availability} — {url}")
                alerts += 1
            if promos and (not previous or promos != previous.get("promo", [])):
                events.append(f"🏷️ {name} : {', '.join(promos)} — {url}")
                alerts += 1

            delta = ""
            if low is not None and price > low:
                delta = f" | +{price - low:.2f} vs bas"
            stock_note = f" ({availability})" if availability != "inconnue" else ""
            digest_rows.append(f"• {name} : {money(price)}{stock_note}{delta} | bas {money(low)}")
            log(f"{pid}: {money(price)}")
        except Exception as e:  # noqa: BLE001 - un site ne doit pas bloquer les autres
            err = f"{type(e).__name__}: {str(e)[:120]}"
            last_error = record["points"][-1].get("error") if record["points"] else None
            if err != last_error:
                events.append(f"❌ {name} : lecture impossible ({err}) — {url}")
                alerts += 1
            record["points"].append({"t": stamp, "price": None, "error": err})
            record["points"] = record["points"][-MAX_POINTS:]
            digest_rows.append(f"• {name} : indisponible ({err[:60]})")
            log(f"{pid}: ERREUR {err}")

    do_digest = now.hour >= digest_hour and history.get("last_digest") != today
    if do_digest:
        history["last_digest"] = today

    messages: list[str] = []
    if events:
        messages.append("🔔 *Veille prix* — " + stamp + "\n\n" + "\n\n".join(events))
    if report_mode == "always" or do_digest:
        title = "📊 Rapport quotidien" if do_digest else "📊 Rapport horaire"
        messages.append(title + " — " + stamp + "\n\n" + "\n".join(digest_rows))
    if not messages and report_mode == "changes":
        log("Aucun changement, aucun message envoye")

    save_history(history)
    for message in messages:
        telegram(message.replace("*", ""))

    log(f"Termine : {len(cfg['products'])} produits, {alerts} evenements")
    return 0


def main() -> int:
    """3 vérifications par run horaire => un contrôle toutes les 20 minutes."""
    rounds = int(os.environ.get("WATCH_ROUNDS", "3"))
    interval = int(os.environ.get("WATCH_INTERVAL_MIN", "20"))
    codes = []
    for i in range(rounds):
        if i:
            time.sleep(interval * 60)
        codes.append(check_once())
    return max(codes) if codes else 0


if __name__ == "__main__":
    sys.exit(main())
