# Limites et pièges rencontrés

Ce document est un **retour d'expérience**, pas un guide théorique. Chaque
point ci-dessous correspond à une panne réellement rencontrée en fabriquant des
archives ZIM francophones pour [Project NOMAD](https://github.com/Crosstalk-Solutions/project-nomad),
avec le symptôme tel qu'il s'est présenté.

Le fil conducteur : **presque aucune de ces pannes ne lève d'erreur.** Elles
produisent une archive d'apparence correcte, dont une partie du contenu est
vide, mal encodée ou inatteignable. C'est ce qui les rend chères à retrouver —
et c'est la raison d'être de cette page. Sur un kit destiné à servir quand on
n'a plus internet, la panne se découvre au pire moment.

---

## 1. Le catalogue Kiwix francophone

Le manque principal est décrit dans le [README](../README.md) : **aucune
ressource de survie en français**, et quelques dizaines de mégaoctets sur
l'agriculture, là où l'équivalent anglophone offre respectivement 14,6 Go et
9,2 Go. Ce n'est pas un défaut de ce dépôt, c'est un manque en amont.

Deux pièges de manipulation du catalogue :

- Le flux OPDS expose les archives sous forme de **métaliens**
  (`.zim.meta4`). Le fichier `.zim` direct est la même URL sans ce suffixe.
- Les éléments du flux sont en **Atom non préfixé** : chercher `d:language`
  ne renvoie rien, il faut interroger l'espace de noms Atom.

Et une limite de fond : Kiwix republie ses archives chaque mois avec une
nouvelle date dans le nom de fichier, et **retire régulièrement les anciennes
versions de son miroir**. Un catalogue figé accumule donc des liens morts sans
que personne ne s'en aperçoive — d'où la vérification automatique de ce dépôt.

---

## 2. Fabriquer une archive nationale : le cas de la base des médicaments

Project NOMAD embarque depuis la version 1.34 une « Medication Reference »
fondée sur les *drug labels* de la **FDA**. Elle est utile, mais américaine :
noms commerciaux, dosages et présentations diffèrent. Elle ne doit pas servir à
établir une posologie en Europe.

L'équivalent français existe et est en licence ouverte : la **Base de Données
Publique des Médicaments**, opérée par l'ANSM avec la HAS et l'UNCAM. En
fabriquer une archive ZIM a révélé quatre pièges, dont un majeur.

### 2.1 Un fichier peut être servi vide, avec un code HTTP 200

`CIS_RCP.zip` — l'archive des monographies, le cœur du sujet — répond
**HTTP 200 avec 0 octet**, et n'est plus référencé sur la page de
téléchargement. Il n'existe plus de récupération en masse des monographies :
elles doivent être prises page par page.

> **La leçon générale** : ne jamais déduire d'un code 200 qu'on a reçu quelque
> chose. Vérifier la taille, et faire échouer la construction si un fichier
> source est vide — sinon on livre une archive amputée en croyant tout avoir.

### 2.2 Un dump d'archive n'est pas une source à jour

Un `CIS_RCP.zip` subsiste sur un portail de données ouvertes, et il se
télécharge parfaitement. Il **date de mai 2022**.

Nous l'avons écarté volontairement. Sur une référence médicale destinée à une
situation d'urgence, une posologie ou une contre-indication périmée est **pire
qu'une absence** : elle est lue et suivie. Et la mêler à des fichiers
structurés du jour créerait une incohérence qu'aucun lecteur ne peut voir.

> **La leçon générale** : pour un corpus vital, la fraîcheur prime sur la
> facilité. Un miroir commode qui a quatre ans de retard est un piège, pas un
> raccourci.

### 2.3 Le piège majeur : 14 % du corpus muet, sans aucune erreur

**2 354 spécialités sur 15 859** sont autorisées par la Commission européenne.
Pour celles-là, la base nationale **n'héberge ni notice ni résumé des
caractéristiques du produit** : sa page ne contient qu'un renvoi vers un PDF de
l'Agence européenne des médicaments.

Techniquement, la page utilise un **conteneur différent** de celui des
médicaments à autorisation nationale. Une extraction qui ne connaît que le
conteneur habituel n'échoue pas : elle produit une fiche parfaitement formée
et **vide**.

Ce que cela coûtait, concrètement : les insulines, les anticoagulants et
l'essentiel de l'oncologie, présents dans l'index et vides à l'ouverture.

La solution : suivre le lien, récupérer le PDF (il existe en version
française), et en extraire le texte — d'où une dépendance à `pdftotext`
(`poppler-utils`). Plusieurs présentations d'un même produit partagent le même
document : dédoublonner divise les téléchargements par deux.

> **La leçon générale** : compter ce qu'on produit, pas seulement ce qu'on
> traite. Un compteur « fiches sans contenu » aurait signalé ce trou
> immédiatement ; sans lui, il ne se voit qu'en ouvrant le bon médicament.

### 2.4 L'encodage n'est pas forcément homogène dans une même source

Sur les six fichiers publiés par la même administration, **un seul est en
UTF-8**, les cinq autres en Windows-1252. Forcer un encodage unique abîme
forcément une moitié du corpus — sans erreur, juste des milliers de mots
déformés. La détection doit se faire **fichier par fichier**.

### 2.5 Une limite qu'on ne peut pas lever

L'homéopathie (environ un millier de spécialités) n'a **ni notice ni
monographie publiées**. Ces médicaments figurent dans l'archive avec leur
identité, leur composition et leurs présentations, mais sans texte. Ce n'est
pas un défaut d'extraction, c'est l'état de la base — et il vaut mieux
l'écrire que laisser croire à une couverture complète.

---

## 3. Pièges d'outillage (zimwriterfs, kiwix-serve, NOMAD)

### Échecs tardifs et échecs déguisés

- **Le titre du ZIM est plafonné à 30 caractères.** Au-delà, `zimwriterfs`
  n'écrit rien — et ne le dit qu'**à la toute fin**, après tout le temps de
  compression. Vérifier la longueur avant de lancer une compilation longue.
- **`kiwix-serve` attend le nom de fichier seul**, pas un chemin complet. Avec
  un chemin, il affiche son aide, liste le dossier et **sort en code 0** : un
  échec qui ressemble trait pour trait à un démarrage réussi.

### Intégration dans NOMAD

- **Nommage imposé** : `<identifiant>_<AAAA-MM>.zim`. NOMAD analyse le nom de
  fichier ; tout autre format et l'archive est ignorée en silence.
- **Déposer le fichier ne suffit pas** : il faut appeler
  `POST /api/zim/rescan-library`.
- **Une archive reconstruite change d'UUID.** La remplacer sur place sans
  rescanner casse la recherche (erreur 500). Toujours enchaîner remplacement →
  rescan → redémarrage du serveur Kiwix.
- **Illustration 48×48 en PNG obligatoire.**
- Ajouter la première archive francophone fait apparaître un **sélecteur de
  langue** dans la bibliothèque, qui masque les contenus anglais. Rien n'est
  perdu : repasser le filtre sur « All ».

### Ce que l'index plein texte couvre réellement

L'index de recherche est construit à partir du **HTML**, pas des PDF. Une
archive qui se contente d'empaqueter des documents PDF sera navigable mais
**pas cherchable**. Tout contenu qui doit être trouvé par un mot doit exister
sous forme de page HTML — quitte à extraire le texte du PDF et à placer
l'original à côté, en pièce jointe.

C'est le point le plus structurant pour qui fabrique une archive : la qualité
de la recherche est décidée à la génération, pas à la lecture.

---

## 4. Pièges de mise en forme des sources

### Les PDF réglementaires ne se lisent pas comme du texte suivi

- Le numéro de rubrique et son libellé sont posés dans **deux blocs distincts**
  (`4.1`, puis plus loin `Indications thérapeutiques`). Chercher un titre d'un
  seul tenant ne trouve **aucune** rubrique.
- Une ligne réduite à un entier nu est un **numéro de page**, pas une rubrique.
  Sans ce filtre, un « 48 » s'invite en tête de notice.
- Un document européen **répète l'intégralité de la monographie pour chaque
  présentation** (stylo, cartouche, flacon). Sans suffixe d'unicité, la page
  sort avec des identifiants HTML en double et les ancres cassent.
- Une notice s'ouvre sur son **sommaire**. Des raccourcis naïfs y atterrissent
  au lieu du texte cherché : il faut écarter les rubriques sans contenu propre.

### Les majuscules accentuées cassent les index alphabétiques

Le piège le plus discret que nous ayons rencontré, et il mérite un exemple.

Une fonction de désaccentuation qui ne traite que les minuscules laisse
`É` intact dans un nom écrit en capitales. L'initiale devient alors `É` —
une lettre qui n'existe dans aucune page d'index. Résultat :

- ces entrées ne sont **rendues sur aucune page de navigation** ;
- chaque fiche qui les référence pointe vers un fichier **inexistant** ;
- au tri, `É` passe **après `Z`**, donc même une liste complète les exilerait
  en fin de parcours.

Mesuré sur la base des médicaments : **72 substances actives et 651
spécialités** concernées, dont l'énoxaparine, l'éthinylestradiol,
l'ésoméprazole et l'étanercept. Aucune erreur, aucun avertissement.

Deux corrections, et la seconde compte plus que la première : couvrir les
majuscules et les ligatures, **et** garantir que la fonction de classement ne
peut renvoyer qu'une valeur effectivement affichée par un index. Corriger le
cas des accents ne suffit pas — il faut rendre la classe de bug impossible.

---

## 5. Ce que nous en retenons pour toute archive nationale

1. **Faire échouer bruyamment ce qui échoue silencieusement.** Vérifier les
   tailles, compter les fiches vides, refuser une source vide.
2. **Compter ce qu'on produit**, pas seulement ce qu'on traite.
3. **Jouer la chaîne complète sur un échantillon** avant la compilation
   longue. Les deux pièges d'outillage ci-dessus se paient en heures s'ils sont
   découverts à la fin.
4. **Vérifier la recherche, pas seulement la génération.** Le test utile n'est
   pas « l'archive existe » mais « ce terme précis trouve ce document précis »
   — en choisissant un terme qui ne peut venir que du chemin d'extraction le
   plus fragile.
5. **Écrire les limites qu'on ne peut pas lever.** Une couverture partielle
   annoncée est utilisable ; une couverture partielle silencieuse ne l'est pas.
