#!/usr/bin/env python3
"""Construit la Base de Donnees Publique des Medicaments au format ZIM (Kiwix).

Source : base-donnees-publique.medicaments.gouv.fr (ANSM / HAS / UNCAM),
licence ouverte. Deux origines complementaires, et il faut les deux :

  - les fichiers structures (CIS_*.txt) donnent l'identite, la composition,
    les presentations, les avis HAS et les conditions de delivrance ;
  - la page publique de chaque specialite porte la NOTICE PATIENT et le RCP,
    qui ne sont plus telechargeables en masse (CIS_RCP.zip est servi vide).

Piege : pour les 14 % de specialites autorisees par la Commission europeenne,
la BDPM n'heberge pas ces textes — sa page ne contient qu'un renvoi vers un PDF
de l'EMA. Sans le suivre, l'archive perdrait silencieusement les insulines, les
anticoagulants et l'essentiel de l'oncologie. On telecharge donc ce PDF (en
francais, l'URL est dans la page) et on en extrait le texte avec pdftotext.

Le seul dump RCP encore disponible, sur data.gouv.fr, date de mai 2022. Il est
volontairement ignore : sur une reference medicale d'urgence, une posologie ou
une contre-indication perimee est pire qu'une absence, et la melanger a des
fichiers structures du jour creerait une incoherence invisible.

Deux etapes, separees pour etre rejouables :

    build_bdpm_zim.py fetch     recupere les pages (reprise sur interruption)
    build_bdpm_zim.py build     fabrique l'arborescence HTML du ZIM

Contrainte : tout est auto-suffisant dans le ZIM. Aucune police externe,
aucun CDN, aucune requete reseau a la lecture.
"""
import gzip
import html
import os
import re
import struct
import subprocess
import sys
import time
import urllib.error
import urllib.request
import zlib
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser

SITE = "https://base-donnees-publique.medicaments.gouv.fr"
CACHE = os.environ.get("BDPM_CACHE", "/tmp/bdpm-cache")
OUT = os.environ.get("OUTDIR", "/tmp/bdpm-src")
LIMIT = int(os.environ.get("LIMIT", "0"))          # 0 = toutes les specialites
WORKERS = int(os.environ.get("WORKERS", "4"))      # rester poli : site public
# Le CDN de l'EMA bride a partir de quelques centaines de PDF (HTTP 429) : on
# s'y adapte par la patience, cf. recupere_ema.
DELAI_EMA = float(os.environ.get("DELAI_EMA", "1.5"))   # apres chaque succes
PAUSE_429 = float(os.environ.get("PAUSE_429", "120"))   # apres un refus
ESSAIS_EMA = int(os.environ.get("ESSAIS_EMA", "6"))
UA = "nomad-bdpm-zim/1.0 (archive hors-ligne; contact via github)"

FICHIERS = [
    "CIS_bdpm.txt", "CIS_CIP_bdpm.txt", "CIS_COMPO_bdpm.txt",
    "CIS_GENER_bdpm.txt", "CIS_HAS_SMR_bdpm.txt", "CIS_CPD_bdpm.txt",
]


# --------------------------------------------------------------------------
# lecture des fichiers structures
# --------------------------------------------------------------------------

def lire_tsv(chemin):
    """Lit un fichier BDPM tabule.

    L'encodage n'est pas homogene d'un fichier a l'autre : CIS_CIP_bdpm.txt est
    en UTF-8, les autres en Windows-1252. Forcer un encodage unique abime
    forcement une moitie du corpus, on le detecte donc fichier par fichier.
    """
    brut = open(chemin, "rb").read()
    try:
        texte = brut.decode("utf-8")
    except UnicodeDecodeError:
        texte = brut.decode("cp1252", "replace")
    lignes = []
    for l in texte.split("\n"):
        l = l.rstrip("\r")
        if l.strip():
            lignes.append(l.split("\t"))
    return lignes


def champ(ligne, i):
    return ligne[i].strip() if len(ligne) > i else ""


def telecharge_fichiers():
    os.makedirs(f"{CACHE}/data", exist_ok=True)
    for f in FICHIERS:
        dest = f"{CACHE}/data/{f}"
        req = urllib.request.Request(f"{SITE}/download/file/{f}",
                                     headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=120) as r:
            contenu = r.read()
        if not contenu:
            raise SystemExit(f"{f} est servi vide par la source — arret.")
        open(dest, "wb").write(contenu)
        print(f"  {f:<24} {len(contenu) // 1024:>6} Ko")


# --------------------------------------------------------------------------
# recuperation des pages (notice + RCP)
# --------------------------------------------------------------------------

def chemin_cache(cis):
    # 16 000 fichiers dans un seul dossier : on repartit sur deux niveaux
    return f"{CACHE}/pages/{cis[:2]}/{cis}.html.gz"


def raison(e):
    """Motif d'echec exploitable.

    Un compteur d'echecs sans motif ne dit pas si la source est tombee, si elle
    bride le debit ou si le format a change — trois pannes qui n'appellent pas
    du tout la meme reaction. Le motif est ce qui permet de decider.
    """
    if isinstance(e, urllib.error.HTTPError):
        return f"HTTP {e.code}"
    if isinstance(e, urllib.error.URLError):
        return f"URLError {getattr(e, 'reason', '')}".strip()[:40]
    return type(e).__name__


def recupere_page(cis):
    """Recupere la page d'une specialite : 'ok', 'cache' ou 'echec <motif>'."""
    dest = chemin_cache(cis)
    if os.path.exists(dest) and os.path.getsize(dest) > 0:
        return "cache"
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    dernier = "inconnu"
    for essai in range(3):
        try:
            req = urllib.request.Request(f"{SITE}/extrait.php?specid={cis}",
                                         headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=60) as r:
                contenu = r.read()
            if len(contenu) < 2000:
                raise ValueError(f"page anormalement courte ({len(contenu)} o)")
            tmp = dest + ".part"
            with gzip.open(tmp, "wb") as f:
                f.write(contenu)
            os.replace(tmp, dest)      # jamais de fichier tronque dans le cache
            return "ok"
        except Exception as e:
            dernier = raison(e)
            if essai < 2:
                time.sleep(2 ** essai)
    return f"echec {dernier}"


