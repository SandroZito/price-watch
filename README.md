# Veille prix → Telegram

Surveille 16 products toutes les heures, détecte les baisses, les promos et les retours en stock, et envoie un rapport sur Telegram via **@FolioPrixBot** (bot dédié aux prix).

## Principe

- `products.json` : la liste des produits (URL + éventuellement `variant_sku` / `variant_match` pour choisir la bonne variante).
- `watch.py` : lit le **JSON-LD** de chaque page (la source la plus fiable), compare à l'historique, envoie les alertes Telegram.
- `history.json` : prix + promos + disponibilité par passage (généré automatiquement, max 240 points par produit).
- `.github/workflows/hourly.yml` : exécution **dans le cloud** toutes les heures, même quand ton Mac est éteint.
- `watch.py` : chaque run effectue **3 vérifications espacées de 20 minutes** (`WATCH_ROUNDS` / `WATCH_INTERVAL_MIN`), soit un contrôle des prix toutes les **20 minutes**. Le cron reste horaire car le credential local n'a pas le scope `workflow` pour modifier `hourly.yml`.

GitHub Actions est gratuit pour ce volume (≈ 360 min/mois, bien sous les 2000 min du palier gratuit ; illimité pour un repo public).

## Mise en route (5 min)

1. Crée un repo GitHub (public ou privé) et pousse ce dossier :
   ```bash
   cd price-watch
   git init
   git add .
   git commit -m "init price watch"
   git remote add origin https://github.com/<TON-COMPTE>/<TON-REPO>.git
   git push -u origin main
   ```

2. Récupère les identifiants Telegram :
   - Parle à **@BotFather** → `/newbot` → il te donne un **token** (le bot Prix est déjà créé : @FolioPrixBot).
   - Récupère le **chat ID** : envoie un message au bot, puis ouvre `https://api.telegram.org/bot<TOKEN>/getUpdates` et lis `"chat":{"id": ...}`.

3. Dans le repo GitHub → **Settings → Secrets and variables → Actions → New repository secret** :
   - `TELEGRAM_BOT_TOKEN` = le token
   - `TELEGRAM_CHAT_ID` = le chat ID

4. Onglet **Actions** → **Run workflow** pour tester. Tu dois recevoir un message. Ensuite laisse le cron tourner.

## Réglages

Dans `products.json` → `settings` :
- `report_mode` : `changes` (défaut, message seulement si quelque chose bouge) ou `always` (rapport à chaque passage).
- `digest_hour_utc` : heure UTC du rapport complet quotidien (défaut `8`, donc 10 h en Belgique en été).

Variables d'environnement possibles dans le workflow : `REPORT_MODE`, `DIGEST_HOUR_UTC`, `TELEGRAM_BOT_TOKEN` (= @FolioPrixBot), `TELEGRAM_CHAT_ID`, ou `PRICE_TELEGRAM_*`.

## Ajouter un produit

Ajoute une entrée dans `products.json` avec `id`, `name`, `url`. Si la page contient plusieurs variantes, ajoute :
- `variant_sku` : SKU / identifiant de variante présent dans le JSON-LD, ou
- `variant_match` : morceau de texte du nom de la variante.

Le script journalise `variante non trouvée, prix générique utilisé` s'il doit se replier sur le premier prix.

## Résultat du test local (20/11/2026)

**15 produits sur 16** ont été lus correctement :

| Produit | Prix relevé |
|---|---|
| Leen Bakker — Irene noyer | 649,00 € |
| LUMZ — Kare Dollar | 125,00 € |
| sweeek — Lennox bordeaux | 169,99 € |
| RÅM — LURA_5 | 327,95 € |
| FITUEYES — Picasso | 218,00 € |
| home24 — Hossa lot 2 | 118,99 € (barré 154,99 €) |
| Jotex — Irma | 47,30 € (barré 56,99 €) |
| vidaXL — Pouf noir | 151,99 € (barré 185,99 €) |
| JYSK — Marstrand | 300,00 € |
| SKLUM — Montana | 649,95 € |
| The Masie — Beca | 829,95 € |
| SKLUM — Alicia | 40,95 € |
| Emma Original 160x200 | 880,05 € |
| Emma Original+ 160x200 | 1 556,00 € |
| JYSK — Ellinge 160x200 | 200,00 € |

Codes promo détectés au passage : `BF15`, `FITUEYES05` (FITUEYES), `MIX30` (Jotex).

## Limitations honnêtes

- **Maisons du Monde** bloque tout accès automatisé (captcha), y compris Chrome headless et le lecteur de texte. Le script isole l'erreur et continue pour les autres ; tu recevras une alerte `❌` si ce prix reste illisible. Solution : retirer ce produit, ou surveiller son prix via une extension navigateur.
- La stratégie est : JSON-LD direct → lecteur de texte public (`r.jina.ai`) en repli. Cela contourne la plupart des 403/429.
- Les **codes promo** ne sont pas toujours dans le HTML : la détection couvre les prix barrés et les motifs `code / coupon / kortingscode`. Pour une couverture complète, une extension type Honey reste imbattable.
- Les prix Emma sont ceux des matelas individuels (SKU de la sélection), pas forcément le total du pack affiché sur la page.

## Test local

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
python watch.py
```
