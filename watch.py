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
from datetime import datetime, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

BASE = Path(__file__).resolve().parent
PRODUCTS_FILE = BASE / "products.json"
HISTORY_FILE = BASE / "history.json"
CREDENTIALS_FILE = Path.home() / ".config/price-watch/telegram.env"
MAX_POINTS = 240
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
    """Charge les identifiants Telegram depuis un fichier local en dehors du repo."""
    if not CREDENTIALS_FILE.exists():
        return
    for line in CREDENTIALS_FILE.read_text(encoding="utf-8").splitlines():
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


def save_history(history: dict) -> None:
    with HISTORY_FILE.open("w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)
        f.write("\n")


def fetch_page(url: str) -> dict:
    """Retourne {soup, text, source}. Repli sur un lecteur de texte si le site bloque."""
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
        if jsonld_products(soup):
            return {"soup": soup, "text": soup.get_text(" ", strip=True), "source": "direct"}
    except requests.RequestException:
        pass

    # Repli : lecteur de texte public (contourne 403/429 sans captcha).
    r = requests.get("https://r.jina.ai/" + url, timeout=60)
    r.raise_for_status()
    text = r.text
    return {"soup": BeautifulSoup(text, "html.parser"), "text": text, "source": "texte"}


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
            if code not in ("COUPON", "PROMO") and code not in codes:
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
    if m:
        hints.append(f"code? {m.group(2)}")
    return sorted(set(hints))


def telegram(text: str) -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        log("Telegram non configure (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID absents)")
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


def main() -> int:
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

        try:
            page = fetch_page(url)
            soup, text = page["soup"], page["text"]
            pnode, offer, fallback = pick_offer(jsonld_products(soup), product, url)
            if offer and to_float(offer.get("price")) is not None:
                price = to_float(offer.get("price"))
                currency = offer.get("priceCurrency", "EUR")
                promos = promo_hints(soup, offer, pnode or {})
                availability = str(offer.get("availability", "")).split("/")[-1]
            else:
                price = text_price(text, product)
                if price is None:
                    raise ValueError("aucun prix trouvé (JSON-LD et texte)")
                currency = "EUR"
                promos = promo_from_text(text)
                availability = "inconnue"
            previous = record["points"][-1] if record["points"] else None
            prev_price = previous.get("price") if previous else None
            prev_avail = previous.get("availability") if previous else None
            low = record.get("lowest")

            record["points"].append(
                {
                    "t": stamp,
                    "price": price,
                    "currency": currency,
                    "availability": availability,
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
            if prev_avail and prev_avail != availability:
                events.append(f"📦 {name} : disponibilité {prev_avail} → {availability}")
                alerts += 1
            if promos and (not previous or promos != previous.get("promo", [])):
                events.append(f"🏷️ {name} : {', '.join(promos)} — {url}")
                alerts += 1

            delta = ""
            if low is not None and price > low:
                delta = f" | +{price - low:.2f} vs bas"
            digest_rows.append(f"• {name} : {money(price)}{delta} | bas {money(low)}")
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


if __name__ == "__main__":
    sys.exit(main())