# --------------------------------------------------------------------------
# medicaments autorises par la Commission europeenne : textes chez l'EMA
# --------------------------------------------------------------------------

RE_EMA = re.compile(
    r'href="(https://www\.ema\.europa\.eu/[^"]*product-information[^"]*\.pdf)"',
    re.I)


def url_ema(page):
    m = RE_EMA.search(page)
    return m.group(1) if m else None


def chemin_ema(url):
    return f"{CACHE}/ema/{url.rsplit('/', 1)[-1]}"


def recupere_ema(url):
    """Recupere un document EMA : 'ok', 'cache' ou 'echec <motif>'.

    Le CDN de l'EMA repond HTTP 429 des quelques centaines de PDF, et son
    en-tete 'retry-after' vaut 0.000 — il n'indique donc rien d'exploitable.
    Observe : un premier lot passe, le suivant est refuse en bloc, et la pause
    de quelques heures suffit a le debloquer. C'est un seau a jetons qui se
    remplit lentement.

    On y repond par de la patience, pas par de la vitesse : un delai apres
    chaque telechargement reussi pour ne pas vider le seau, et une longue pause
    quand le 429 tombe quand meme. Foncer et reessayer vite ne fait que
    consommer les essais et rendre la reprise plus longue.
    """
    dest = chemin_ema(url)
    if os.path.exists(dest) and os.path.getsize(dest) > 0:
        return "cache"
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    dernier = "inconnu"
    for essai in range(ESSAIS_EMA):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=180) as r:
                contenu = r.read()
            if not contenu.startswith(b"%PDF"):
                raise ValueError("la reponse n'est pas un PDF")
            tmp = dest + ".part"
            open(tmp, "wb").write(contenu)
            os.replace(tmp, dest)
            time.sleep(DELAI_EMA)
            return "ok"
        except Exception as e:
            dernier = raison(e)
            if essai == ESSAIS_EMA - 1:
                break
            bride = isinstance(e, urllib.error.HTTPError) and e.code == 429
            time.sleep(PAUSE_429 if bride else 5 * (essai + 1))
    return f"echec {dernier}"


def urls_ema_du_cache(cis_liste):
    """Releve les PDF EMA cites par les pages deja en cache, sans doublon.

    Plusieurs presentations d'un meme produit (stylo, cartouche, flacon)
    partagent le meme document : dedoublonner divise les telechargements par
    deux et evite de marteler le site de l'EMA.
    """
    urls = set()
    for cis in cis_liste:
        c = chemin_cache(cis)
        if not os.path.exists(c):
            continue
        try:
            with gzip.open(c, "rb") as f:
                page = f.read().decode("utf-8", "replace")
        except Exception:
            continue
        u = url_ema(page)
        if u:
            urls.add(u)
    return sorted(urls)


def motifs_echec(compte):
    """Repartition des echecs par motif, la seule chose qui dit quoi faire."""
    return {k[6:]: v for k, v in compte.items() if k.startswith("echec ")}


def recolte(fonction, cibles, quoi, pas):
    """Applique `fonction` a `cibles` en parallele et rend compte."""
    compte = defaultdict(int)
    debut = time.time()
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        for i, res in enumerate(pool.map(fonction, cibles), 1):
            compte[res] += 1
            if i % pas == 0 or i == len(cibles):
                rates = sum(motifs_echec(compte).values())
                reste = ((time.time() - debut) / i) * (len(cibles) - i)
                print(f"  {i}/{len(cibles)}  telecharges {compte['ok']}  "
                      f"deja en cache {compte['cache']}  echecs {rates}  "
                      f"reste ~{reste / 60:.0f} min", end="\r", flush=True)
    print()
    motifs = motifs_echec(compte)
    if motifs:
        total = sum(motifs.values())
        detail = ", ".join(f"{v}x {k}" for k, v in
                           sorted(motifs.items(), key=lambda x: -x[1]))
        print(f"\n{total} {quoi} non recupere(s) : {detail}")
        print("Relancer 'fetch' reprendra ce qui manque : le cache est conserve.")
    return compte


def phase_fetch():
    print("Fichiers structures...")
    telecharge_fichiers()

    cis_liste = [champ(l, 0) for l in lire_tsv(f"{CACHE}/data/CIS_bdpm.txt")]
    cis_liste = [c for c in cis_liste if c.isdigit()]
    if LIMIT:
        cis_liste = cis_liste[:LIMIT]
    print(f"\n{len(cis_liste)} specialites a recuperer ({WORKERS} en parallele)")

    compte = recolte(recupere_page, cis_liste, "page(s)", 100)
    urls = urls_ema_du_cache(cis_liste)
    print(f"\n{len(urls)} documents EMA a recuperer (autorisations europeennes)")
    compte.update(recolte(recupere_ema, urls, "document(s) EMA", 25))
    return compte


# --------------------------------------------------------------------------
# extraction du contenu utile dans la page
# --------------------------------------------------------------------------

VIDES = {"br", "img", "hr", "meta", "input", "link"}
GARDEES = {"h2", "h3", "h4", "h5", "h6", "p", "ul", "ol", "li", "table",
           "thead", "tbody", "tr", "td", "th", "strong", "em", "b", "i",
           "br", "sup", "sub", "dl", "dt", "dd"}
CLASSES_JETEES = ("fr-no-print", "print-div", "link-haut-page", "fr-sidemenu",
                  "fr-tabs__list")


class Panneau(HTMLParser):
    """Isole le sous-arbre HTML de l'element portant l'id demande."""

    def __init__(self, cible):
        super().__init__(convert_charrefs=False)
        self.cible, self.prof, self.dans, self.morceaux = cible, 0, False, []

    def handle_starttag(self, tag, attrs):
        if not self.dans:
            if dict(attrs).get("id") == self.cible:
                self.dans, self.prof = True, 1
            return
        if tag not in VIDES:
            self.prof += 1
        self.morceaux.append(self.get_starttag_text())

    def handle_endtag(self, tag):
        if not self.dans:
            return
        if tag not in VIDES:
            self.prof -= 1
            if self.prof <= 0:
                self.dans = False
                return
        self.morceaux.append(f"</{tag}>")

    def handle_data(self, d):
        if self.dans:
            self.morceaux.append(d)

    def handle_entityref(self, n):
        if self.dans:
            self.morceaux.append(f"&{n};")

    def handle_charref(self, n):
        if self.dans:
            self.morceaux.append(f"&#{n};")


