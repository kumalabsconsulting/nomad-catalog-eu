# nomad-catalog-eu

Catalogues de contenu **francophones et européens** pour
[Project NOMAD](https://github.com/Crosstalk-Solutions/project-nomad), la
plateforme de connaissance hors-ligne.

Project NOMAD est excellent, et entièrement anglophone. Ses catalogues curés —
Wikipédia, collections Kiwix, cartes — ne proposent que du contenu en anglais et
des régions américaines. Ce dépôt fournit l'équivalent pour d'autres langues, en
commençant par le français.

## État actuel

| Langue | Catégories | Ressources | Vérifié le |
|---|---|---|---|
| Français | 7 | 37 | voir la CI |

Toutes les ressources proviennent du catalogue officiel
[Kiwix](https://library.kiwix.org). Aucune URL ni taille n'est saisie à la
main : elles sont extraites du flux OPDS de Kiwix et vérifiées automatiquement.

### Catégories françaises

| Catégorie | Contenu principal |
|---|---|
| **Médecine** | WikiMed (86 805 articles médicaux), anatomie, biologie |
| **Autonomie & Survie** | Ekopedia, Wikilivres, énergie, climat, géographie |
| **Agriculture & Potager** | Botanique, agronomie, cuisine, alimentation |
| **Réparation & Bricolage** | iFixit en français, technologie, électronique |
| **Référence générale** | Wikipédia, Wiktionnaire, Wikisource |
| **Éducation** | Vikidia, PhET, Wikiversité, Khan Academy |
| **Informatique** | Documentation Ubuntu, Python, Scratch |

Chaque catégorie propose trois paliers — *Essentiel* (recommandé), *Standard*,
*Complet* — pour choisir selon la place disponible.

## ⚠️ Ces catalogues ne sont pas encore consommables automatiquement

Project NOMAD lit ses catalogues depuis des **URL codées en dur** dans
`admin/app/services/collection_manifest_service.ts`. Il n'existe aujourd'hui ni
variable d'environnement, ni réglage, ni interface permettant d'ajouter une
source de catalogue.

L'issue amont
[#1323](https://github.com/Crosstalk-Solutions/project-nomad/issues/1323)
propose de rendre ces sources configurables. **Tant qu'elle n'est pas résolue,
ce dépôt sert de source de référence pour une installation manuelle**, décrite
ci-dessous.

## Installation manuelle, aujourd'hui

Le schéma reproduit exactement celui de Project NOMAD, y compris le champ
`language` déjà présent en amont. En attendant le support natif :

```bash
# 1. choisir une ressource dans catalog/fr/kiwix-categories.json
#    et la telecharger dans le dossier ZIM de NOMAD
cd /opt/project-nomad/storage/zim
curl -O https://download.kiwix.org/zim/wikipedia/wikipedia_fr_medicine_maxi_2026-07.zim

# 2. enregistrer la bibliotheque
curl -X POST http://localhost:8080/api/zim/rescan-library
docker restart nomad_kiwix_server
```

Le nom de fichier doit respecter le motif `<identifiant>_<AAAA-MM>.zim`, sans
quoi NOMAD ignore le fichier. Les noms publiés par Kiwix le respectent déjà.

## Reconstruire ou mettre à jour les catalogues

```bash
python3 scripts/fetch_kiwix.py fra     # rafraichit l'index depuis Kiwix
python3 scripts/build_catalog.py       # regenere catalog/fr/*.json
python3 scripts/verify.py              # verifie que chaque URL repond
```

`verify.py` sort en code non nul si une ressource est injoignable. Un catalogue
aux liens morts est pire qu'un catalogue vide : la panne se découvre le jour où
l'on n'a plus internet pour la corriger.

## Contribuer une autre langue

La structure est prévue pour être multilingue. Pour ajouter une langue :

1. `python3 scripts/fetch_kiwix.py <code ISO 639-3>` — par exemple `spa`, `deu`
2. définir une sélection dans `scripts/build_catalog.py`
3. générer dans `catalog/<code ISO 639-1>/`
4. `python3 scripts/verify.py`

**La curation doit être faite par quelqu'un qui parle la langue.** Le catalogue
français est curé par un francophone ; je ne prétendrai pas curer l'espagnol ou
l'allemand à la place de ceux qui les parlent.

## Un manque que ce catalogue ne comble pas

Le catalogue Kiwix francophone ne contient **aucune ressource de survie**, et
seulement quelques dizaines de mégaoctets sur l'agriculture — là où l'équivalent
anglophone offre respectivement 14,6 Go et 9,2 Go.

Ce n'est pas un défaut de ce dépôt, c'est un manque en amont : ce contenu
n'existe pas en français au format ZIM. Le combler suppose de **fabriquer** des
archives à partir de sources francophones (premiers secours, potager,
conservation, low-tech, base de données publique des médicaments de l'ANSM),
avec les outils OpenZIM `zimit` et `zimwriterfs`. C'est la suite prévue.

## Licence

Les catalogues (fichiers JSON de ce dépôt) sont sous licence **CC0** : ce sont
des listes de faits, réutilisez-les librement.

Les **ressources référencées** appartiennent à leurs auteurs respectifs et
restent soumises à leurs licences propres. Ce dépôt ne redistribue aucun
contenu : il ne contient que des références vers le catalogue public de Kiwix.
