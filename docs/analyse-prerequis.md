# Analyse des pré-requis — gtav-ai-pipeline.spec

Référence : `gtav-ai-pipeline.spec` (copie exacte de la spec fournie, sha256 4b374bb9…57ad0).
Date d'analyse : 2026-10-11. Sources vérifiées : docs Meshy (text-to-3d v2, rigging v1),
dépôt Sollumz v2.9.0, dépôt CodeWalker (CodeWalker.Core), pages AddonPeds / Ped Selector.

## 1. Pré-requis par couche

### 1.1 Poste de build (Linux, cette machine)
| Élément | Requis par la spec | État actuel | Action |
|---|---|---|---|
| Python ≥ 3.11 + uv | oui | uv présent, Python 3.14 | `uv python install 3.12` (mcp/pydantic OK) |
| Blender 4.x headless | oui | ABSENT | Blender 4.5.9 LTS (tarball ou image Docker) |
| Sollumz activé | oui | ABSENT | Sollumz v2.9.0 (extension, Blender ≥ 4.2) + dépendance `szio==1.4.0.dev2` dans le Python de Blender |
| Conversion XML → binaire | implicite | ABSENT | CLI .NET sur CodeWalker.Core (netstandard2.0, compilable sous Linux via Docker dotnet) |
| Conversion PNG → DDS | implicite | ABSENT | ImageMagick (DXT5 + mipmaps) |
| GPU | non | aucun | non bloquant (pas de rendu requis) |
| Disque | ~3 Go | 8,8 Go libres | OK mais serré (Blender ~1 Go, SDK .NET ~0,9 Go) |
| Docker | optionnel | présent | utilisé pour Blender et dotnet |

### 1.2 Secrets / comptes
- `MESHY_API_KEY` : ABSENT. Coût indicatif par perso : preview ~20 crédits + refine ~10 + rigging ~5.
  (Note 2026-10-12 : Meshy a été retiré du projet, voir `docs/architecture-decisions.md` point 17 ;
  cette ligne documente l'analyse d'origine, le projet utilise désormais `TRIPO_API_KEY`.)
- Clé LLM : inutile si Hermes orchestre via MCP (cf. §3, conflit avec le choix initial "orchestrateur Claude SDK maison").

### 1.3 Assets de référence (ne peuvent PAS être fournis par nous)
- `template_male.blend` / `template_female.blend` doivent être produits depuis TA copie du jeu :
  extraire un ped de base (ex. `mp_f_freemode_01` / `a_f_y_*`) avec CodeWalker/OpenIV, l'importer
  dans Blender via Sollumz (pour conserver les tags de bones, flags et hiérarchie Sollumz), sauvegarder.
  Les fichiers Rockstar ne sont pas redistribuables.
- Le `.yft` (squelette/fragment) et le `.ymt` du ped de base sont aussi nécessaires (voir §2.4).

### 1.4 Poste de jeu (Windows)
- GTA V **Legacy** uniquement : Ped Selector ne supporte pas GTA V Enhanced.
- ScriptHookV, ScriptHookVDotNet 3, OpenIV (dossier `mods/`), AddonPeds/PedSelector installé
  (`mods/update/x64/dlcpacks/<addonpeds|pedselector>/` + entrée dans `dlclist.xml`), Menyoo
  (critères d'acceptation 4), éventuellement le script "Forever Together".
- Un moyen de livrer depuis Linux : partage SMB/SSH, ou génération d'un paquet (zip / .oiv).

## 2. Écarts et erreurs dans la spec (à corriger avant implémentation)

### 2.1 Livraison dans le jeu — BLOQUANT
- La spec dépose les modèles en vrac dans `<GTAV_INSTALL_ROOT>/UserMods/`. Le jeu ne charge pas de
  fichiers en vrac : avec AddonPeds/PedSelector, les `.yft/.ydd/.ytd/.ymt` vont dans
  `mods/update/x64/dlcpacks/<addonpeds|pedselector>/dlc.rpf/peds.rpf`.
- `ap_m.xml` n'est pas sous la racine du jeu mais dans
  `Documents/Rockstar Games/GTA V/UserMods/ap_m.xml`.
- Écrire dans un RPF demande un outil : CodeWalker.Core sait créer/modifier des RPF "OPEN"
  (style OpenIV) sans clés Rockstar → faisable sans OpenIV, à valider.

### 2.2 Script Blender (§6) — ne fonctionnera pas tel quel
1. `bpy.ops.sollumz.export_ped` n'existe pas. L'opérateur réel est
   `bpy.ops.sollumz.export_assets(directory=…, direct_export=True, use_custom_settings=True, …)`
   et il n'exporte que des objets ayant la hiérarchie Sollumz
   (Drawable Dictionary → Drawable → Drawable Model, `sollum_type` renseignés, shader `ped`).
   Méthode fiable : réutiliser le composant importé du template et y remplacer la géométrie.
2. Export binaire natif Sollumz (`pymateria`) = **Windows uniquement**. Sous Linux on obtient du
   CodeWalker XML (`.ydd.xml`, `.ytd.xml`) → étape de conversion obligatoire (cf. 1.1).
3. Aucun alignement : le mesh Meshy arrive à échelle, orientation et pose arbitraires. Un
   DataTransfer de poids sur un mesh non aligné à la pose de bind GTA donne des poids faux
   (étirements, critère 4 "Animation Compatibility" en échec). Il faut : `pose_mode: "t-pose"`
   ou `"a-pose"` côté Meshy, mise à l'échelle sur la boîte du mesh de référence, puis repose sur
   la pose de bind GTA (via le rig Meshy, API rigging, mapping de bones par mots-clés).
4. `vert_mapping='POLY_NEAREST_VN'` n'existe pas (valeurs Blender : `NEAREST`, `POLY_NEAREST`,
   `POLYINTERP_NEAREST`, `POLYINTERP_VNORPROJ`…). Manquent aussi `layers_vgroup_select_src='ALL'`,
   `layers_vgroup_select_dst='NAME'`, puis normalisation et limite à 4 influences/vertex (RAGE).