class Nettoyeur(HTMLParser):
    """Ne conserve que des balises de contenu, sans aucun attribut.

    Le site est habille par le systeme de design de l'Etat : sans ce filtrage,
    le ZIM emporterait la moitie du theme DSFR pour chaque medicament.
    """

    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.out, self.jete = [], 0

    def handle_starttag(self, tag, attrs):
        cls = dict(attrs).get("class", "")
        if self.jete:
            if tag not in VIDES:
                self.jete += 1
            return
        if tag in ("script", "style", "nav", "button", "form") \
                or any(c in cls for c in CLASSES_JETEES):
            self.jete = 1 if tag not in VIDES else 0
            return
        if tag in GARDEES:
            self.out.append(f"<{tag}>")

    def handle_endtag(self, tag):
        if self.jete:
            if tag not in VIDES:
                self.jete -= 1
            return
        if tag in GARDEES and tag not in VIDES:
            self.out.append(f"</{tag}>")

    def handle_data(self, d):
        if not self.jete:
            self.out.append(d)

    def handle_entityref(self, n):
        if not self.jete:
            self.out.append(f"&{n};")

    def handle_charref(self, n):
        if not self.jete:
            self.out.append(f"&#{n};")


def extrait_panneau(page, cible):
    p = Panneau(cible)
    p.feed(page)
    n = Nettoyeur()
    n.feed("".join(p.morceaux))
    s = "".join(n.out).replace("Redirection vers le haut de page", " ")
    s = re.sub(r"(?is)<p>(\s|&nbsp;)*</p>", "", s)
    return re.sub(r"[ \t]{2,}", " ", s).strip()


# Un RCP et une notice sont numerotes ("4.3 Contre-indications"). Le site rend
# ces titres avec des classes, que le nettoyage supprime : on les retrouve donc
# sur le motif de numerotation, pour rendre le document navigable.
NUM = re.compile(
    r"(?is)<p>\s*((?:\d+\.){1,2}\d*)\s*[.  ]\s*([^<]{3,120}?)\s*</p>")


def ident_unique(base, vus):
    """Un document EMA repete son RCP pour chaque presentation : sans compteur,
    la page sortirait avec des id en double, ce qui casse les ancres.

    Le suffixe est 'bis' et non un simple chiffre : 'r-4' vu deux fois donnerait
    'r-4-2', qui percuterait la vraie rubrique 4.2.
    """
    vus[base] = vus.get(base, 0) + 1
    return base if vus[base] == 1 else f"{base}-bis{vus[base]}"


def titre_sections(frag, prefixe):
    """Transforme les paragraphes numerotes en vrais titres ancres."""
    ancres, vus = [], {}

    def remplace(m):
        num, libelle = m.group(1).rstrip("."), m.group(2).strip()
        ident = ident_unique(f"{prefixe}-{num.replace('.', '-')}", vus)
        ancres.append((num, libelle, ident))
        return (f'<h3 id="{ident}"><span class="num">{html.escape(num)}</span> '
                f"{html.escape(libelle)}</h3>")

    return NUM.sub(remplace, frag), ancres


RE_RUBRIQUE = re.compile(r'<h3 id="([^"]+)">.*?</h3>(.*?)(?=<h3 |\Z)', re.S)


def ancres_creuses(frag):
    """Rubriques sans contenu propre : ce sont les entrees de sommaire.

    Une notice EMA s'ouvre sur « Que contient cette notice ? » suivi de la
    liste de ses rubriques. Y envoyer les raccourcis ferait atterrir le lecteur
    sur le sommaire au lieu du texte qu'il cherche.
    """
    creuses = set()
    for m in RE_RUBRIQUE.finditer(frag):
        if not re.sub(r"<[^>]+>", "", m.group(2)).strip():
            creuses.add(m.group(1))
    return creuses


# Ce qu'on cherche en premier quand on ouvre une notice sans etre soignant.
REPERES = [
    ("contre-indication", "Contre-indications"),
    ("posologie", "Posologie"),
    ("mode d'administration", "Administration"),
    ("indication", "Indications"),
    ("effets indesirables", "Effets indésirables"),
    ("effets secondaires", "Effets indésirables"),
    ("grossesse", "Grossesse"),
    ("surdosage", "Surdosage"),
    ("conservation", "Conservation"),
]


# Les majuscules accentuees comptent autant que les minuscules : les noms de
# specialites sont ecrits en capitales. Sans elles, EPHYNAL classe sous "É"
# tombait dans une lettre absente de l'index — donc dans aucune page — et se
# triait apres le Z.
ACCENTS = str.maketrans(
    "áàâäãåéèêëíìîïóòôöõúùûüýÿçñ" "ÁÀÂÄÃÅÉÈÊËÍÌÎÏÓÒÔÖÕÚÙÛÜÝŸÇÑ",
    "aaaaaaeeeeiiiiooooouuuuyycn" "AAAAAAEEEEIIIIOOOOOUUUUYYCN")
LIGATURES = [("œ", "oe"), ("Œ", "OE"), ("æ", "ae"), ("Æ", "AE")]


def sans_accent(s):
    s = s.translate(ACCENTS)
    for a, b in LIGATURES:
        s = s.replace(a, b)
    return s


def reperes_utiles(ancres):
    """Selectionne les sections qu'on veut atteindre en un clic."""
    trouves, vus = [], set()
    for num, libelle, ident in ancres:
        l = sans_accent(libelle.lower())
        for motif, etiquette in REPERES:
            if motif in l and etiquette not in vus:
                vus.add(etiquette)
                trouves.append((etiquette, ident))
                break
    ordre = [e for _, e in REPERES]
    return sorted(trouves, key=lambda x: ordre.index(x[0]))


