#!/usr/bin/env python3
"""Crée le repo GitHub, pousse le code et configure les secrets Telegram.

Usage:
    GITHUB_PAT=ghp_xxx python setup_github.py [nom-du-repo]

Le token GitHub n'est jamais affiché. Les secrets sont chiffrés avec la clé
publique du repo (libsodium sealed box), comme l'exige l'API GitHub.
"""

import base64
import os
import subprocess
import sys
from pathlib import Path

import requests
from nacl.public import PublicKey, SealedBox

BASE = Path(__file__).resolve().parent
API = "https://api.github.com"


def main() -> int:
    pat = os.environ.get("GITHUB_PAT")
    if not pat:
        print("Erreur: définir GITHUB_PAT (Personal Access Token avec droit 'repo').")
        return 1
    repo = sys.argv[1] if len(sys.argv) > 1 else "price-watch"
    headers = {
        "Authorization": f"Bearer {pat}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    me = requests.get(f"{API}/user", headers=headers, timeout=20).json()
    if "login" not in me:
        print(f"PAT invalide: {me.get('message', 'erreur inconnue')}")
        return 1
    owner = me["login"]
    print(f"Compte GitHub: {owner}")

    r = requests.post(
        f"{API}/user/repos",
        headers=headers,
        json={"name": repo, "private": False, "auto_init": False},
        timeout=20,
    )
    if r.status_code == 201:
        print(f"Repo créé: {owner}/{repo}")
    elif r.status_code == 422 and "already exists" in r.text.lower():
        print(f"Repo existant: {owner}/{repo}")
    else:
        print(f"Création échouée {r.status_code}: {r.text[:200]}")
        return 1

    url = f"https://x-access-token:{pat}@github.com/{owner}/{repo}.git"
    subprocess.run(["git", "remote", "remove", "origin"], cwd=BASE, capture_output=True)
    subprocess.run(["git", "remote", "add", "origin", url], cwd=BASE, check=True)
    subprocess.run(["git", "push", "-u", "origin", "main"], cwd=BASE, check=True)
    print("Code poussé.")

    key_resp = requests.get(
        f"{API}/repos/{owner}/{repo}/actions/secrets/public-key", headers=headers, timeout=20
    ).json()
    public_key = base64.b64decode(key_resp["key"])
    key_id = key_resp["key_id"]

    creds = Path.home() / ".config/price-watch/telegram.env"
    values = {}
    if creds.exists():
        for line in creds.read_text(encoding="utf-8").splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                values[k.strip()] = v.strip()
    # Le token est relu depuis le fichier local, jamais ré-affiché.
    for name in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
        secret = values.get(name) or os.environ.get(name)
        if not secret:
            print(f"Secret {name} absent — à ajouter manuellement dans Settings → Secrets.")
            continue
        encrypted = base64.b64encode(
            SealedBox(PublicKey(public_key)).encrypt(secret.encode())
        ).decode()
        resp = requests.put(
            f"{API}/repos/{owner}/{repo}/actions/secrets/{name}",
            headers=headers,
            json={"encrypted_value": encrypted, "key_id": key_id},
            timeout=20,
        )
        print(f"Secret {name}: {'OK' if resp.status_code in (201, 204) else resp.text[:120]}")

    requests.post(
        f"{API}/repos/{owner}/{repo}/actions/workflows/hourly.yml/dispatches",
        headers=headers,
        json={"ref": "main"},
        timeout=20,
    )
    print(f"Terminé. Actions: https://github.com/{owner}/{repo}/actions")
    return 0


if __name__ == "__main__":
    sys.exit(main())
