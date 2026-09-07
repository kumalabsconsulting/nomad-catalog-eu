#!/usr/bin/env python3
"""Tests de non-regression de build_bdpm_zim.py.

Chaque test porte le nom d'une panne REELLEMENT observee pendant la
construction du ZIM des medicaments, et son docstring decrit le symptome tel
qu'il s'est presente. Ce fichier n'est pas une couverture de code : c'est la
memoire des erreurs deja payees, pour ne pas les repayer.

La plupart de ces pannes etaient silencieuses. Elles ne levaient aucune
exception : elles produisaient une archive d'apparence correcte, dont une
partie du contenu etait vide, mal encodee ou inatteignable. C'est ce qui les
rend chers a retrouver, et c'est pourquoi elles sont figees ici.

    python3 -m unittest discover -s builders/bdpm -p 'test_*.py' -v

Aucun acces reseau : les rares appels sortants sont remplaces par des doubles.
"""
import importlib.util
import os
import re
import tempfile
import unittest
from collections import Counter
from unittest import mock

ICI = os.path.dirname(os.path.abspath(__file__))


def charge_module():
    spec = importlib.util.spec_from_file_location(
        "build_bdpm_zim", os.path.join(ICI, "build_bdpm_zim.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


b = charge_module()


# --------------------------------------------------------------------------
# lecture des fichiers source
# --------------------------------------------------------------------------

class LectureDesSources(unittest.TestCase):

    def test_encodage_mixte_entre_fichiers_de_la_meme_source(self):
        """CIS_CIP_bdpm.txt est en UTF-8, les cinq autres en Windows-1252.

        Symptome : en forcant un encodage unique, une moitie du corpus sortait
        avec 'comprime' correct et l'autre avec 'comprimA©'. Rien ne plantait,
        l'archive partait avec des milliers de mots abimes.
        """
        with tempfile.TemporaryDirectory() as d:
            latin = os.path.join(d, "latin.txt")
            utf8 = os.path.join(d, "utf8.txt")
            open(latin, "wb").write("60002283\tcomprimé pelliculé\n"
                                    .encode("cp1252"))
            open(utf8, "wb").write("60002283\tcomprimé pelliculé\n"
                                   .encode("utf-8"))
            for chemin in (latin, utf8):
                lignes = b.lire_tsv(chemin)
                self.assertEqual(lignes[0][1], "comprimé pelliculé",
                                 f"encodage mal detecte dans {chemin}")

    def test_un_echec_de_recuperation_dit_son_motif(self):
        """L'EMA bride le debit sur une rafale de plus d'un millier de PDF :
        819 documents sur 1 123 ont echoue d'un coup.

        Symptome : le compteur annoncait '819 echecs' sans dire pourquoi. Une
        source tombee, une source qui bride et un format qui change appellent
        trois reactions differentes — sans motif, on ne peut pas choisir.
        """
        import urllib.error
        erreur = urllib.error.HTTPError("http://x", 429, "Too Many Requests",
                                        {}, None)
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.object(b, "CACHE", d), \
                 mock.patch.object(b.time, "sleep"), \
                 mock.patch("urllib.request.urlopen", side_effect=erreur):
                res = b.recupere_ema("https://www.ema.europa.eu/x_fr.pdf")
        self.assertEqual(res, "echec HTTP 429")

    def test_un_bridage_declenche_une_longue_pause_pas_une_relance_rapide(self):
        """Symptome : face au 429 du CDN de l'EMA, des reessais rapproches
        consommaient les essais sans rien obtenir. Un refus pour cause de debit
        se traite par l'attente, pas par l'insistance."""
        import urllib.error
        erreur = urllib.error.HTTPError("http://x", 429, "Too Many Requests",
                                        {}, None)
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.object(b, "CACHE", d), \
                 mock.patch.object(b.time, "sleep") as dors, \
                 mock.patch("urllib.request.urlopen", side_effect=erreur):
                b.recupere_ema("https://www.ema.europa.eu/x_fr.pdf")
        attentes = [c.args[0] for c in dors.call_args_list]
        self.assertTrue(all(a == b.PAUSE_429 for a in attentes),
                        f"pauses inattendues sur un 429 : {attentes}")

    def test_le_bilan_regroupe_les_echecs_par_motif(self):
        """Le bilan doit separer les motifs, sinon un blocage massif se noie
        dans quelques erreurs reseau anodines."""
        compte = {"ok": 300, "cache": 4, "echec HTTP 429": 800,
                  "echec timeout": 19}
        self.assertEqual(b.motifs_echec(compte),
                         {"HTTP 429": 800, "timeout": 19})

    def test_fichier_source_servi_vide_arrete_la_construction(self):
        """CIS_RCP.zip repond HTTP 200 avec 0 octet.

        Symptome : la source peut servir un fichier vide sans erreur HTTP. Sans
        garde, on construisait une archive amputee en croyant tout avoir.
        """
        faux = mock.MagicMock()
        faux.__enter__.return_value.read.return_value = b""
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.object(b, "CACHE", d), \
                 mock.patch("urllib.request.urlopen", return_value=faux):
                with self.assertRaises(SystemExit):
                    b.telecharge_fichiers()


# --------------------------------------------------------------------------
# extraction du contenu des pages
# --------------------------------------------------------------------------

class ExtractionDesPages(unittest.TestCase):

    def test_le_panneau_extrait_est_bien_celui_demande(self):
        """Les panneaux sont imbriques dans des div : compter les balises
        ouvrantes et fermantes est indispensable, sinon l'extraction deborde
        sur le pied de page du site."""
        page = ('<div id="autre">bruit</div>'
                '<div id="cible"><div><p>garde</p></div></div>'
                '<div id="apres">bruit</div>')
        frag = b.extrait_panneau(page, "cible")
        self.assertIn("garde", frag)
        self.assertNotIn("bruit", frag)

    def test_le_decor_du_site_ne_part_pas_dans_le_zim(self):
        """Le site est habille par le systeme de design de l'Etat.

        Symptome : sans filtrage, chaque fiche emportait scripts, boutons
        d'impression et menu lateral — soit la moitie du theme DSFR repetee
        15 859 fois.
        """
        page = ('<div id="c"><script>a=1</script><nav>menu</nav>'
                '<div class="fr-no-print">Imprimer</div>'
                '<div class="fr-sidemenu">sommaire lateral</div>'
                '<p>vrai contenu</p></div>')
        frag = b.extrait_panneau(page, "c")
        self.assertIn("vrai contenu", frag)
        for indesirable in ("a=1", "menu", "Imprimer", "sommaire lateral"):
            self.assertNotIn(indesirable, frag)

    def test_medicament_europeen_reconnu_par_son_lien_ema(self):
        """14 % des specialites (2 354) sont autorisees par la Commission
        europeenne : la BDPM n'heberge alors ni notice ni RCP, sa page ne
        porte qu'un renvoi vers un PDF de l'EMA.

        Symptome : sans suivre ce lien, insulines, anticoagulants et oncologie
        figuraient dans l'index mais s'ouvraient vides.
        """
        page = ('<a href="https://www.ema.europa.eu/fr/documents/'
                'product-information/abasaglar-epar-product-information_fr.pdf">'
                'Vers le RCP et la notice</a>')
        self.assertTrue(b.url_ema(page).endswith("_fr.pdf"))
        self.assertIsNone(b.url_ema("<p>aucun lien</p>"))


# --------------------------------------------------------------------------
# mise en forme des documents EMA
# --------------------------------------------------------------------------

RCP_ET_NOTICE = """ANNEXE I
RESUME DES CARACTERISTIQUES DU PRODUIT

4.1

Indications therapeutiques

marqueur-rcp

ANNEXE II
conditions de fabrication

ANNEXE III
A. ETIQUETAGE
mentions sur l emballage

B. NOTICE

1.

Qu est-ce que ce medicament

marqueur-notice
"""


class DocumentsEMA(unittest.TestCase):

    def test_rcp_et_notice_sont_separes_aux_bonnes_annexes(self):
        """Le RCP est l'annexe I, la notice l'annexe III B. L'annexe II
        (fabrication) et l'etiquetage sont administratifs et ecartes."""
        rcp, notice = b.decoupe_ema(RCP_ET_NOTICE)
        self.assertIn("marqueur-rcp", rcp)
        self.assertNotIn("marqueur-notice", rcp)
        self.assertNotIn("conditions de fabrication", rcp)
        self.assertIn("marqueur-notice", notice)

    def test_numero_de_page_du_pdf_nest_pas_pris_pour_une_rubrique(self):
        """Symptome : un '48' orphelin s'affichait en tete de notice.

        Une rubrique porte toujours un point ('1.', '4.1'). Une ligne reduite a
        un entier nu est un numero de page.
        """
        html, _ = b.texte_ema_en_html("48\n\nVeuillez lire la notice.\n", "n")
        self.assertNotIn("<p>48</p>", html)
        self.assertIn("Veuillez lire la notice.", html)

    def test_le_numero_et_son_libelle_sont_dans_deux_blocs_distincts(self):
        """Le PDF ne pose pas '4.1 Indications' sur une ligne : il pose '4.1',
        puis le libelle plus loin. Chercher un titre d'un seul tenant ne
        trouvait aucune rubrique."""
        html, ancres = b.texte_ema_en_html(
            "4.1\n\nIndications therapeutiques\n\ntexte\n", "r")
        self.assertIn('<h3 id="r-4-1">', html)
        self.assertEqual(ancres[0][1], "Indications therapeutiques")


# --------------------------------------------------------------------------
# ancres et navigation
# --------------------------------------------------------------------------

class AncresEtNavigation(unittest.TestCase):

    def test_rcp_repete_ne_produit_pas_didentifiants_en_double(self):
        """Un document EMA repete son RCP pour chaque presentation (stylo,
        cartouche, flacon).

        Symptome : la meme rubrique sortait plusieurs fois avec le meme id.
        HTML invalide, et les raccourcis renvoyaient toujours au premier bloc.
        """
        frag = ("<p>4. INFORMATIONS CLINIQUES</p><p>a</p>"
                "<p>4.2 Posologie</p><p>b</p>"
                "<p>4. INFORMATIONS CLINIQUES</p><p>c</p>")
        html, _ = b.titre_sections(frag, "r")
        ids = re.findall(r'id="([^"]+)"', html)
        doubles = [i for i, n in Counter(ids).items() if n > 1]
        self.assertFalse(doubles, f"identifiants en double : {doubles}")

    def test_le_suffixe_dunicite_ne_percute_pas_une_sous_rubrique(self):
        """Symptome : suffixer par un chiffre donnait 'r-4-2' pour la deuxieme
        occurrence de la rubrique 4, ce qui entrait en collision avec la vraie
        rubrique 4.2. D'ou le suffixe 'bis'."""
        vus = {}
        premier = b.ident_unique("r-4", vus)
        second = b.ident_unique("r-4", vus)
        sous_rubrique = b.ident_unique("r-4-2", vus)
        self.assertEqual(premier, "r-4")
        self.assertNotEqual(second, sous_rubrique)

    def test_le_sommaire_ne_capte_pas_les_raccourcis(self):
        """Une notice EMA s'ouvre sur 'Que contient cette notice ?' suivi de
        la liste de ses rubriques.

        Symptome : les raccourcis en haut de fiche renvoyaient vers cette table
        des matieres au lieu du texte cherche.
        """
        frag = ('<h3 id="n-1">Effets indesirables</h3>'
                '<h3 id="n-4">Effets indesirables</h3><p>du vrai texte</p>')
        creuses = b.ancres_creuses(frag)
        self.assertIn("n-1", creuses)
        self.assertNotIn("n-4", creuses)


# --------------------------------------------------------------------------
# page d'un medicament, de bout en bout
# --------------------------------------------------------------------------

SPECIALITE = {
    "nom": "TESTOL 500 mg, comprimé", "forme": "comprimé", "voies": "orale",
    "statut": "Autorisation active", "procedure": "Procédure nationale",
    "commercialisation": "Commercialisée", "date_amm": "01/01/2000",
    "statut_bdm": "", "titulaire": "LABO TEST", "surveillance": "Non",
}

PAGE_CACHE = (
    '<div id="tabpanel-notice-panel">'
    '<p>1. Qu est-ce que TESTOL</p><p>Un medicament d essai.</p>'
    '<p>4. Effets indesirables</p><p>Rares.</p></div>'
    '<div id="tabpanel-rcp-panel">'
    '<p>4.1 Indications therapeutiques</p><p>Essais.</p>'
    '<p>4.3 Contre-indications</p><p>Allergie connue.</p></div>')


def fabrique_page(**surcharge):
    s = dict(SPECIALITE, **surcharge)
    return b.page_medicament(
        "12345678", s,
        [{"code": "42215", "substance": "TESTINE", "dosage": "500 mg",
          "reference": "un comprimé", "nature": "SA"}],
        [{"libelle": "boîte de 10", "statut": "Présentation active",
          "etat": "Déclaration de commercialisation", "cip13": "3400900000000",
          "taux": "65%", "prix": "2,10"}],
        [], [], [], PAGE_CACHE)


class PageMedicament(unittest.TestCase):

    def test_aucune_ancre_ne_pointe_dans_le_vide(self):
        """Garde-fou global : tout raccourci doit trouver sa cible dans la
        page. C'est ce controle qui a revele les identifiants en double."""
        html, _, _ = fabrique_page()
        cibles = set(re.findall(r'id="([^"]+)"', html))
        brisees = [a for a in re.findall(r'href="#([^"]+)"', html)
                   if a not in cibles]
        self.assertFalse(brisees, f"ancres brisees : {brisees}")

    def test_les_manques_sont_rendus_explicitement(self):
        """Symptome : les compteurs 'sans notice' / 'sans RCP' cherchaient une
        phrase dans le HTML produit. Une simple correction de formulation les
        a fait mentir sans rien casser d'autre."""
        _, manque_notice, manque_rcp = fabrique_page()
        self.assertFalse(manque_notice)
        self.assertFalse(manque_rcp)
        _, sans_notice, sans_rcp = b.page_medicament(
            "12345678", SPECIALITE, [], [], [], [], [], "")
        self.assertTrue(sans_notice)
        self.assertTrue(sans_rcp)

    def test_une_autorisation_retiree_est_signalee_comme_grave(self):
        """Sur une reference medicale, un medicament dont l'AMM est retiree ou
        suspendue doit se voir au premier coup d'oeil, pas se lire comme les
        autres."""
        html, _, _ = fabrique_page(statut="Autorisation retirée")
        self.assertIn('class="etat grave"', html)
        html, _, _ = fabrique_page(commercialisation="Non commercialisée")
        self.assertIn('class="etat avert"', html)

    def test_le_texte_visible_est_du_francais_accentue(self):
        """Symptome : les libelles etaient ecrits sans accents comme les
        commentaires du script. Sur une reference medicale francaise destinee
        a etre lue, ce n'est pas acceptable."""
        html, _, _ = fabrique_page()
        for attendu in ("Résumé des caractéristiques", "Présentations",
                        "Procédure", "Autorisé et commercialisé"):
            self.assertIn(attendu, html)


# --------------------------------------------------------------------------
# classement dans les index
# --------------------------------------------------------------------------

class ClassementDesIndex(unittest.TestCase):

    def test_les_accents_ne_creent_pas_une_lettre_a_part(self):
        """Sinon 'ÉPHYNAL' atterrit dans une lettre fantome au lieu du E, et
        reste introuvable par la navigation."""
        self.assertEqual(b.initiale("ÉPHYNAL"), "E")
        self.assertEqual(b.initiale("Amoxicilline"), "A")

    def test_un_nom_commencant_par_un_chiffre_a_sa_place(self):
        """'5-FLUORO-URACILE' n'a pas d'initiale alphabetique : sans bac a
        chiffres, il disparaissait de tous les index."""
        self.assertEqual(b.initiale("5-FLUORO-URACILE"), "0")

    def test_toute_initiale_possible_est_affichable(self):
        """La panne de fond derriere le cas des accents : une initiale hors
        A-Z cree un groupe qu'aucune page d'index n'affiche, et le medicament
        sort de la navigation sans lever la moindre erreur. Le classement doit
        donc etre clos sur LETTRES, quel que soit le nom recu."""
        for nom in ("ÉPHYNAL", "Œstrogel", "5-FLUORO-URACILE", "( sans nom )",
                    "ΑΛΦΑ", "东风", ""):
            self.assertIn(b.initiale(nom), b.LETTRES, f"initiale hors index : {nom!r}")

    def test_le_tri_place_les_accents_a_leur_lettre(self):
        """Symptome jumeau : 'É' etant apres 'Z' en codage, ÉPHYNAL se triait
        en fin de liste au lieu de voisiner avec EPHEDRINE."""
        noms = ["ZYRTEC", "ÉPHYNAL", "EPHEDRINE", "AMOXICILLINE"]
        tries = sorted(noms, key=lambda n: b.sans_accent(n.upper()))
        self.assertEqual(tries,
                         ["AMOXICILLINE", "EPHEDRINE", "ÉPHYNAL", "ZYRTEC"])


# --------------------------------------------------------------------------
# contraintes de l'outillage, verifiees sur la documentation
# --------------------------------------------------------------------------

class ContraintesZimwriterfs(unittest.TestCase):

    def test_le_titre_documente_tient_dans_30_caracteres(self):
        """Symptome : zimwriterfs refuse un titre de plus de 30 caracteres, ne
        produit aucun fichier, et ne le dit qu'a la toute fin — apres tout le
        temps de compression. Le test porte sur la commande du README, qui est
        ce qu'on rejoue reellement."""
        readme = open(os.path.join(ICI, "README.md"), encoding="utf-8").read()
        bloc = readme.split("build_bdpm_zim.py build", 1)[1]
        titres = re.findall(r"-t '([^']+)'", bloc)
        self.assertTrue(titres, "aucun titre trouve dans la commande du README")
        self.assertLessEqual(len(titres[0]), 30,
                             f"titre trop long : {titres[0]!r}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