# --- conversion des documents EMA -----------------------------------------

# Le PDF pose le numero de rubrique et son libelle dans deux blocs distincts :
# une ligne "4.1", puis le titre. Une ligne qui n'est qu'un entier sans point
# ("33") est un numero de page, pas une rubrique — le point les separe.
NUMERO_RUBRIQUE = re.compile(r"^(\d{1,2}\.(?:\d{1,2})?)$")
RE_ANNEXE = re.compile(r"(?m)^\s*ANNEXE\s+([IVX]+)\b")
RE_NOTICE = re.compile(r"(?m)^\s*B\.\s*NOTICE\s*$")


def texte_pdf(chemin):
    """Texte d'un PDF EMA, mis en cache a cote du PDF."""
    cache_txt = chemin + ".txt"
    if os.path.exists(cache_txt) and os.path.getsize(cache_txt) > 0:
        return open(cache_txt, encoding="utf-8", errors="replace").read()
    try:
        r = subprocess.run(["pdftotext", chemin, "-"],
                           capture_output=True, timeout=180)
    except FileNotFoundError:
        raise SystemExit("pdftotext est absent : installer poppler-utils.")
    if r.returncode != 0:
        return ""
    t = r.stdout.decode("utf-8", "replace")
    open(cache_txt, "w", encoding="utf-8").write(t)
    return t


def decoupe_ema(t):
    """Separe le RCP (annexe I) de la notice patient (annexe III B).

    L'annexe II (conditions de fabrication) et l'etiquetage sont ecartes :
    administratifs, ils n'aident personne a se soigner et pesent lourd.
    """
    t = t.replace("\f", "\n")
    fin_rcp = len(t)
    for m in RE_ANNEXE.finditer(t):
        if m.group(1) == "II":
            fin_rcp = m.start()
            break
    m = RE_NOTICE.search(t)
    return t[:fin_rcp], (t[m.end():] if m else "")


def texte_ema_en_html(t, prefixe):
    lignes = [l.strip() for l in t.split("\n")]
    sortie, ancres, para, vus = [], [], [], {}

    def ferme():
        if para:
            s = " ".join(para).strip()
            if s:
                sortie.append(f"<p>{html.escape(s)}</p>")
            para.clear()

    i = 0
    while i < len(lignes):
        l = lignes[i]
        if not l:
            ferme()
            i += 1
            continue
        if l.isdigit() and len(l) <= 3:
            # numero de page du PDF : sinon il s'invite en tete de la notice.
            # Une vraie rubrique porte toujours un point ("1.", "4.1").
            i += 1
            continue
        m = NUMERO_RUBRIQUE.match(l)
        if m:
            j = i + 1
            while j < len(lignes) and not lignes[j]:
                j += 1
            libelle = lignes[j] if j < len(lignes) else ""
            if 3 <= len(libelle) <= 100 and any(c.isalpha() for c in libelle):
                ferme()
                num = m.group(1).rstrip(".")
                ident = ident_unique(f"{prefixe}-{num.replace('.', '-')}", vus)
                ancres.append((num, libelle, ident))
                sortie.append(f'<h3 id="{ident}"><span class="num">'
                              f"{html.escape(num)}</span> "
                              f"{html.escape(libelle)}</h3>")
                i = j + 1
                continue
        para.append(l)
        i += 1
    ferme()
    return "".join(sortie), ancres


def documents_ema(page):
    """(notice, rcp, ancres) depuis le PDF EMA cite par la page, si present."""
    u = url_ema(page)
    if not u:
        return "", "", []
    chemin = chemin_ema(u)
    if not os.path.exists(chemin):
        return "", "", []
    rcp_txt, notice_txt = decoupe_ema(texte_pdf(chemin))
    notice, a1 = texte_ema_en_html(notice_txt, "n")
    rcp, a2 = texte_ema_en_html(rcp_txt, "r")
    return notice, rcp, a1 + a2


# --------------------------------------------------------------------------
# habillage
# --------------------------------------------------------------------------

