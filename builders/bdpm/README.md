# Médicaments français hors ligne (BDPM)

Fabrique une archive ZIM de la **Base de Données Publique des Médicaments** —
la base officielle française, publiée par l'ANSM avec la HAS et l'UNCAM, en
licence ouverte.

C'est l'équivalent français de la « Medication Reference » que Project NOMAD
embarque depuis la version 1.34, laquelle repose sur les *drug labels* de la
**FDA**. Celle-ci est utile mais américaine : noms commerciaux, dosages et
présentations diffèrent, et elle ne doit pas servir à établir une posologie en
Europe.

Ce que l'archive contient, pour environ **15 900 spécialités** :

- la **notice patient** et le **résumé des caractéristiques du produit**, en
  texte intégral — donc trouvables par la recherche plein texte de Kiwix ;
- l'identité, la composition en substances actives, les présentations avec
  prix et taux de remboursement, les avis HAS, les conditions de délivrance ;
- un bandeau d'état par médicament : autorisation retirée, non commercialisé,
  surveillance renforcée ;
- deux index — par nom commercial, et **par substance active**, celui qui sert
  quand on a la plaquette sans la boîte.

## Utilisation

```bash
sudo apt-get install -y poppler-utils     # pdftotext, indispensable (voir plus bas)

python3 build_bdpm_zim.py fetch           # récupère les sources (reprenable)
python3 build_bdpm_zim.py build           # fabrique l'arborescence HTML

sudo docker run --rm -v /tmp/bdpm-src:/src -v /tmp/zimout:/out \
  ghcr.io/openzim/zim-tools zimwriterfs \
  -w index.html -I favicon.png -l fra -n bdpm-medicaments \
  -t 'Médicaments français (ANSM)' \
  -d 'Notices patient et RCP des médicaments autorisés en France' \
  -c 'ANSM / HAS / UNCAM' -p 'Project NOMAD' \
  /src /out/bdpm-medicaments_2026-09.zim
```

Le nom de fichier doit respecter le motif `<identifiant>_<AAAA-MM>.zim`, sans
quoi Project NOMAD ignore l'archive. Après dépôt dans le dossier ZIM :
`POST /api/zim/rescan-library`, puis redémarrage du serveur Kiwix.

### Réglages

| Variable | Défaut | Rôle |
|---|---|---|
| `BDPM_CACHE` | `/tmp/bdpm-cache` | sources téléchargées ; **le conserver**, il rend `fetch` reprenable |
| `OUTDIR` | `/tmp/bdpm-src` | arborescence HTML produite |
| `WORKERS` | `4` | connexions parallèles |
| `LIMIT` | `0` (tout) | nombre de spécialités, pour un essai rapide |
| `DELAI_EMA` | `1.5` | pause après chaque document européen récupéré |
| `PAUSE_429` | `120` | pause quand le CDN de l'EMA refuse pour cause de débit |
| `ESSAIS_EMA` | `6` | tentatives par document européen |

**Comptez plusieurs heures** pour un `fetch` complet, et gardez `WORKERS` bas.
Les deux sources sont des services publics ; `robots.txt` autorise l'accès,
ce n'est pas une raison pour les marteler. Une interruption ne coûte rien : le
cache est conservé et une relance ne reprend que ce qui manque.

## Pourquoi `pdftotext` est indispensable

**2 354 spécialités sur 15 859 — 14 %** sont autorisées par la Commission
européenne. Pour celles-là, la base française **n'héberge ni notice ni RCP** :
sa page ne contient qu'un renvoi vers un PDF de l'Agence européenne des
médicaments.

Sans suivre ce lien, l'archive perd silencieusement **les insulines, les
anticoagulants et l'essentiel de l'oncologie** : ces médicaments figurent dans
l'index et s'ouvrent vides. Le script récupère donc le PDF français de l'EMA et
en extrait le texte.

C'est le piège le plus coûteux de cette source, et il n'est pas le seul :
`CIS_RCP.zip` est servi **vide** avec un code HTTP 200, l'encodage n'est pas
homogène entre les fichiers d'une même administration, et le CDN de l'EMA bride
à partir de quelques centaines de PDF. Tout est détaillé, avec les symptômes,
dans **[docs/limites-et-pieges.md](../../docs/limites-et-pieges.md)**.

## Vérifier avant de se fier à l'archive

```bash
python3 -m unittest discover -s builders/bdpm -p 'test_*.py'
```

23 tests, sans accès réseau. **Chacun porte le nom d'une panne réellement
rencontrée**, et son docstring décrit le symptôme. Ce n'est pas une recherche
de couverture : presque toutes ces pannes étaient silencieuses — elles
produisaient une archive d'apparence correcte dont une partie du contenu était
vide, mal encodée ou inatteignable.

Sur l'archive elle-même :

```bash
sudo docker run --rm -v /tmp/zimout:/z ghcr.io/openzim/zim-tools \
  zimcheck -A /z/bdpm-medicaments_2026-09.zim        # attendu : Overall Test Status: Pass

sudo docker run -d --name kiwix-test -p 127.0.0.1:8891:8080 -v /tmp/zimout:/data \
  ghcr.io/kiwix/kiwix-serve:3.8.2 bdpm-medicaments_2026-09.zim
curl -sG --data-urlencode 'pattern=insuline glargine' \
     --data-urlencode 'books.filter.lang=fra' --data-urlencode 'pageLength=5' \
     http://127.0.0.1:8891/search
```

Chercher `insuline glargine` est **le** test qui compte : il ne peut réussir
que si le texte extrait des PDF européens est bien arrivé dans l'index plein
texte. Une archive qui se contente d'empaqueter des PDF serait navigable mais
pas cherchable.

## Limite qu'on ne peut pas lever

L'homéopathie — environ un millier de spécialités — n'a **ni notice ni
monographie publiées**. Ces médicaments figurent avec leur identité, leur
composition et leurs présentations, mais sans texte. C'est l'état de la base,
pas un défaut d'extraction.

## Licences

Les **données** sont sous licence ouverte (État français) pour la base
nationale, et publiées par l'Agence européenne des médicaments pour les
autorisations centralisées. Ce dépôt ne redistribue aucune donnée : il fournit
de quoi les récupérer à la source, à jour.

Ce **script** suit la licence du dépôt.

## Ce n'est pas un avis médical

L'archive contient les textes officiels tels que publiés à la date
d'extraction. C'est une référence, pas une prescription : elle ne remplace ni
un avis médical, ni un pharmacien. Une base figée vieillit — regénérez-la
plutôt que de vous fier à une copie ancienne.
