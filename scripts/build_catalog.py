#!/usr/bin/env python3
"""Génère les catalogues NOMAD à partir d'une sélection curée.

Le schéma reproduit exactement celui de Project NOMAD
(`collections/kiwix-categories.json`), y compris le champ `language` déjà
présent en amont — un catalogue produit ici est donc consommable tel quel le
jour où le sélecteur sait filtrer par langue (issue amont #1323).

Les tailles et URL ne sont pas saisies à la main : elles proviennent du
catalogue OPDS officiel de Kiwix, récupéré par `fetch_kiwix.py`.
"""
import json
import os
import sys

RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE = os.path.join(RACINE, "data", "kiwix_index.json")

# --------------------------------------------------------------------------
# Sélection curée. Chaque entrée référence une ressource Kiwix par (name,
# flavour) ; taille, URL et nombre d'articles sont résolus depuis l'index.
# Les paliers suivent la convention amont : essential (recommandé) / standard /
# comprehensive, du plus vital au plus confortable.
# --------------------------------------------------------------------------
CATALOGUE = {
    "medecine": {
        "name": "Médecine",
        "icon": "IconStethoscope",
        "description": "Références médicales francophones : encyclopédie clinique, anatomie, premiers secours.",
        "tiers": [
            ("essential", "Essentiel", "L'encyclopédie médicale francophone. À prendre en premier.", True,
             [("wikipedia_fr_medicine", "maxi")]),
            ("standard", "Standard", "Ajoute le corps humain et la médecine expliqués en vidéo.", False,
             [("cest-pas-sorcier_fr_corps-humain", None), ("cest-pas-sorcier_fr_medecine", None)]),
            ("comprehensive", "Complet", "Biologie moléculaire et ressources universitaires.", False,
             [("wikipedia_fr_molcell", "maxi"), ("wikiversity_fr_all", "maxi")]),
        ],
    },
    "autonomie": {
        "name": "Autonomie & Survie",
        "icon": "IconShieldCheck",
        "description": "Vivre en autonomie : écologie pratique, énergie, alimentation, savoir-faire manuels.",
        "tiers": [
            ("essential", "Essentiel", "Écologie pratique et manuels de savoir-faire.", True,
             [("ekopedia_fr_all", "maxi"), ("wikibooks_fr_all", "maxi")]),
            ("standard", "Standard", "Énergie, climat et agronomie.", False,
             [("cest-pas-sorcier_fr_energie", None), ("mooc-energie-climat_fr_all", None),
              ("wikipedia_fr_climate-change", "maxi")]),
            ("comprehensive", "Complet", "Géographie de terrain et guides de voyage hors ligne.", False,
             [("wikivoyage_fr_all", "maxi"), ("wikipedia_fr_geography", "maxi")]),
        ],
    },
    "agriculture": {
        "name": "Agriculture & Potager",
        "icon": "IconPlant",
        "description": "Cultiver et se nourrir : botanique, agronomie, cuisine, conservation.",
        "tiers": [
            ("essential", "Essentiel", "Botanique et cuisine de base.", True,
             [("cest-pas-sorcier_fr_botanique", None), ("cuisinelibre.org_fr_all", None)]),
            ("standard", "Standard", "Agronomie et alimentation.", False,
             [("cest-pas-sorcier_fr_agronomie", None), ("maitre_lucas_alimentation_fr", None)]),
            ("comprehensive", "Complet", "Planète, environnement et développement du vivant.", False,
             [("maitre_lucas_planete_terre_fr", None), ("maitre_lucas_developpement_fr", None)]),
        ],
    },
    "reparation": {
        "name": "Réparation & Bricolage",
        "icon": "IconTool",
        "description": "Réparer plutôt que remplacer : guides pas à pas, technologie, électronique.",
        "tiers": [
            ("essential", "Essentiel", "Les guides de réparation iFixit en français.", True,
             [("ifixit_fr_all", None)]),
            ("standard", "Standard", "Technologie et industrie expliquées.", False,
             [("cest-pas-sorcier_fr_technologie", None)]),
            ("comprehensive", "Complet", "Électronique et physique appliquée.", False,
             [("deus-ex-silicium_fr_all", None), ("wikipedia_fr_physics", "maxi")]),
        ],
    },
    "reference": {
        "name": "Référence générale",
        "icon": "IconBook",
        "description": "Le socle encyclopédique francophone : Wikipédia, dictionnaire, bibliothèque.",
        "tiers": [
            ("essential", "Essentiel", "Le meilleur de Wikipédia et le dictionnaire. Compact.", True,
             [("wikipedia_fr_top", "maxi"), ("wiktionary_fr_all", "nopic")]),
            ("standard", "Standard", "Wikipédia complète sans images.", False,
             [("wikipedia_fr_all", "nopic")]),
            ("comprehensive", "Complet", "Wikipédia intégrale avec images, et la bibliothèque Wikisource.", False,
             [("wikipedia_fr_all", "maxi"), ("wikisource_fr_all", "nopic")]),
        ],
    },
    "education": {
        "name": "Éducation",
        "icon": "IconSchool",
        "description": "Apprendre et enseigner hors ligne, du primaire au supérieur.",
        "tiers": [
            ("essential", "Essentiel", "L'encyclopédie des enfants et les sciences interactives.", True,
             [("vikidia_fr_all", "maxi"), ("phet_fr_all", None)]),
            ("standard", "Standard", "Ressources universitaires et scolaires.", False,
             [("wikiversity_fr_all", "maxi"), ("youscribe_fr_college", None), ("youscribe_fr_lycee", None)]),
            ("comprehensive", "Complet", "Khan Academy en français. Très volumineux.", False,
             [("khanacademy_fr_all", None)]),
        ],
    },
    "informatique": {
        "name": "Informatique",
        "icon": "IconCode",
        "description": "Maintenir et réparer ses outils numériques sans internet.",
        "tiers": [
            ("essential", "Essentiel", "La documentation Ubuntu francophone.", True,
             [("doc.ubuntu-fr.org_fr_all", None)]),
            ("standard", "Standard", "Programmation et informatique générale.", False,
             [("unine.ch_fr_premiers-pas-avec-python", None), ("wikipedia_fr_computer", "maxi")]),
            ("comprehensive", "Complet", "Initiation à la programmation pour les plus jeunes.", False,
             [("scratch-wiki_fr_all", "maxi")]),
        ],
    },
}