# Feuille partagee, ecrite une fois a la racine du ZIM : l'inliner dans chaque
# page ferait porter la meme feuille 16 000 fois.
CSS = """
:root{
  --fond:#FAFAF6; --carte:#F0F1EA; --encre:#14201B; --encre-pale:#5C6A63;
  --regle:#D4D8CE; --vert:#00734A; --alerte:#A32A17; --ambre:#7A5300; --lien:#1F5A7A;
}
@media (prefers-color-scheme:dark){
  :root{
    --fond:#121714; --carte:#1A211D; --encre:#E3E8E3; --encre-pale:#96A39B;
    --regle:#2E3831; --vert:#4FBE8B; --alerte:#E8836E; --ambre:#D9A441; --lien:#7FB6D4;
  }
}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{
  margin:0;background:var(--fond);color:var(--encre);
  font:16px/1.55 system-ui,-apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
}
.wrap{max-width:62rem;margin:0 auto;padding:1.5rem 1.25rem 4rem}
a{color:var(--lien)}
:focus-visible{outline:2px solid var(--vert);outline-offset:2px}

nav{display:flex;gap:1.5rem;border-bottom:1px solid var(--regle);margin-bottom:1.75rem;flex-wrap:wrap}
nav a{color:var(--encre-pale);text-decoration:none;padding:.6rem 0 .55rem;
  border-bottom:2px solid transparent;margin-bottom:-1px;font-size:.9375rem}
nav a:hover{color:var(--encre)}
nav a[aria-current]{color:var(--encre);border-bottom-color:var(--vert)}

h1{font-size:1.6rem;line-height:1.25;font-weight:600;margin:0 0 .35rem}
h2{font-size:1.1rem;font-weight:600;margin:2.5rem 0 .75rem;
   padding-bottom:.35rem;border-bottom:1px solid var(--regle)}
h3{font-size:1rem;font-weight:600;margin:1.75rem 0 .5rem}
h3 .num{color:var(--encre-pale);font-variant-numeric:tabular-nums;margin-right:.4rem}
.chapeau{color:var(--encre-pale);margin:0 0 1.5rem;font-size:.9375rem}

/* bandeau d'etat : la seule couleur forte de la page, et elle porte une
   information — autorisation retiree, medicament non commercialise. */
.etat{border-left:4px solid var(--vert);background:var(--carte);
  padding:.85rem 1rem;margin:0 0 1.5rem;font-size:.9375rem}
.etat.avert{border-left-color:var(--ambre)}
.etat.grave{border-left-color:var(--alerte)}
.etat strong{display:block;margin-bottom:.15rem}
.etat.avert strong{color:var(--ambre)}
.etat.grave strong{color:var(--alerte)}

.substances{font-size:1.05rem;margin:0 0 1.25rem}
.substances a{text-decoration:none;border-bottom:1px solid var(--regle)}
.substances a:hover{border-bottom-color:var(--lien)}

/* raccourcis vers les sections que l'on cherche en premier */
.reperes{display:flex;flex-wrap:wrap;gap:.5rem;margin:0 0 2rem;
  padding:.75rem 0;border-top:1px solid var(--regle);border-bottom:1px solid var(--regle)}
.reperes a{font-size:.875rem;text-decoration:none;padding:.25rem .6rem;
  border:1px solid var(--regle);border-radius:2px;color:var(--encre)}
.reperes a:hover{border-color:var(--vert);color:var(--vert)}

dl.identite{display:grid;grid-template-columns:auto 1fr;gap:.35rem 1.25rem;
  margin:0 0 1.5rem;font-size:.9375rem}
dl.identite dt{color:var(--encre-pale)}
dl.identite dd{margin:0}

.doc{max-width:42rem}
.doc p{margin:.65rem 0}
.doc table{width:100%;border-collapse:collapse;margin:1rem 0;font-size:.9375rem}
.doc td,.doc th{border:1px solid var(--regle);padding:.4rem .55rem;text-align:left;vertical-align:top}
.doc ul,.doc ol{padding-left:1.4rem}
.absent{color:var(--encre-pale);font-style:italic}

table.presentations{width:100%;border-collapse:collapse;font-size:.875rem;margin-top:.5rem}
table.presentations th{text-align:left;color:var(--encre-pale);font-weight:500;
  border-bottom:1px solid var(--regle);padding:.4rem .5rem}
table.presentations td{border-bottom:1px solid var(--regle);padding:.5rem;vertical-align:baseline}
table.presentations td.prix{white-space:nowrap;font-variant-numeric:tabular-nums;text-align:right}

/* index */
.lettres{display:flex;flex-wrap:wrap;gap:.3rem;margin:0 0 1.75rem}
.lettres a{text-decoration:none;padding:.3rem .55rem;border:1px solid var(--regle);
  border-radius:2px;color:var(--encre);font-size:.875rem;min-width:2rem;text-align:center}
.lettres a:hover{border-color:var(--vert)}
.lettres a[aria-current]{background:var(--vert);border-color:var(--vert);color:var(--fond)}
.filtre{width:100%;padding:.6rem .75rem;margin-bottom:1.25rem;background:var(--carte);
  color:var(--encre);border:1px solid var(--regle);border-radius:3px;font-size:1rem;font-family:inherit}
.filtre::placeholder{color:var(--encre-pale)}
table.liste{width:100%;border-collapse:collapse}
table.liste tr{border-bottom:1px solid var(--regle)}
table.liste tr:hover{background:var(--carte)}
table.liste td{padding:.55rem .5rem;vertical-align:baseline}
table.liste td.nom a{color:var(--encre);text-decoration:none}
table.liste td.nom a:hover{text-decoration:underline}
table.liste td.sub{color:var(--encre-pale);font-size:.875rem;width:40%;text-align:right}
table.liste tr.retire td.nom a{color:var(--encre-pale);text-decoration:line-through}
@media(max-width:38rem){table.liste td.sub{display:none}}
.compte{color:var(--encre-pale);font-size:.875rem;font-weight:400}
"""

JS = """
(function(){
  var f=document.getElementById('f'), t=document.getElementById('liste'), c=document.getElementById('n');
  if(!f||!t) return;
  f.hidden=false;
  f.addEventListener('input',function(){
    var v=f.value.trim().toLowerCase(), rows=t.tBodies[0].rows, n=0;
    for(var i=0;i<rows.length;i++){
      var ok=!v||rows[i].textContent.toLowerCase().indexOf(v)>-1;
      rows[i].hidden=!ok; if(ok) n++;
    }
    if(c) c.textContent=n;
  });
})();
"""

NAV = [("index.html", "Accueil"),
       ("noms-A.html", "Par nom"),
       ("substances-A.html", "Par substance")]

LETTRES = list("ABCDEFGHIJKLMNOPQRSTUVWXYZ") + ["0"]


def page(titre, corps, courant=None, avec_js=False):
    morceaux = []
    for h, l in NAV:
        cur = ' aria-current="page"' if h == courant else ""
        morceaux.append(f'<a href="{h}"{cur}>{html.escape(l)}</a>')
    liens = "".join(morceaux)
    script = '<script src="script.js"></script>' if avec_js else ""
    return ('<!DOCTYPE html><html lang="fr"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            f"<title>{html.escape(titre)}</title>"
            '<link rel="stylesheet" href="style.css"></head><body>'
            f'<div class="wrap"><nav>{liens}</nav>{corps}</div>{script}</body></html>')


def bande_lettres(prefixe, courante, presentes):
    liens = []
    for L in LETTRES:
        if L not in presentes:
            continue
        cur = ' aria-current="page"' if L == courante else ""
        lbl = "0-9" if L == "0" else L
        liens.append(f'<a href="{prefixe}-{L}.html"{cur}>{lbl}</a>')
    return f'<div class="lettres">{"".join(liens)}</div>'


def initiale(nom):
    """Lettre de classement, garantie presente dans LETTRES.

    On verifie l'appartenance a LETTRES plutot que isalpha() : une initiale
    hors A-Z creerait un groupe qui n'est affiche par aucune page d'index, et
    le medicament disparaitrait de la navigation sans la moindre erreur.
    """
    c = sans_accent(nom.strip().upper()[:1])
    return c if c in LETTRES else "0"


