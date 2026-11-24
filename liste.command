#!/bin/bash
# Double-clique ce fichier pour gérer la liste des produits suivis.
cd "$(dirname "$0")" || exit 1
PORT=8766
if curl -s --max-time 2 "http://127.0.0.1:$PORT/api/liste" >/dev/null 2>&1; then
  echo "Le serveur tourne déjà."
else
  python3 liste_server.py &
  SRV=$!
  trap 'kill $SRV 2>/dev/null; exit 0' INT TERM
  sleep 1
fi
open "http://127.0.0.1:$PORT/liste.html"
echo "Serveur actif sur http://127.0.0.1:$PORT — ferme cette fenêtre pour l'arrêter."
wait
