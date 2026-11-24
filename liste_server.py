#!/usr/bin/env python3
"""Micro-serveur pour gérer la liste des produits suivis.

Écoute uniquement sur 127.0.0.1 : aucun secret, aucun accès externe.
Dépendances : stdlib uniquement.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import urllib.parse
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

BASE = Path(__file__).resolve().parent
PRODUCTS = BASE / "products.json"
HISTORY = BASE / "history.json"
HTML = BASE / "liste.html"
PORT = int(os.environ.get("LISTE_PORT", "8766"))


def log(msg: str) -> None:
    print(msg, flush=True)


def read_json(path: Path, default: dict) -> dict:
    try:
        with path.open(encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return dict(default)


def write_products(cfg: dict) -> None:
    with PRODUCTS.open("w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
        f.write("\n")


def stock_label(raw) -> str:
    key = str(raw or "").lower()
    for needle, label in (
        ("instock", "en stock"), ("in_store", "en stock"), ("limited", "stock limité"),
        ("preorder", "précommande"), ("backorder", "précommande"),
        ("outofstock", "rupture"), ("soldout", "rupture"), ("rupture", "rupture"),
        ("en stock", "en stock"), ("précommande", "précommande"),
    ):
        if needle in key:
            return label
    return "inconnue"


def auto_label(product: dict, settings: dict) -> str:
    trial_until = settings.get("scraper_trial_until")
    today = datetime.now(timezone.utc).date().isoformat()
    if trial_until and today >= str(trial_until):
        return "1×/8j"
    return "2×/j" if float(product.get("scraper_min_hours", 12)) <= 12 else "1×/j"


def rows() -> list[dict]:
    cfg = read_json(PRODUCTS, {"settings": {}, "products": []})
    hist = read_json(HISTORY, {"products": {}})
    out = []
    for p in cfg.get("products", []):
        rec = hist.get("products", {}).get(p["id"], {})
        pts = [x for x in rec.get("points", []) if x.get("price") is not None]
        promos = sorted({c for x in rec.get("points", []) for c in x.get("promo", [])})
        series = [
            {"t": d["d"] + "T12:00:00Z", "p": d["avg"], "lo": d.get("min"), "hi": d.get("max")}
            for d in rec.get("daily", []) if d.get("avg") is not None
        ]
        series += [
            {"t": x.get("t", ""), "p": x["price"]}
            for x in rec.get("points", []) if x.get("price") is not None
        ]
        series.sort(key=lambda s: s["t"])
        out.append({
            "id": p["id"],
            "name": p["name"],
            "url": p["url"],
            "last": pts[-1].get("price") if pts else None,
            "low": rec.get("lowest"),
            "high": rec.get("highest") or max((x["price"] for x in pts), default=None),
            "promos": promos,
            "series": series,
            "variant": p.get("variant_sku") or p.get("variant_match") or "",
            "detected": rec.get("name", p["name"]),
            "source": pts[-1].get("source", "") if pts else "",
            "manual": bool(p.get("manual")),
            "auto": bool(p.get("scraper")),
            "auto_label": auto_label(p, cfg.get("settings", {})),
            "manual_at": (pts[-1].get("t", "")[:10] if p.get("manual") and pts else ""),
            "stock": (pts[-1].get("stock") or stock_label(pts[-1].get("availability"))) if pts else "inconnue",
        })
    return out


def make_id(url: str) -> str:
    host = urllib.parse.urlparse(url).netloc.replace("www.", "").split(".")[0].lower()
    host = re.sub(r"[^a-z0-9]", "-", host).strip("-") or "produit"
    cfg = read_json(PRODUCTS, {"products": []})
    ids = {p["id"] for p in cfg.get("products", [])}
    candidate = host
    i = 2
    while candidate in ids:
        candidate = f"{host}-{i}"
        i += 1
    return candidate


def git(*args: str) -> tuple[int, str]:
    try:
        r = subprocess.run(
            ["git", *args], cwd=BASE, capture_output=True, text=True,
            timeout=60, env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        )
        return r.returncode, (r.stdout + r.stderr).strip()
    except Exception as e:  # noqa: BLE001
        return 1, str(e)[:200]


def sync(message: str, paths: tuple[str, ...] = ("products.json",)) -> str:
    """Commit + push + déclenche une vérification immédiate."""
    steps: list[str] = []
    code, out = git("add", *paths)
    if code:
        return f"Erreur git add : {out[:120]}"
    code, out = git("diff", "--cached", "--quiet")
    if code == 0:
        # rien à committer, on tente quand même le dispatch
        pass
    else:
        code, out = git("commit", "-q", "-m", message)
        if code:
            return f"Erreur commit : {out[:120]}"
        steps.append("commité")
    code, out = git("push", "-q", "origin", "main")
    if code:
        return "⚠️ Enregistré localement, push impossible — clique sur Synchroniser"
    steps.append("poussé sur GitHub")
    # Vérification immédiate (meilleur effort).
    try:
        import requests  # présent si l'utilisateur a installé les déps du projet
        pat_script = Path.home() / ".config/price-watch/gh_dispatch.py"
        if pat_script.exists():
            subprocess.run([sys.executable, str(pat_script)], cwd=BASE, timeout=30)
            steps.append("vérification lancée")
    except Exception:  # noqa: BLE001
        pass
    return "✓ " + ", ".join(steps)


def add_product(body: dict) -> dict:
    url = str(body.get("url", "")).strip()
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return {"rows": rows(), "message": "❌ URL invalide"}
    cfg = read_json(PRODUCTS, {"settings": {}, "products": []})
    pid = make_id(url)
    name = str(body.get("name", "")).strip() or parsed.netloc.replace("www.", "")
    variant = str(body.get("variant", "")).strip()
    entry = {"id": pid, "name": name, "url": url}
    if variant:
        if variant.isdigit() or re.fullmatch(r"[A-Z]{2,}[0-9A-Z]+", variant):
            entry["variant_sku"] = variant
        else:
            entry["variant_match"] = variant
    cfg.setdefault("products", []).append(entry)
    write_products(cfg)
    msg = sync(f"liste: ajout {pid}")
    return {"rows": rows(), "message": f"Ajouté : {name} · {msg}"}


def set_manual_price(body: dict) -> dict:
    """Enregistre un prix saisi à la main (utile pour les sites protégés)."""
    pid = str(body.get("id", ""))
    try:
        price = round(float(str(body.get("price", "")).replace(",", ".")), 2)
    except (TypeError, ValueError):
        return {"rows": rows(), "message": "❌ Prix invalide"}
    if price <= 0:
        return {"rows": rows(), "message": "❌ Prix invalide"}
    hist = read_json(HISTORY, {"products": {}})
    cfg = read_json(PRODUCTS, {"products": []})
    name = next((p["name"] for p in cfg.get("products", []) if p["id"] == pid), pid)
    record = hist.setdefault("products", {}).setdefault(
        pid, {"name": name, "url": "", "points": [], "lowest": None, "lowest_at": None}
    )
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    record.setdefault("points", []).append({
        "t": stamp, "price": price, "currency": "EUR",
        "availability": "inconnue", "promo": [], "source": "manuel",
    })
    record["points"] = record["points"][-240:]
    record["lowest"] = price if record.get("lowest") is None else min(record["lowest"], price)
    if record.get("lowest") == price:
        record["lowest_at"] = stamp
    record["highest"] = max(record.get("highest") or 0, price)
    with HISTORY.open("w", encoding="utf-8") as f:
        json.dump(hist, f, ensure_ascii=False, indent=2)
        f.write("\n")
    msg = sync(f"prix manuel: {pid} = {price:.2f}", ("history.json",))
    return {"rows": rows(), "message": f"Prix {price:.2f} € enregistré · {msg}"}


def remove_product(body: dict) -> dict:
    pid = str(body.get("id", ""))
    cfg = read_json(PRODUCTS, {"settings": {}, "products": []})
    before = len(cfg.get("products", []))
    cfg["products"] = [p for p in cfg.get("products", []) if p["id"] != pid]
    if len(cfg["products"]) == before:
        return {"rows": rows(), "message": "❌ Produit introuvable"}
    write_products(cfg)
    msg = sync(f"liste: retrait {pid}")
    return {"rows": rows(), "message": f"Retiré · {msg}"}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:
        log("  " + (fmt % args))

    def send_json(self, obj: dict, code: int = 200) -> None:
        data = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def send_html(self) -> None:
        data = HTML.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def body(self) -> dict:
        n = int(self.headers.get("Content-Length", "0"))
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n))
        except json.JSONDecodeError:
            return {}

    def do_GET(self) -> None:
        if self.path in ("/", "/liste.html"):
            self.send_html()
        elif self.path == "/api/liste":
            # "synced" = pas de changements locaux non commités
            code, out = git("status", "--porcelain")
            synced = not any(line.startswith(("M ", "A ", "D ")) for line in out.splitlines())
            self.send_json({"rows": rows(), "synced": synced})
        else:
            self.send_error(404)

    def do_POST(self) -> None:
        body = self.body()
        if self.path == "/api/ajouter":
            self.send_json(add_product(body))
        elif self.path == "/api/retirer":
            self.send_json(remove_product(body))
        elif self.path == "/api/prix-manuel":
            self.send_json(set_manual_price(body))
        elif self.path == "/api/sync":
            self.send_json({"message": sync("liste: synchronisation")})
        else:
            self.send_error(404)


def main() -> None:
    log(f"Liste des produits → http://127.0.0.1:{PORT}/liste.html")
    log("Ctrl+C pour arrêter (le suivi cloud continue).")
    try:
        ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
    except KeyboardInterrupt:
        log("Arrêté.")


if __name__ == "__main__":
    main()