5. Bug de précédence : `obj.type == 'MESH' and "head" in … or "body" in …`.
6. Limites RAGE non vérifiées : ≤ 65 535 vertices par géométrie, textures puissance de 2,
   DXT + mipmaps (sinon risque de "texture allocation crash", critère 4).

### 2.3 Manifeste ap_m.xml (§7)
- `defusedxml` déclaré mais parse via `xml.etree` non sécurisé.
- ~~Gabarit "female" incohérent : `PoseMatcherName=Male`~~ — faux constat : `Male` / `Male_prone`
  est la bonne valeur pour les deux sexes. `Female` / `Female_prone` n'existe pas dans le jeu,
  qui plante au lancement (testé en jeu le 2026-10-13).
- `IsStreamedGfx` codé en dur à `false` alors que `register_usermods_entry` a `streamed_gfx=true`
  par défaut. La disposition de fichiers décrite (1 `.ydd` + 1 `.ytd`) correspond à un ped
  **non streamé** → `false` est la bonne valeur ; le défaut `true` est faux.
- Ordre des champs : conforme à `CPedModelInfo__InitData` de CodeWalker (vérifié).

### 2.4 Artefacts manquants
- `.ymt` (CPedVariationInfo) listé dans les livrables mais produit par aucune étape. À générer
  (XML → binaire via CodeWalker.Core) ou copier depuis le ped de base si même découpage
  de composants.
- `.yft` : la spec le fait exporter par Blender ; en pratique c'est le fragment squelette du ped
  de base, copié/renommé (le reconstruire dans Sollumz n'apporte rien et casse souvent la physique).

### 2.5 Meshy (§3, §5.3)
_(Analyse historique de la spec d'origine, qui prévoyait Meshy comme backend 3D ;
Meshy a été retiré du projet le 2026-10-12, voir `docs/architecture-decisions.md`
point 17 — Tripo3D est désormais le seul backend cloud. Section conservée telle
quelle pour l'historique de l'analyse.)
- `MESHY_API_BASE_URL=https://api.meshy.ai/v2.0` est faux. Endpoints réels :
  `POST https://api.meshy.ai/openapi/v2/text-to-3d` (preview puis refine),
  `POST https://api.meshy.ai/openapi/v1/rigging`, polling `GET …/{id}`.
- `art_style` est déprécié (ignoré par Meshy 6+) ; `stylized` n'a jamais été une valeur valide
  (`realistic` | `sculpture`). Paramètres utiles : `pose_mode`, `target_polycount`,
  `should_remesh`, `enable_pbr`, `target_formats`.
- Le seuil "> 500 Ko" par fichier est arbitraire (une texture PNG simple peut faire moins) :
  remplacer par une validation de format/dimensions.

### 2.6 Orchestration / MCP (§2.2, §3, §5.1)
- Deux jeux de noms d'outils incompatibles : §2.2 (`process_ped_asset`, `register_usermods_entry`)
  vs §3 (`generate_raw_3d_asset`, `compile_gtav_ped`, `sync_usermods_manifest`). Proposition : §3.
- `hermes.json` n'est pas un format de configuration Hermes. Un serveur MCP se déclare dans
  `~/.hermes/config.yaml` (section `mcp_servers`) ; le prompt système va dans un skill/profil.
- `uv run --directory ./mcp_server python -m mcp_server.server` : depuis l'intérieur du
  package, `mcp_server` n'est pas importable → utiliser `--directory .`.
- Chemin `GTAV_INSTALL_ROOT` Windows alors que le pipeline tourne sous Linux : il faut un
  transport (partage monté) ou un mode "paquet" livré à la main.

### 2.7 Critères d'acceptation
- 1 à 3 : automatisables (tests + validateur).
- 4 (PedSelector, clonage Menyoo, Spooner, animations) : uniquement testables à la main sur
  le poste Windows. On peut seulement réduire le risque (vérif squelette = template, poids
  normalisés ≤ 4, textures DXT/mipmaps, rendu de poses de test dans Blender).

## 3. Décisions à prendre
1. Orchestrateur : Hermes + MCP (spec) ou orchestrateur Claude SDK maison (choix précédent) ?
2. Cible : AddonPeds/PedSelector seul (spec) ou aussi dlcpack autonome + FiveM (choix précédent) ?
3. Livraison sur le poste de jeu : partage monté, SSH, ou paquet zip/.oiv à installer à la main ?
4. Ped de base pour les templates (ex. `mp_m_freemode_01` / `mp_f_freemode_01` ou un ambient) ?