# Options Wikipédia, au format attendu par `collections/wikipedia.json`
WIKIPEDIA = [
    ("none", "Pas de Wikipédia", "Ignorer l'installation de Wikipédia", None, None),
    ("top-mini", "Référence rapide", "Les 246 000 articles les plus consultés, images réduites.", "wikipedia_fr_top", "mini"),
    ("top-maxi", "Articles populaires", "Les 246 000 articles les plus consultés, avec images.", "wikipedia_fr_top", "maxi"),
    ("all-mini", "Wikipédia complète (compacte)", "Les 4,5 millions d'articles en format condensé.", "wikipedia_fr_all", "mini"),
    ("all-nopic", "Wikipédia complète (sans images)", "Tous les articles, sans illustrations.", "wikipedia_fr_all", "nopic"),
    ("all-maxi", "Wikipédia complète (intégrale)", "Tous les articles avec images et médias.", "wikipedia_fr_all", "maxi"),
]


def charger_index():
    if not os.path.exists(SOURCE):
        sys.exit(f"Index Kiwix absent : {SOURCE}\nLancez d'abord scripts/fetch_kiwix.py")
    idx = {}
    for e in json.load(open(SOURCE, encoding="utf-8")):
        idx[(e["name"], e["flavour"])] = e
    return idx


def resoudre(idx, name, flavour):
    e = idx.get((name, flavour))
    if e is None:
        return None
    return e


def main():
    idx = charger_index()
    manquants = []

    categories = []
    for slug, c in CATALOGUE.items():
        tiers = []
        for tslug, tname, tdesc, reco, refs in c["tiers"]:
            resources = []
            for name, flavour in refs:
                e = resoudre(idx, name, flavour)
                if e is None:
                    manquants.append(f"{slug}/{tslug}: {name} ({flavour})")
                    continue
                res = {
                    "id": f"{name}_{flavour}" if flavour else name,
                    "version": e.get("version") or "",
                    "title": e["title"],
                    "description": (e.get("summary") or "").strip()[:160],
                    "url": e["url"],
                    "size_mb": e["size_mb"],
                }
                if e.get("articles"):
                    res["article_count"] = int(e["articles"])
                resources.append(res)
            if resources:
                t = {"name": tname, "slug": f"{slug}-{tslug}", "description": tdesc, "resources": resources}
                if reco:
                    t["recommended"] = True
                tiers.append(t)
        categories.append({
            "name": c["name"], "slug": slug, "icon": c["icon"],
            "description": c["description"], "language": "fr", "tiers": tiers,
        })

    wiki = []
    for oid, nom, desc, name, flavour in WIKIPEDIA:
        if name is None:
            wiki.append({"id": oid, "name": nom, "description": desc, "size_mb": 0, "url": None, "version": None})
            continue
        e = resoudre(idx, name, flavour)
        if e is None:
            manquants.append(f"wikipedia/{oid}: {name} ({flavour})")
            continue
        wiki.append({"id": oid, "name": nom, "description": desc,
                     "size_mb": e["size_mb"], "url": e["url"], "version": e.get("version") or ""})

    os.makedirs(os.path.join(RACINE, "catalog", "fr"), exist_ok=True)
    ecrire(os.path.join(RACINE, "catalog", "fr", "kiwix-categories.json"),
           {"spec_version": "2026-03-15", "language": "fr", "categories": categories})
    ecrire(os.path.join(RACINE, "catalog", "fr", "wikipedia.json"),
           {"spec_version": "2026-08-02", "language": "fr", "options": wiki})

    total = sum(r["size_mb"] or 0 for c in categories for t in c["tiers"] for r in t["resources"])
    print(f"{len(categories)} categories, "
          f"{sum(len(t['resources']) for c in categories for t in c['tiers'])} ressources, "
          f"{total/1024:.1f} Go cumules")
    for c in categories:
        st = sum(r["size_mb"] or 0 for t in c["tiers"] for r in t["resources"])
        print(f"  {c['slug']:<14} {sum(len(t['resources']) for t in c['tiers']):>2} ressources  {st/1024:>6.1f} Go")
    if manquants:
        print("\nReferences introuvables dans l'index :")
        for m in manquants:
            print("  -", m)


def ecrire(chemin, donnees):
    with open(chemin, "w", encoding="utf-8") as f:
        json.dump(donnees, f, ensure_ascii=False, indent=2)
        f.write("\n")


if __name__ == "__main__":
    main()
