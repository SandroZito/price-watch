#!/bin/bash
# Double-clique ce fichier pour gérer la liste des produits suivis.
set -u
cd "$(dirname "$0")" || { echo "Dossier introuvable"; read -n1 -p "Entrée pour fermer"; exit 1; }

PY="$(command -v python3 || true)"
[ -z "$PY" ] && PY="/usr/bin/python3"
LOG="/tmp/liste-prix.log"
OK=0
PORT=8766

# Cherche un serveur déjà actif, sinon un port libre.
for p in 8766 8767 8768 8769 8770; do
  if curl -s --max-time 1 "http://127.0.0.1:$p/api/liste" >/dev/null 2>&1; then
    PORT=$p; OK=1; break
  fi
done

if [ "$OK" = "0" ]; then
  echo "Démarrage de la liste…"
  LISTE_PORT=$PORT "$PY" liste_server.py >"$LOG" 2>&1 &
  SRV=$!
  trap 'kill $SRV 2>/dev/null; exit 0' INT TERM
  for i in $(seq 1 24); do
    if curl -s --max-time 1 "http://127.0.0.1:$PORT/api/liste" >/dev/null 2>&1; then OK=1; break; fi
    sleep 0.5
  done
fi

if [ "$OK" = "0" ]; then
  echo ""
  echo "❌ Le serveur n'a pas démarré. Détail :"
  cat "$LOG" 2>/dev/null | tail -20
  echo ""
  read -n1 -p "Appuie sur Entrée pour fermer"
  exit 1
fi

open "http://127.0.0.1:$PORT/liste.html"
echo ""
echo "✅ Liste ouverte : http://127.0.0.1:$PORT/liste.html"
echo "   Garde cette fenêtre ouverte pendant tes modifications."
echo "   Ctrl+C (ou fermer la fenêtre) = arrêt du serveur."
echo "   Le suivi des prix, lui, continue dans le cloud."
if [ -n "${SRV:-}" ]; then
  wait "$SRV"
fi
exit 0