def png_croix(chemin, taille=48):
    """Illustration du ZIM : une croix, sur le vert des pharmacies francaises."""
    fond, trait = (250, 250, 246), (0, 115, 74)
    a, b = taille * 2 // 5, taille * 3 // 5
    lignes = []
    for y in range(taille):
        px = []
        for x in range(taille):
            px += list(trait if (a <= x < b or a <= y < b) else fond)
        lignes.append(b"\x00" + bytes(px))
    brut = b"".join(lignes)

    def bloc(tag, data):
        c = tag + data
        return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c))

    png = (b"\x89PNG\r\n\x1a\n"
           + bloc(b"IHDR", struct.pack(">IIBBBBB", taille, taille, 8, 2, 0, 0, 0))
           + bloc(b"IDAT", zlib.compress(brut))
           + bloc(b"IEND", b""))
    open(chemin, "wb").write(png)


# --------------------------------------------------------------------------
# fabrication des pages
# --------------------------------------------------------------------------

TYPE_GENERIQUE = {"0": "princeps", "1": "générique",
                  "2": "générique par complémentarité posologique",
                  "4": "générique substituable"}


def charge_referentiels():
    d = f"{CACHE}/data"
    spec = {}
    for l in lire_tsv(f"{d}/CIS_bdpm.txt"):
        cis = champ(l, 0)
        if not cis.isdigit():
            continue
        spec[cis] = {
            "nom": champ(l, 1), "forme": champ(l, 2), "voies": champ(l, 3),
            "statut": champ(l, 4), "procedure": champ(l, 5),
            "commercialisation": champ(l, 6), "date_amm": champ(l, 7),
            "statut_bdm": champ(l, 8), "titulaire": champ(l, 10),
            "surveillance": champ(l, 11),
        }
    compo, presentations, smr, cpd, gener = (defaultdict(list) for _ in range(5))
    for l in lire_tsv(f"{d}/CIS_COMPO_bdpm.txt"):
        compo[champ(l, 0)].append({"code": champ(l, 2), "substance": champ(l, 3),
                                   "dosage": champ(l, 4), "reference": champ(l, 5),
                                   "nature": champ(l, 6)})
    for l in lire_tsv(f"{d}/CIS_CIP_bdpm.txt"):
        presentations[champ(l, 0)].append(
            {"libelle": champ(l, 2), "statut": champ(l, 3), "etat": champ(l, 4),
             "cip13": champ(l, 6), "taux": champ(l, 8), "prix": champ(l, 9)})
    for l in lire_tsv(f"{d}/CIS_HAS_SMR_bdpm.txt"):
        smr[champ(l, 0)].append({"motif": champ(l, 2), "date": champ(l, 3),
                                 "valeur": champ(l, 4), "libelle": champ(l, 5)})
    for l in lire_tsv(f"{d}/CIS_CPD_bdpm.txt"):
        cpd[champ(l, 0)].append(champ(l, 1))
    for l in lire_tsv(f"{d}/CIS_GENER_bdpm.txt"):
        gener[champ(l, 2)].append({"groupe": champ(l, 1),
                                   "type": TYPE_GENERIQUE.get(champ(l, 3), "")})
    return spec, compo, presentations, smr, cpd, gener


def bandeau_etat(s, conditions):
    """Ce qu'il faut savoir avant tout le reste : ce medicament est-il encore
    autorise, encore commercialise, et sa delivrance est-elle encadree ?"""
    statut, comm = s["statut"], s["commercialisation"]
    retire = any(m in statut.lower()
                 for m in ("retir", "suspend", "abrog", "archiv"))
    if retire:
        classe, titre = "grave", f"Autorisation non active — {statut}"
        corps = ("Ce médicament n'est plus couvert par une autorisation de mise "
                 "sur le marché en vigueur. Les informations ci-dessous sont "
                 "conservées à titre documentaire.")
    elif "non commercialis" in comm.lower():
        classe, titre = "avert", "Non commercialisé"
        corps = ("Autorisation active, mais le médicament n'est pas commercialisé "
                 "à la date de cette archive.")
    else:
        classe, titre = "", "Autorisé et commercialisé"
        corps = f"{statut}. {comm}."
    if s["surveillance"].strip().lower().startswith("oui"):
        corps += (" Ce médicament fait l'objet d'une surveillance renforcée.")
        classe = classe or "avert"
    if conditions:
        corps += " Délivrance : " + " ; ".join(conditions) + "."
    cl = f" {classe}" if classe else ""
    return (f'<div class="etat{cl}"><strong>{html.escape(titre)}</strong>'
            f"{html.escape(corps)}</div>")


def tableau_presentations(lignes):
    if not lignes:
        return '<p class="absent">Aucune présentation enregistrée.</p>'
    corps = []
    for p in lignes:
        prix = p["prix"].replace(",", ",") if p["prix"] else "—"
        taux = p["taux"] or "—"
        corps.append(f"<tr><td>{html.escape(p['libelle'])}</td>"
                     f"<td>{html.escape(p['cip13'])}</td>"
                     f"<td>{html.escape(taux)}</td>"
                     f'<td class="prix">{html.escape(prix)}</td></tr>')
    return ('<table class="presentations"><thead><tr><th>Présentation</th>'
            "<th>Code CIP13</th><th>Remboursement</th><th>Prix</th></tr></thead>"
            f'<tbody>{"".join(corps)}</tbody></table>')


