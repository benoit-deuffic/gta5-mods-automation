# Intégration en jeu d'un ped construit par le pipeline — analyse (2026-10-12)

Statut (2026-10-13) : étapes 2 et 3 faites (export + assemblage, `scripts/ped_package.py`, voir
`docs/architecture-decisions.md` point 38). Validé en jeu le 2026-10-13 par l'utilisateur :
Alexandra (90C + fesses athletic, `ped_package.py`, installée à la main) fonctionne.
Restent : variantes 001 à 005, `.oiv` et inscription automatisée. Deux choix restent à faire
(voir la fin).

Sources lues :
- deux vrais paquets OIV : `Downloads/e41d11-Ped Selector/.../PedSelector.oiv` et
  `Downloads/cfacc3-mp_fem_nude_2_02_oiv_READ_F_README/mp_female_nude_INSTALL.oiv` ;
- le mod CatalinaNude installé (`UserMods/CatalinaNude/`, 102 fichiers) et son `readme.txt` ;
- `UserMods/ap_m.xml` ;
- le code du dépôt : `layered_ped_builder.py`, `blender_rig_export.py`, `codewalker_convert.py`,
  `xml_manifest_updater.py`, `package_builder.py`, `ymt_builder.py`.

## 1. Le découpage : moins lourd qu'il n'y paraît

Le ped streamé est déjà découpé à la source :
- `<ped>.yft` (squelette) et `<ped>.ymt` (variations) ;
- un dossier `<ped>/` avec un `.ydd` par drawable (`uppr_000_r.ydd`, `lowr_000_r.ydd`…) et un
  `.ytd` par texture (`uppr_diff_000_a_ara.ytd`…).

Le builder (`layered_ped_builder.py`) importe chaque composant depuis son propre fichier. On sait
donc quel objet Blender vient de quel fichier.

La morphologie (`body_morphology.fit_bust` / `fit_glutes`) ne déforme que le corps nu,
`uppr_000` et `lowr_000`. Le reste ne change pas : tête, cheveux, pieds, accessoires, toutes les
`.ytd`, le `.yft` et le `.ymt`. On les copie en binaire tels quels, depuis `import/<ped>/`, sans
aller-retour XML : aucune perte possible. Seuls les `.ydd` déformés sont réexportés.

## 2. Les vrais points durs

1. **LOD** (résolu : la déformation est rejouée sur tous les LOD). Chaque `.ydd` contient `DrawableModelsHigh`, `Medium` et `Low` (vérifié sur
   `uppr_000_r`, `lowr_000_r`, `teef_000_u`). Ne déformer que High ferait changer le corps de
   forme d'un coup avec la distance. Il faut appliquer la même déformation aux trois niveaux :
   ajustement mesuré sur High, puis le même champ (mêmes lobes, mêmes échelles, même décalage)
   rejoué sur Medium et Low.
2. **Variantes de corps.** CatalinaNude a 6 `uppr` (000 à 005) et 3 `lowr` (000 à 002), les
   « bodyshape » du readme. Le builder ne prend que la variante la plus basse (000). Choix :
   - a. déformer toutes les variantes vers les mêmes cibles, chacune mesurée et ajustée ;
     le `.ymt` reste intact (**recommandé**) ;
   - b. livrer une seule variante, ce qui oblige à régénérer le `.ymt`. `ymt_builder.py` produit
     des `compInfos` et des `aSelectionSets` vides : des métadonnées seraient perdues.
3. **Textures intégrées.** Les `.ydd` de Catalina contiennent leurs normal et spec
   (`uppr_normal_000`, `uppr_spec_000`…). Résolu sans Sollumz : chaque `.ydd` d'origine est
   réexporté seul, déformé dans son XML, puis reconverti, avec des `.dds` identiques à l'octet.
   Le dossier plat `import_xml/` ne convient pas : `lowr_000/001/002` y partagent des noms de
   texture différents.
4. **Nom interne.** `uppr_000_r.ydd` contient un drawable nommé `uppr_015_r`, et `teef_000_u`
   contient `task_001_u`. Pour un ped streamé, le jeu se fie au nom du fichier : sans doute pas
   bloquant, mais on garde le nom interne d'origine par prudence.
