# Templates de ped de base — à fournir par l'utilisateur

Ce dossier doit contenir, APRÈS extraction manuelle depuis ta propre copie légale de GTA V :

- `template_female_a_f_y_business_02.blend`
- `template_male_a_m_y_business_02.blend`

Ces fichiers ne sont PAS fournis par ce dépôt (assets Rockstar non redistribuables).

## Procédure d'extraction (une fois, par genre)

1. Ouvrir `CodeWalker` (ou OpenIV) sur ton installation GTA V.
2. Localiser le ped ambient de base :
   - Féminin : `a_f_y_business_02`
   - Masculin : `a_m_y_business_02`
3. Exporter son `.yft` (fragment squelette), `.ydd` (drawable dictionary) et `.ytd` vers un
   dossier de travail (ex. `CodeWalker > Export XML` puis conversion, ou export direct Collada/
   OBJ+squelette selon la version de l'outil).
4. Dans Blender (4.2+, avec l'extension Sollumz v2.9.0 installée et activée) :
   `File > Import` via l'importeur Sollumz pour conserver les bone tags, flags et la hiérarchie
   Sollumz (Drawable Dictionary > Drawable > Drawable Model) — ne PAS repartir d'un import FBX
   générique, qui perd les métadonnées RAGE nécessaires à l'export.
5. Vérifier que l'objet armature s'appelle `Armature` (ou `skel`) et qu'au moins un mesh contient
   "head" ou "body" dans son nom (requis par `scripts/blender_rig_export.py`).
6. Sauvegarder sous le nom attendu dans ce dossier.

## Pourquoi ce ped de base précisément
Voir `docs/architecture-decisions.md` point 4 : un template ambient donne un squelette/rig
autonome par genre, et chaque ped généré par le pipeline obtient son propre nom et ses propres
fichiers — pas de partage d'index de composants comme avec freemode, donc pas de risque
d'écrasement entre plusieurs personnages générés.

Tant que ces fichiers ne sont pas présents, le pipeline fonctionne en **mode mock** (voir
`docs/architecture-decisions.md` point 6) : la compilation Blender est simulée et produit des
artefacts placeholder clairement marqués comme tels, pour permettre de tester toute la chaîne
(Tripo3D mock → Blender mock → manifeste → packaging) sans les outils réels.