def page_medicament(cis, s, compo, presentations, smr, cpd, gener, cache_page):
    notice = rcp = ""
    ancres = []
    origine = ""
    if cache_page:
        notice, a1 = titre_sections(
            extrait_panneau(cache_page, "tabpanel-notice-panel"), "n")
        rcp, a2 = titre_sections(
            extrait_panneau(cache_page, "tabpanel-rcp-panel"), "r")
        ancres = a1 + a2
        if not notice.strip() and not rcp.strip():
            # autorisation europeenne : les textes sont chez l'EMA
            notice, rcp, ancres = documents_ema(cache_page)
            if notice.strip() or rcp.strip():
                origine = ("Textes issus de l'information produit publiée par "
                           "l'Agence européenne des médicaments (EMA).")

    actives = [c for c in compo if c["nature"] == "SA"]
    liens_sub = []
    for c in actives:
        cible = f"substances-{initiale(c['substance'])}.html#sub-{c['code']}"
        dose = f" {c['dosage']}" if c["dosage"] else ""
        liens_sub.append(f'<a href="{cible}">{html.escape(c["substance"])}</a>'
                         f"{html.escape(dose)}")
    bloc_sub = (f'<p class="substances">{" · ".join(liens_sub)}</p>'
                if liens_sub else "")

    creuses = ancres_creuses(notice) | ancres_creuses(rcp)
    reperes = reperes_utiles([a for a in ancres if a[2] not in creuses]
                             or ancres)
    bloc_reperes = ""
    if reperes:
        bloc_reperes = '<div class="reperes">' + "".join(
            f'<a href="#{i}">{html.escape(e)}</a>' for e, i in reperes) + "</div>"

    identite = [("Forme", s["forme"]), ("Voie d'administration", s["voies"]),
                ("Titulaire", s["titulaire"]),
                ("Date d'autorisation", s["date_amm"]),
                ("Procédure", s["procedure"]), ("Code CIS", cis)]
    if gener:
        g = gener[0]
        identite.append(("Groupe générique",
                         f"{g['groupe']} ({g['type']})" if g["type"] else g["groupe"]))
    dl = "".join(f"<dt>{html.escape(k)}</dt><dd>{html.escape(v or '—')}</dd>"
                 for k, v in identite)

    parts = [f'<h1>{html.escape(s["nom"])}</h1>',
             bandeau_etat(s, cpd), bloc_sub, bloc_reperes,
             f'<dl class="identite">{dl}</dl>']

    if compo:
        rows = "".join(
            f"<tr><td>{html.escape(c['substance'])}</td>"
            f"<td>{html.escape(c['dosage'] or '—')}</td>"
            f"<td>{html.escape(c['reference'] or '—')}</td>"
            f"<td>{'active' if c['nature'] == 'SA' else 'fraction thérapeutique'}</td></tr>"
            for c in compo)
        parts.append('<h2 id="composition">Composition</h2>'
                     '<table class="presentations"><thead><tr><th>Substance</th>'
                     "<th>Dosage</th><th>Pour</th><th>Nature</th></tr></thead>"
                     f'<tbody>{rows}</tbody></table>')

    parts.append('<h2 id="notice">Notice patient</h2>')
    if origine:
        parts.append(f'<p class="chapeau">{html.escape(origine)}</p>')
    parts.append(f'<div class="doc">{notice}</div>' if notice.strip()
                 else '<p class="absent">Notice non disponible pour cette '
                      "spécialité dans la base publique.</p>")

    parts.append('<h2 id="rcp">Résumé des caractéristiques du produit</h2>')
    parts.append(f'<div class="doc">{rcp}</div>' if rcp.strip()
                 else '<p class="absent">RCP non disponible pour cette '
                      "spécialité dans la base publique.</p>")

    parts.append('<h2 id="presentations">Présentations et remboursement</h2>')
    parts.append(tableau_presentations(presentations))

    if smr:
        lignes = "".join(
            f"<tr><td>{html.escape(a['valeur'])}</td>"
            f"<td>{html.escape(a['motif'])}</td>"
            f"<td>{html.escape(a['date'])}</td></tr>" for a in smr[:8])
        parts.append('<h2 id="has">Service médical rendu (avis HAS)</h2>'
                     '<table class="presentations"><thead><tr><th>SMR</th>'
                     "<th>Motif</th><th>Date</th></tr></thead>"
                     f'<tbody>{lignes}</tbody></table>')

    # on rend les manques explicitement : les deduire en cherchant une phrase
    # dans le HTML produit se casse au premier changement de formulation.
    return page(s["nom"], "".join(parts)), not notice.strip(), not rcp.strip()


# --------------------------------------------------------------------------
# index
# --------------------------------------------------------------------------

def liste_medicaments(entrees, filtrable=False):
    """entrees : (nom, href, substances, retire)

    L'id n'est pose que sur la liste que le champ de filtre pilote : les pages
    de substances empilent une liste par substance, elles ne peuvent pas toutes
    porter le meme id.
    """
    lignes = []
    for nom, href, subs, retire in entrees:
        cl = ' class="retire"' if retire else ""
        lignes.append(f"<tr{cl}><td class=\"nom\"><a href=\"{href}\">"
                      f'{html.escape(nom)}</a></td>'
                      f'<td class="sub">{html.escape(subs)}</td></tr>')
    idattr = ' id="liste"' if filtrable else ""
    return (f'<table class="liste"{idattr}>'
            f'<tbody>{"".join(lignes)}</tbody></table>')


