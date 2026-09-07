#!/usr/bin/env python3
"""Vérifie que chaque ressource du catalogue est réellement téléchargeable.

Un catalogue dont les liens sont morts est pire qu'un catalogue vide : on
découvre la panne le jour où l'on n'a plus internet pour la corriger. Ce script
interroge chaque URL en HEAD et compare la taille annoncée à la taille réelle.

Sortie non nulle si une ressource est injoignable — utilisable en CI.
"""
import json
import os
import sys
import urllib.error
import urllib.request

RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOLERANCE = 0.02  # 2 % d'ecart admis entre taille annoncee et taille reelle


def urls_du_catalogue():
    vus = {}
    for langue in sorted(os.listdir(os.path.join(RACINE, "catalog"))):
        base = os.path.join(RACINE, "catalog", langue)
        if not os.path.isdir(base):
            continue
        for fichier in sorted(os.listdir(base)):
            if not fichier.endswith(".json"):
                continue
            d = json.load(open(os.path.join(base, fichier), encoding="utf-8"))
            for cat in d.get("categories", []):
                for tier in cat["tiers"]:
                    for r in tier["resources"]:
                        vus.setdefault(r["url"], (f"{langue}/{cat['slug']}/{tier['slug']}", r.get("size_mb")))
            for o in d.get("options", []):
                if o.get("url"):
                    vus.setdefault(o["url"], (f"{langue}/wikipedia/{o['id']}", o.get("size_mb")))
    return vus


def tete(url, timeout=45):
    req = urllib.request.Request(url, method="HEAD",
                                 headers={"User-Agent": "nomad-catalog-eu/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.headers.get("Content-Length")


def main():
    cibles = urls_du_catalogue()
    print(f"{len(cibles)} URL uniques a verifier\n")
    echecs = []
    ecarts = []
    for url, (origine, annonce) in sorted(cibles.items(), key=lambda x: x[1][0]):
        nom = url.rsplit("/", 1)[-1]
        try:
            code, longueur = tete(url)
        except urllib.error.HTTPError as e:
            print(f"  ECHEC  {origine:<34} HTTP {e.code}  {nom}")
            echecs.append((origine, nom, f"HTTP {e.code}"))
            continue
        except Exception as e:
            print(f"  ECHEC  {origine:<34} {type(e).__name__}  {nom}")
            echecs.append((origine, nom, type(e).__name__))
            continue

        note = ""
        if longueur and annonce:
            reel = int(longueur) / 1048576
            # tolerance relative ET plancher absolu : sur un fichier de quelques
            # Mo, l'arrondi au Mo suffit a declencher un faux positif
            if abs(reel - annonce) > max(1.0, annonce * TOLERANCE):
                note = f"  (annonce {annonce} Mo, reel {reel:.0f} Mo)"
                ecarts.append((origine, nom, annonce, round(reel)))
        print(f"  ok     {origine:<34} HTTP {code}  {nom}{note}")

    print()
    if echecs:
        print(f"{len(echecs)} ressource(s) INJOIGNABLE(S) :")
        for o, n, r in echecs:
            print(f"  - {o} : {n} ({r})")
    if ecarts:
        print(f"{len(ecarts)} ecart(s) de taille — relancer build_catalog.py :")
        for o, n, a, r in ecarts:
            print(f"  - {o} : {n} annonce {a} Mo, reel {r} Mo")
    if not echecs and not ecarts:
        print("Toutes les ressources sont joignables et les tailles concordent.")
    sys.exit(1 if echecs else 0)


if __name__ == "__main__":
    main()
