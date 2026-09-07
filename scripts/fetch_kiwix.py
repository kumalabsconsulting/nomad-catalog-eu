#!/usr/bin/env python3
"""Récupère l'index des ressources Kiwix d'une langue depuis le catalogue OPDS.

Sert de source unique aux catalogues : ni les URL ni les tailles ne sont
saisies à la main, elles viennent toujours de Kiwix. Relancer ce script suffit
à rafraîchir un catalogue quand de nouvelles versions sont publiées.

Usage : fetch_kiwix.py [code_langue_iso639-3]   (défaut : fra)
"""
import json
import os
import sys
import urllib.request
import xml.etree.ElementTree as ET

LANG = sys.argv[1] if len(sys.argv) > 1 else "fra"
BASE = "https://opds.library.kiwix.org/catalog/v2/entries"
NS = {"a": "http://www.w3.org/2005/Atom"}
RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SORTIE = os.path.join(RACINE, "data", "kiwix_index.json")


def page(start, count=100):
    url = f"{BASE}?lang={LANG}&count={count}&start={start}"
    req = urllib.request.Request(url, headers={"User-Agent": "nomad-catalog-eu/1.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return ET.fromstring(r.read())


def texte(e, tag):
    n = e.find(f"a:{tag}", NS)
    return n.text if n is not None else None


def main():
    entrees, start = [], 0
    while True:
        got = page(start).findall("a:entry", NS)
        if not got:
            break
        for e in got:
            url = taille = None
            for l in e.findall("a:link", NS):
                if "acquisition" in (l.get("rel") or ""):
                    # le catalogue expose un metalink ; le .zim direct est la
                    # meme URL sans le suffixe .meta4
                    url = (l.get("href") or "").replace(".zim.meta4", ".zim")
                    taille = l.get("length")
                    break
            if not url:
                continue
            version = ""
            base = url.rsplit("/", 1)[-1].replace(".zim", "")
            if "_" in base:
                queue = base.rsplit("_", 1)[-1]
                if len(queue) == 7 and queue[4] == "-":   # AAAA-MM
                    version = queue
            entrees.append({
                "title": texte(e, "title"),
                "name": texte(e, "name"),
                "flavour": texte(e, "flavour"),
                "summary": texte(e, "summary") or "",
                "language": texte(e, "language"),
                "articles": texte(e, "articleCount"),
                "media": texte(e, "mediaCount"),
                "tags": texte(e, "tags") or "",
                "version": version,
                "url": url,
                "size_mb": round(int(taille) / 1048576) if taille and taille.isdigit() else None,
            })
        start += len(got)
        if len(got) < 100:
            break

    # doublons : on garde la plus grosse variante pour un couple (name, flavour),
    # qui correspond a la version la plus recente publiee par Kiwix
    retenu = {}
    for e in entrees:
        k = (e["name"], e["flavour"])
        if k not in retenu or (e["size_mb"] or 0) > (retenu[k]["size_mb"] or 0):
            retenu[k] = e

    os.makedirs(os.path.dirname(SORTIE), exist_ok=True)
    with open(SORTIE, "w", encoding="utf-8") as f:
        json.dump(sorted(retenu.values(), key=lambda x: -(x["size_mb"] or 0)),
                  f, ensure_ascii=False, indent=1)
        f.write("\n")
    print(f"langue {LANG} : {len(entrees)} entrees, {len(retenu)} apres deduplication")
    print(f"ecrit dans {SORTIE}")


if __name__ == "__main__":
    main()