def phase_build():
    os.makedirs(OUT, exist_ok=True)
    spec, compo, presentations, smr, cpd, gener = charge_referentiels()

    cis_liste = sorted(spec, key=lambda c: sans_accent(spec[c]["nom"].upper()))
    if LIMIT:
        cis_liste = cis_liste[:LIMIT]
    print(f"{len(cis_liste)} specialites a produire")

    par_lettre = defaultdict(list)
    par_substance = defaultdict(list)
    noms_substances = {}
    sans_notice = sans_rcp = sans_page = 0

    for i, cis in enumerate(cis_liste, 1):
        s = spec[cis]
        chemin = chemin_cache(cis)
        contenu = ""
        if os.path.exists(chemin):
            try:
                with gzip.open(chemin, "rb") as f:
                    contenu = f.read().decode("utf-8", "replace")
            except Exception:
                contenu = ""
        if not contenu:
            sans_page += 1

        corps, manque_notice, manque_rcp = page_medicament(
            cis, s, compo[cis], presentations[cis], smr[cis], cpd[cis],
            gener[cis], contenu)
        sans_notice += manque_notice
        sans_rcp += manque_rcp

        href = f"med_{cis}.html"
        with open(f"{OUT}/{href}", "w", encoding="utf-8") as f:
            f.write(corps)

        actives = [c for c in compo[cis] if c["nature"] == "SA"]
        libelle_sub = ", ".join(dict.fromkeys(c["substance"] for c in actives))
        retire = any(m in s["statut"].lower()
                     for m in ("retir", "suspend", "abrog", "archiv"))
        entree = (s["nom"], href, libelle_sub, retire)
        par_lettre[initiale(s["nom"])].append(entree)
        for c in actives:
            par_substance[c["code"]].append(entree)
            noms_substances[c["code"]] = c["substance"]

        if i % 500 == 0 or i == len(cis_liste):
            print(f"  {i}/{len(cis_liste)} pages", end="\r", flush=True)
    print()

    # --- par nom ---
    presentes = {L for L in LETTRES if par_lettre.get(L)}
    for L in LETTRES:
        entrees = sorted(par_lettre.get(L, []),
                         key=lambda e: sans_accent(e[0].upper()))
        if not entrees and L in presentes:
            continue
        titre = "Chiffres" if L == "0" else f"Lettre {L}"
        corps = (f'<h1>Médicaments — {html.escape(titre)}</h1>'
                 f'<p class="chapeau"><span id="n">{len(entrees)}</span> '
                 "spécialités. Les noms barrés ne sont plus autorisés.</p>"
                 + bande_lettres("noms", L, presentes)
                 + '<input class="filtre" id="f" type="search" hidden '
                   'placeholder="Filtrer dans cette lettre" '
                   'aria-label="Filtrer les médicaments">'
                 + (liste_medicaments(entrees, filtrable=True) if entrees
                    else '<p class="absent">Aucune spécialité.</p>'))
        with open(f"{OUT}/noms-{L}.html", "w", encoding="utf-8") as f:
            f.write(page(f"Médicaments — {titre}", corps,
                         courant="noms-A.html", avec_js=True))

    # --- par substance active ---
    subs_par_lettre = defaultdict(list)
    for code, nom in noms_substances.items():
        subs_par_lettre[initiale(nom)].append((nom, code))
    presentes_sub = {L for L in LETTRES if subs_par_lettre.get(L)}
    for L in LETTRES:
        subs = sorted(subs_par_lettre.get(L, []),
                      key=lambda x: sans_accent(x[0].upper()))
        if not subs and L in presentes_sub:
            continue
        blocs = []
        for nom, code in subs:
            entrees = sorted(par_substance[code],
                             key=lambda e: sans_accent(e[0].upper()))
            blocs.append(f'<h2 id="sub-{html.escape(code)}">{html.escape(nom)} '
                         f'<span class="compte">{len(entrees)}</span></h2>'
                         + liste_medicaments(entrees))
        titre = "Chiffres" if L == "0" else f"Lettre {L}"
        corps = (f'<h1>Substances actives — {html.escape(titre)}</h1>'
                 f'<p class="chapeau">{len(subs)} substances. '
                 "Utile quand on a la boîte mais pas le nom commercial.</p>"
                 + bande_lettres("substances", L, presentes_sub)
                 + ("".join(blocs) if blocs
                    else '<p class="absent">Aucune substance.</p>'))
        with open(f"{OUT}/substances-{L}.html", "w", encoding="utf-8") as f:
            f.write(page(f"Substances actives — {titre}", corps,
                         courant="substances-A.html"))

    # --- accueil ---
    # compter sur cis_liste et non sur tout le fichier : sinon un build
    # partiel (LIMIT) afficherait le total de la base, ce qui est faux.
    total_pres = sum(len(presentations[c]) for c in cis_liste)
    corps = (
        "<h1>Base de données publique des médicaments</h1>"
        '<p class="chapeau">Copie hors-ligne de la base officielle française '
        "publiée par l'ANSM, avec la HAS et l'UNCAM.</p>"
        '<div class="etat avert"><strong>Ce que ce document est, et ce qu\'il '
        "n'est pas</strong>Ce sont les textes officiels (notice patient et "
        "résumé des caractéristiques du produit) tels que publiés à la date "
        "d'extraction. C'est une référence, pas une prescription : elle ne "
        "remplace ni un avis médical, ni un pharmacien.</div>"
        f'<dl class="identite">'
        f"<dt>Spécialités</dt><dd>{len(cis_liste)}</dd>"
        f"<dt>Substances actives</dt><dd>{len(noms_substances)}</dd>"
        f"<dt>Présentations</dt><dd>{total_pres}</dd>"
        f"<dt>Date d'extraction</dt><dd>{time.strftime('%d/%m/%Y')}</dd>"
        f"<dt>Source</dt><dd>base-donnees-publique.medicaments.gouv.fr</dd>"
        f"<dt>Licence</dt><dd>Licence ouverte (État français)</dd></dl>"
        "<h2>Chercher par nom commercial</h2>"
        + bande_lettres("noms", None, presentes) +
        "<h2>Chercher par substance active</h2>"
        '<p class="chapeau">Quand on a la plaquette sans la boîte, ou pour '
        "trouver un équivalent.</p>"
        + bande_lettres("substances", None, presentes_sub) +
        "<h2>Chercher par mot</h2>"
        '<p class="chapeau">La recherche de Kiwix porte sur le texte intégral '
        "des notices et des RCP : un symptôme, une contre-indication ou un nom "
        "de substance y trouvent leurs médicaments.</p>")
    with open(f"{OUT}/index.html", "w", encoding="utf-8") as f:
        f.write(page("Base de données publique des médicaments", corps,
                     courant="index.html"))

    open(f"{OUT}/style.css", "w", encoding="utf-8").write(CSS)
    open(f"{OUT}/script.js", "w", encoding="utf-8").write(JS)
    if not os.path.exists(f"{OUT}/favicon.png"):
        png_croix(f"{OUT}/favicon.png")

    print(f"\n{len(cis_liste)} specialites, {len(noms_substances)} substances")
    print(f"  {sans_page} sans page recuperee, {sans_notice} sans notice, "
          f"{sans_rcp} sans RCP")
    print(f"Source ZIM prete dans {OUT}")


def main():
    etape = sys.argv[1] if len(sys.argv) > 1 else ""
    if etape == "fetch":
        phase_fetch()
    elif etape == "build":
        phase_build()
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main()
