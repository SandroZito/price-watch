# Design — Liste de produits éditable (ajout / retrait)

## Intention

L'utilisateur veut voir **la liste des liens qu'il a ajoutés** et pouvoir **ajouter ou retirer un produit** sans toucher à un fichier JSON. Cette liste est la source de vérité : elle pilote la vérification des prix et ce que le bot Telegram `@FolioPrixBot` rapporte.

 Approche retenue (choix de l'utilisateur) : **page web locale**.

## Objectifs

- Afficher les 16 produits avec leur nom, Lien cliquable, dernier prix connu, plus bas historique.
- Ajouter un produit en collant une URL (+ nom optionnel + variante optionnelle).
- Retirer un produit en un clic.
- Toute modification met à jour `products.json`, le pousse sur GitHub et déclenche une vérification immédiate.
- Aucun secret (token Telegram) dans le navigateur.

## Non-objectifs

- Pas de récepteur de commandes Telegram (le bot reste émetteur uniquement).
- Pas de suppression de l'historique d'un produit retiré (conservé, sans risque).
- Pas de file d'attente hors-ligne : en cas d'échec du push, le changement reste local avec un indicateur et un bouton « Synchroniser ».
- Pas de modification des workflows GitHub (le credential local n'a que le scope `repo`).

## Contraintes

- Le repo peut être dans un dossier protégé : un `launchd` n'hérite pas des permissions du Terminal. Le serveur est donc **lancé par l'utilisateur**, qui les hérite.
- Git credential helper `osxkeychain` fonctionne pour push (scope `repo`) et pour le `workflow_dispatch` via l'API.
- `api.telegram.org` est bloqué depuis le réseau local : toute vérification Telegram passe par GitHub Actions.
- Le tracking reste dans le cloud (GitHub Actions) ; le serveur local ne tourne que pendant l'édition.

## Approches envisagées

1. **Page web locale + micro-serveur** (retenue) — autonomie complète, aucun secret dans le navigateur, mais nécessite de lancer un script quand on veut modifier.
2. **Liste Markdown + instruction à l'agent** — zéro infrastructure, mais dépendance à l'agent pour chaque changement.
3. **Commandes Telegram** — naturel pour l'utilisateur, mais exige un récepteur cloud permanent et ajoute de la fragilité (le bot ne peut pas écouter depuis un Mac éteint).

## Design

### Fichiers

- `liste.html` — page unique, table des produits, champ d'ajout, boutons ✕, bouton « Synchroniser ».
- `liste_server.py` — micro-serveur **stdlib uniquement** (`http.server`), lié à `127.0.0.1` :
  - `GET /` → sert `liste.html`
  - `GET /api/liste` → `products.json` + dernier prix / plus bas lus dans `history.json`
  - `POST /api/ajouter` → `{url, nom?, variante?}`
  - `POST /api/retirer` → `{id}`
  - `POST /api/sync` → rejoue le push
- `liste.command` — lanceur macOS double-cliquable : démarre le serveur et ouvre le navigateur sur `http://127.0.0.1:8766`.

### Ajout

Le serveur :
1. valide l'URL (http/https) ;
2. génère un `id` stable (slug du domaine + suffixe court) ;
3. ajoute l'entrée à `products.json` avec `name`, `url`, et `variant_sku` ou `variant_match` si le champ variante est rempli ;
4. écrit le JSON en UTF-8 indenté ;
5. `git add` + `commit` + `push` ;
6. `POST workflow_dispatch` (meilleur effort) pour une vérification immédiate ;
7. renvoie le nouvel état à la page.

Si le produit a plusieurs variantes et qu'aucune n'est précisée, `watch.py` retient la première offre ; la page affiche le nom du produit tel que détecté au dernier passage, ce qui permet de corriger la variante via le champ dédié si besoin.

### Retrait

Retire l'entrée de `products.json`, puis mêmes étapes 4-7. L'historique reste intact.

### Synchronisation

En cas d'échec du push (réseau, credentials), le fichier local est conservé, la page affiche « ⚠️ non synchronisé » et le bouton « Synchroniser » relance l'étape 5-6.

### Rendu de la page (esquisse)

```
📋 Liste des produits suivis                      16 produits · synchronisé

Produit                                   Dernier prix   Plus bas    
Leen Bakker — Table Irene noyer  ↗         389,40 €       389,40 €   ✕
LUMZ — Tabouret Kare Dollar      ↗         125,00 €       125,00 €   ✕
Maisons du Monde — Étagère ...   ↗         —              —          ✕
...

[coller une URL]  [Nom (optionnel)]  [Variante (optionnelle)]  [+ Ajouter]
```

## Vérification

- Lancer `liste.command`, ajouter une URL factice, vérifier `products.json`, le commit GitHub et l'alerte Telegram de variante si besoin.
- Retirer le produit de test, vérifier qu'il disparaît de la liste et du prochain rapport.
- Couper le serveur : le suivi cloud continue normalement.