5. **Renommage du ped** (par ex. `CatalinaNude_80A`, pour ne pas écraser l'original) : il suffit de
   renommer le dossier, le `.yft` et le `.ymt`. `CatalinaNude.yft.xml` ne contient pas le nom.
   Le contenu du `.ymt` (`dlcName`) reste à vérifier.
6. **Vêtements.** Les composants non déformés (jbib, accs) ne suivent pas la nouvelle forme et
   peuvent traverser le corps. Sans grande importance pour un ped nu, à garder en tête pour les
   autres.

## 3. Paquet OIV (format relevé sur les deux vrais paquets)

```
<Ped>.oiv            (zip)
  assembly.xml       <package version="2.2" target="Five"> ; metadata (name, version,
                     author, description), colors, content
  icon.png
  content/...        fichiers référencés par <add source="...">
```

Opérations observées :
- `<add source="x">chemin\relatif\au\jeu</add>` copie un fichier ;
- `<archive path="update\update.rpf" createIfNotExist="False" type="RPF7">` ouvre une archive ;
- `<xml path="common\data\dlclist.xml">` avec `<add xpath=... append="Last">` et
  `<remove xpath=...>` modifie un XML dans cette archive.

Pour la cible AddonPeds/PedSelector, on ajoute les fichiers dans
`update\x64\dlcpacks\addonpeds\dlc.rpf` → `peds.rpf` avec des `<archive type="RPF7">` imbriqués.
OpenIV écrit l'archive lui-même, on n'a pas de RPF à produire, et l'installation se fait dans
`mods/`. L'imbrication est prévue par le format, mais aucun des deux exemples ne l'utilise : à
valider au premier essai.

**Limite** : un `.oiv` ne modifie que des fichiers sous le dossier du jeu. Il ne peut pas toucher
`Documents/Rockstar Games/GTA V/UserMods/ap_m.xml`. Pour l'inscription, deux solutions :
- l'éditeur AddonPeds, à la main (ce que demande le readme de Catalina, avec Streamed = true) ;
- `scripts/xml_manifest_updater.py` (existe déjà : defusedxml, écriture atomique, IsStreamedGfx),
  appelé depuis WSL via `/mnt/c/...`, avec une sauvegarde du fichier avant.

Remarque : l'entrée CatalinaNude actuelle de `ap_m.xml` a `PoseMatcherName = Male` et
`PoseMatcherProneName = Male_prone` pour un ped féminin. Non corrigé, en attente d'un accord.

**Alternative écartée pour l'instant** : un dlcpack autonome (son propre `dlc.rpf` avec
`content.xml`, `setup2.xml`, `peds.meta`, plus l'ajout dans `dlclist.xml`, comme
`mp_female_nude.oiv`). Le `.oiv` ferait tout, sans `ap_m.xml`. Il faudrait en revanche fabriquer
un `dlc.rpf` complet, et `codewalker-cli` ne sait que lire les RPF (`RpfFile.GetResourceFile`).

`scripts/package_builder.py` est obsolète pour cette cible : il produit l'ancienne disposition non
streamée (un seul `.ydd`/`.ytd`) et laisse les fichiers à remballer à la main. Il sera remplacé
par l'étape « package » ci-dessous.

## 4. Recette proposée (une étape par commande, pilotée par arguments)

1. **build** : le `.blend` avec les cibles (existe déjà). À ajouter : la déformation des LOD
   Medium/Low et des variantes uppr/lowr.
2. **export** : uniquement les drawables déformés. Sollumz `export_assets` → `.ydd.xml`, puis
   `codewalker-cli` → `.ydd` (les deux briques existent).
3. **assemble** :
   - copie binaire de `import/<ped>/`, `<ped>.yft` et `<ped>.ymt` ;
   - remplacement des `.ydd` déformés ;
   - renommage du ped ;
   - contrôle : même liste de fichiers que l'original, chaque `.ydd` relu par `codewalker-cli`.
4. **package** : `.oiv` (`assembly.xml`, `icon.png`, `content/`).
5. **register** (optionnel) : entrée `ap_m.xml` via `xml_manifest_updater.py`, avec sauvegarde.
6. **test en jeu** (Windows, GTA V Legacy, PedSelector) : Package Installer d'OpenIV vers `mods/`,
   apparition du ped, LOD à distance, teintes de peau (6 ethnies), animations et
   `expr_set_ambient_female`, os `SPR_*`.

**Ordre conseillé** : d'abord les étapes 2 et 3 sur `uppr_000`/`lowr_000` seuls, avec un test en
jeu à la main (glisser-déposer dans OpenIV). C'est là que se trouvent les inconnues : textures
intégrées et comportement du jeu. Ensuite seulement : LOD, variantes, `.oiv`, inscription.

## 5. Décisions en attente

- Variantes de corps : toutes déformées (recommandé) ou une seule (régénérer le `.ymt`) ?
- Inscription `ap_m.xml` : notre script (avec sauvegarde) ou l'éditeur AddonPeds à la main ?
- Corriger `PoseMatcherName` de l'entrée CatalinaNude existante ?
