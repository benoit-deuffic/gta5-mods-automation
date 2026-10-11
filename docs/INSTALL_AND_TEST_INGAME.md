# Installation et test en jeu — `testped_reskin01` (Windows / GTA V Legacy)

Ce document explique comment installer, sur ton PC Windows (le même "bora" physique,
côté Windows — PC avec Menyoo + PedSelector déjà installés), le paquet produit par le
pipeline Linux, puis comment le tester réellement en jeu. Rien dans cette procédure ne
peut être exécuté depuis cette session Linux : tout ce qui suit est à faire **toi-même,
à la main, sur Windows**.

Référence du layout de livraison : `docs/architecture-decisions.md` points 2-3, et
`scripts/package_builder.py`. Critères d'acceptation : `gtav-ai-pipeline.spec` §8.

## 0. Fichier à récupérer

Sur la machine Linux (`bora`, côté WSL2) :

```
dist/testped_reskin01_delivery.zip   (~9.6 Mo)
```

Copie ce fichier vers ton Windows (partage réseau, clé USB, `\\wsl$\...` depuis
l'Explorateur Windows, etc.) — par ex. sur ton Bureau.

Contenu du zip (vérifié avec `unzip -l` / lecture directe, pas juste la valeur de retour
de l'outil) :

```
addonpeds_pedselector/
  README_INSTALL.txt
  UserMods/ap_m.xml
  dlcpacks/pedselector/dlc.rpf/peds.rpf/
    testped_reskin01.ydd / .ydd.xml
    testped_reskin01.yft / .yft.xml
    testped_reskin01.ytd / .ytd.xml
    testped_reskin01.ymt / .ymt.xml
dlcpack_standalone/testped_reskin01/
  content.xml, setup2.xml, README_INSTALL.txt
  common/data/peds.meta
  x64/models/cdimages/testped_reskin01/
    testped_reskin01.ydd / .yft / .ytd / .ymt (+ .xml)
```

Les fichiers **sans** extension `.xml` (ex. `testped_reskin01.ytd`, 2.99 Mo) sont les
vrais binaires RAGE, convertis réellement par CodeWalker.Core (`codewalker-cli convert`)
sur cette session — pas des placeholders. Les `.xml` à côté sont les intermédiaires
CodeWalker-XML, gardés pour debug ; tu n'en as pas besoin pour l'installation.

**Ce lot utilise la voie "reskin texture"** : le corps/squelette (`.ydd`/`.yft`) est une
copie/renommage du ped de base `a_f_y_business_02` déjà extrait et validé (voir
`docs/architecture-decisions.md` point 4 et point 12) — seule la texture `uppr_diff_000_a_whi`
a été remplacée par le rendu IA (`a dark leather jacket texture`, pipeline ControlNet-depth,
point 14). C'est volontaire et documenté : la géométrie du corps ne change pas, seul l'habillage
visuel change. Suis cette procédure deux fois (deux layouts différents, choisis-en UN SEUL —
voir §1) ; ne mélange pas les deux.

## 1. Choix du layout : AddonPeds/PedSelector (recommandé)

Tu as déjà Menyoo + PedSelector installés → utilise `addonpeds_pedselector/`, pas
`dlcpack_standalone/` (ce deuxième ne produit pas de vrai `.rpf` binaire packé tant que tu
n'as pas repassé par OpenIV/CodeWalker, voir son propre `README_INSTALL.txt` dans le zip).

### 1.1 Copier le manifeste

Copie le contenu de `addonpeds_pedselector/UserMods/ap_m.xml` dans :

```
Documents\Rockstar Games\GTA V\UserMods\ap_m.xml
```

- Si ce fichier **n'existe pas encore** : copie-le tel quel.
- S'il **existe déjà** (tu as d'autres peds AddonPeds déjà installés) : **fusionne**, ne
  l'écrase pas — ouvre les deux fichiers dans un éditeur de texte, copie uniquement le
  bloc `<Item>...</Item>` dont le `<Name>` vaut `testped_reskin01` à l'intérieur de la
  balise `<InitDatas>` existante du fichier cible, juste avant `</InitDatas>`.

### 1.2 Copier les fichiers modèle dans le dlcpack PedSelector

Les fichiers sous `addonpeds_pedselector/dlcpacks/pedselector/dlc.rpf/peds.rpf/` doivent
être intégrés dans le **vrai** `peds.rpf` de ton installation AddonPeds/PedSelector :

```
<GTA V>\mods\update\x64\dlcpacks\pedselector\dlc.rpf\peds.rpf
```

Avec **OpenIV** (mode édition activé) :
1. Ouvre `dlc.rpf` → `peds.rpf` dans l'arborescence.
2. Glisse-dépose les 4 fichiers binaires (`testped_reskin01.ydd`, `.yft`, `.ytd`, `.ymt` —
   PAS les `.xml`) à l'intérieur.
3. Referme OpenIV proprement pour qu'il réécrive le RPF.

Si `dlclist.xml` (`mods\update\update.rpf\common\data\dlclist.xml`) ne contient pas déjà
`dlcpacks:/pedselector/`, ajoute-la (normalement déjà présente si PedSelector est
installé).

### 1.3 Prérequis déjà présents sur ta machine
- ScriptHookV + SHVDN3 (PedSelector en dépend).
- PedSelector lui-même (déjà installé selon toi).
- Menyoo (déjà installé).

## 2. Lancer le jeu et sélectionner le ped

1. Lance GTA V **version Legacy** (PedSelector ne fonctionne PAS sur Enhanced — vérifie
   que tu lances bien `GTA5.exe` legacy, pas la version Enhanced si les deux sont
   installées).
2. En jeu, ouvre le menu PedSelector (touche configurée dans son .ini, par défaut
   généralement `F6` ou un sous-menu SHVDN — vérifie `PedSelector.ini` si tu ne te
   souviens plus).
3. Cherche `testped_reskin01` dans la liste (le nom est stocké en minuscules dans
   `ap_m.xml`/`<Name>` — c'est normal, Sollumz/CodeWalker normalisent toujours les noms de
   fichiers et identifiants en minuscules, voir `docs/architecture-decisions.md` point 12).
4. Sélectionne-le pour te transformer en ce ped.

**Point de vérification n°1 (critère d'acceptation spec §8.4, "Initial Injection")** :
- ✅ Succès : le ped se charge, tu te retrouves avec son apparence (corps `a_f_y_business_02`
  avec la veste en cuir générée par IA sur le torse), sans crash.
- ❌ Échec à signaler : crash immédiat ou "texture allocation" / écran noir / CTD au moment
  de la sélection → signale-le avec le message d'erreur exact (le `.ytd` fait 2.99 Mo avec
  mipmaps DXT1, il ne devrait pas poser ce problème, mais le bug connu de `codewalker-cli
  inspect` sur ce `.ytd` — voir `docs/architecture-decisions.md` point 13 — veut dire que le
  binaire produit par `convert` n'a jamais pu être revalidé par un second outil indépendant;
  c'est le test le plus important de toute cette procédure).

## 3. Cloner le ped avec Menyoo

1. Une fois transformé en `testped_reskin01`, ouvre le menu Menyoo (touche `F8` par
   défaut).
2. Va dans **Player Options > Clone Player**.
3. Un PNJ clone de ton personnage actuel (donc avec l'apparence `testped_reskin01`) doit
   apparaître à côté de toi.

**Point de vérification n°2 (critère d'acceptation spec §8.4, "Menyoo Cloning")** :
- ✅ Succès : le clone apparaît, stable, avec la bonne apparence.
- ❌ Échec à signaler : le clone n'apparaît pas, apparaît avec l'apparence par défaut (pas
  la tienne), ou crash au clonage.

## 4. Object Spooner — ajouter le clone à la base de données (persistance)

1. Ouvre le menu Menyoo **Object Spooner**.
2. Vise/sélectionne le PNJ clone créé à l'étape 3 (habituellement en le ciblant avec la
   mire Spooner — "Select Existing Entity" ou en visant directement selon la version de
   Menyoo).
3. Utilise **Add to Database** (persistance pour les prochaines sessions).
4. Quitte et relance une session / recharge une sauvegarde à proximité pour vérifier que
   le PNJ réapparaît bien à sa position depuis la base Menyoo.

**Point de vérification n°3 (critère d'acceptation spec §8.4, "Spooner Persistence")** :
- ✅ Succès : "Add to Database" réussit sans erreur, le PNJ réapparaît après reload.
- ❌ Échec à signaler : erreur au moment d'ajouter à la base, ou le PNJ ne réapparaît pas
  après reload.

## 5. Test d'animation (distorsion géométrique)

1. En tant que clone ou en tant que joueur transformé en `testped_reskin01`, déclenche des
   animations variées via Menyoo (émotes, ou le script "Forever Together" si tu l'utilises
   déjà) : marche, course, accroupi, entrée/sortie de véhicule, quelques émotes de bras
   amples.
2. Observe particulièrement les bras, les jambes et les hanches pendant le mouvement.

**Point de vérification n°4 (critère d'acceptation spec §8.4, "Animation Compatibility")** :
- ✅ Succès : le squelette suit les animations normalement, pas d'étirement de polygones à
  l'infini, pas de membres qui se déforment de façon absurde.
- ❌ Échec à signaler : "polygon stretching" (membres étirés façon spaghetti), des parties
  du maillage qui flottent loin du corps. À savoir : ce lot réutilise directement le
  squelette ET le maillage d'origine du ped de base (aucune nouvelle géométrie IA n'a été
  riguée dessus pour ce test précis — seule la texture a changé), donc ce risque est en
  théorie minimal ici ; un échec à cette étape indiquerait plutôt un problème côté
  renommage/réemballage RPF (étape 1.2) que côté rig.

## 6. Limitation connue à garder en tête en testant

Le fichier `testped_reskin01.ytd` (texture packée, 2.99 Mo) n'a **jamais pu être revalidé
en relecture par un second outil indépendant** sur cette session : `codewalker-cli inspect`
échoue avec `Exception: illegal position!` sur ce `.ytd` précis — un bug reproduit à
l'identique sur l'export **non modifié** du jeu lui-même (donc pas introduit par le
remplacement de texture), voir `docs/architecture-decisions.md` point 13. La seule
validation faite a été une relecture Pillow du fichier DDS généré (dimensions/format
corrects). **C'est précisément pour ça que cette procédure de test en jeu existe** — c'est
le premier vrai test de ce binaire sur un moteur GTA V réel.

## 7. Quoi me remonter après le test

Pour chacune des 4 étapes de vérification ci-dessus : succès/échec + message d'erreur ou
capture d'écran si échec. Avec ça on saura si :
- le pipeline de reskin texture (le plus nouveau et le moins testé en jeu) est
  effectivement jouable tel quel ;
- le bug `codewalker-cli inspect` sur ce `.ytd` a un impact réel en jeu ou pas ;
- il faut ajuster le remplissage d'arrière-plan du ControlNet (mosaïque de cuir visible
  sur les zones non couvertes par la veste, documenté point 14) avant une prochaine
  itération.
