# Décisions d'architecture — gtav-ai-pipeline

Suite à `docs/analyse-prerequis.md`. Décisions validées avec l'utilisateur le 2026-10-11.

1. **Orchestrateur** : Hermes Agent + serveur MCP stdio (conforme à la spec fournie §2.2/§3).
   Le fichier `hermes.json` de la spec n'est pas un format Hermes valide : le serveur MCP sera
   déclaré dans `~/.hermes/config.yaml` (section `mcp_servers`), et les instructions système
   données via un skill (`gtav-ped-modding`, déjà présent) plutôt qu'un prompt embarqué.

2. **Cible de livraison** : AddonPeds/PedSelector (single-player, GTA V Legacy) **et** dlcpack
   autonome. FiveM explicitement exclu de ce lot.

3. **Transport build → jeu** : le build tourne sur ce poste Linux ; la livraison se fait par un
   paquet zip/.oiv généré par le pipeline, installé manuellement par l'utilisateur sur son PC
   Windows. Pas de partage réseau/SSH automatisé pour l'instant.

4. **Ped de base (template)** : ambient — `a_f_y_business_02` (féminin) / `a_m_y_business_02`
   (masculin). Choix justifié par rapport à freemode : chaque ped généré obtient un
   `model_name` unique, ses propres fichiers (.yft/.ydd/.ytd/.ymt) et sa propre entrée
   `ap_m.xml` → aucune collision structurelle possible, contrairement à freemode qui partage un
   seul dictionnaire de composants avec des index de slots à gérer manuellement.
   **Ces fichiers template doivent être extraits par l'utilisateur de SA propre copie du jeu**
   (CodeWalker/OpenIV → import Sollumz pour préserver bone tags/flags) ; ils ne sont pas
   redistribuables et ne sont donc jamais fournis par ce dépôt (cf. `templates/README.md`).

5. **Génération 3D** : Meshy (contenu SFW uniquement — aucun service mainstream n'autorise le
   NSFW, et le contenu sexuel impliquant une apparence mineure est illégal indépendamment des
   CGU). Architecture gardée portable (interface `Text3DBackend`) pour permettre un futur
   backend auto-hébergé (TripoSR/InstantMesh/Wonder3D) sans réécrire l'orchestration, mais ce
   n'est pas implémenté dans ce lot.

6. **Mode de fonctionnement actuel** : ni clé `MESHY_API_KEY`, ni Blender, ni CodeWalker ne sont
   installés sur cette machine. Le pipeline est donc livré avec un **mode mock** systématique
   (variable `PIPELINE_MOCK=1`, activé par défaut si les dépendances réelles sont absentes) :
   - `meshy_client` sert des fixtures locales au lieu d'appeler l'API Meshy.
   - `blender_runner` détecte l'absence de `blender` sur le PATH et produit des artefacts
     placeholder (CW-XML minimal) au lieu d'un vrai export Sollumz.
   - `codewalker_convert` détecte l'absence du binaire .NET et marque la conversion
     XML→binaire comme "pending-real-tool" sans fabriquer de faux binaire RAGE.
   Tout le reste (génération XML ap_m.xml, ymt, packaging zip/.oiv, validations, tests) est
   réel et testé par `pytest`, indépendamment des outils externes.

7. **Ollama ≠ remplacement de Meshy** : Ollama sert des LLM locaux (texte/vision
   GGUF), il ne génère pas de géométrie 3D — il n'existe aucun modèle de
   génération 3D distribué via Ollama. Rôle retenu : `mcp_server/prompt_refiner.py`
   utilise un LLM Ollama local pour enrichir le prompt utilisateur (silhouette,
   tenue, coiffure, pose neutre) avant génération 3D, avec repli automatique sur
   le prompt original si Ollama est indisponible, et un garde-fou mots-clés NSFW
   appliqué au prompt d'entrée ET à la sortie du LLM (cohérent avec la politique
   Meshy du point 5). `scripts/bootstrap_ollama.sh` détecte le matériel hôte
   (`scripts/hw_detect.py` : GPU NVIDIA/VRAM, RAM, disque libre) et choisit un
   tier de modèle adapté (testé réellement sur ce sandbox : tier `cpu-low` →
   `qwen2.5:0.5b-instruct`, ~400 Mo, Ollama installé et interrogé avec succès ;
   le même script choisira `qwen2.5:14b-instruct` + `llava:13b` sur le futur
   poste RTX 5070 12 Go).
   Génération 3D locale (vrai remplacement de Meshy) : architecture prête via
   `mcp_server/text3d_backend.py` (interface `Text3DBackend`, sélection par
   `TEXT3D_BACKEND=meshy|local`), mais l'implémentation réelle
   (`scripts/local_3d_generate.py` branché sur TripoSR ou Hunyuan3D-2) n'est
   PAS écrite ici : elle nécessite un GPU CUDA ≥8 Go VRAM absent de ce
   sandbox. `scripts/bootstrap_local_3d_env.sh` prépare l'environnement
   Python (venv, deps) pour le futur poste GPU, non exécuté/validé ici.

8. **Blender/Sollumz/CodeWalker (portage Linux headless)** : conservés tels
   quels (interface + mode mock, cf. point 6) sans investissement
   supplémentaire — le projet sera dupliqué vers le futur poste dédié où ces
   outils tourneront nativement (Blender+Sollumz sous Windows pour l'export
   binaire natif, cf. §2.2 de `docs/analyse-prerequis.md`). Décision explicite
   de l'utilisateur : pas de portage Linux headless réel à faire dans ce dépôt.

9. **Tripo3D comme second backend 3D (découverte du 2026-10-11)** : la clé
   dans `~/.meshly` n'était pas une clé Meshy (préfixe `msy_`) mais une clé
   **Tripo3D** (préfixe `tsk_`), confirmée valide en direct contre l'API réelle
   (`GET /v3/account/balance` → HTTP 200, authentification OK) mais avec un
   solde de 0 crédit (`balance=0.00`), donc aucune génération réelle possible
   pour l'instant. `mcp_server/tripo_client.py` + `TripoBackend` dans
   `text3d_backend.py` implémentent ce backend contre les vrais paramètres de
   l'API (vérifiés en direct : `POST /v3/generation/text-to-model` nécessite
   `model` parmi `P1-20260311|P2-20260801|v2.5-20250123|v3.0-20250812|
   v3.1-20260211`, confirmé par un vrai 400 de l'API). Sélection via
   `TEXT3D_BACKEND=tripo`. Différence notable avec Meshy : Tripo ne retourne
   qu'un GLB unique (textures PBR embarquées, pas de fichiers texture séparés,
   URL expirant 5 min après succès) — le script Blender (`blender_rig_export.py`)
   n'importe pour l'instant que du FBX, l'import GLB natif (`bpy.ops.import_scene.gltf`)
   reste à brancher quand Blender sera disponible pour le tester. Contrairement
   au mode mock de Meshy (activé seulement si aucune clé n'est configurée),
   une clé Tripo présente mais en échec (crédits insuffisants) lève une erreur
   explicite (`TripoInsufficientCreditsError`) plutôt que de basculer en mock
   silencieusement — pour ne jamais masquer un vrai problème de configuration.

10. **CodeWalker.Core compile et fonctionne RÉELLEMENT sous Linux (découverte
    du 2026-10-11)** — contredit l'hypothèse prudente du skill ("non prouvé").
    Vérifié de bout en bout :
    - `dotnet build CodeWalker.Core.csproj` (.NET 8 SDK) réussit sans erreur
      sur Linux (netstandard2.0, dépendances SharpDX/SharpDX.Mathematics
      purement managées, aucun appel natif Windows nécessaire pour la
      conversion XML→binaire).
    - Un CLI dédié (`tools/CodeWalkerCli/`, `codewalker-cli convert --in
      <xml_dir> --out <out_dir> --name <model_name>`) a été écrit, compilé en
      exécutable autonome Linux (`dotnet publish -r linux-x64
      --self-contained true`), et testé réellement : conversion de
      `.ytd.xml`/`.ydd.xml`/`.yft.xml` minimaux en vrais binaires RAGE (57,
      66, 71 octets respectivement) via `XmlMeta.GetYtdData/GetYddData/
      GetYftData`. Intégré et validé via `scripts/codewalker_convert.py`
      (`CODEWALKER_CLI_PATH`) et testé en bout en bout (`tests/
      test_codewalker_cli_integration.py`, skip propre si le CLI n'est pas
      construit).
    - CodeWalker n'a pas de fichier de licence explicite sur son dépôt → son
      code source n'est PAS vendorisé/committé ici, seulement cloné à la
      demande dans `third_party/CodeWalker` (gitignored) via
      `scripts/fetch_codewalker.sh`, même principe que pour les assets
      Rockstar (ne jamais embarquer ce qu'on ne possède pas).
    - **Découverte corrélée et importante** : en testant la conversion avec
      notre propre `.ymt` généré par `scripts/ymt_builder.py`, comparaison
      avec le vrai struct `CPedVariationInfo` dans
      `CodeWalker.Core/GameFiles/MetaTypes/MetaTypes.cs` (ligne ~5986) révèle
      que notre schéma généré est **incorrect** : champs réels manquants
      (`bHasTexVariations`, `bHasDrawblVariations`, `bHasLowLODs`,
      `bIsSuperLOD` en préambule du struct), mauvais niveau d'imbrication pour
      `aComponentData3`/`aDrawblData3` (structures `CPVComponentData` /
      `CPVDrawblData` / `CPVTextureData` / `CComponentInfo` / `CPedPropInfo`
      bien réelles mais mal reproduites), champs inventés sans existence
      réelle (`pedXml_availTexs`, `aValidCompDrawbl`, `propMask` au mauvais
      niveau). **Conséquence : `scripts/ymt_builder.py` doit être réécrit
      avant tout usage réel** — le CLI `codewalker-cli` refuse volontairement
      de convertir un `.ymt.xml` (`[SKIP]` explicite) plutôt que de produire
      un binaire corrompu silencieusement. Tâche de suivi ajoutée au plan.

11. **`scripts/ymt_builder.py` réécrit et validé par round-trip réel (bora,
    2026-10-11)** — corrige le point 10. Le vrai schéma `CPedVariationInfo`
    (112 octets) a été extrait directement de
    `CodeWalker.Core/GameFiles/MetaTypes/MetaTypes.cs` (`MetaStructureInfo`
    pour `CPedVariationInfo`/`CPVComponentData`/`CPVDrawblData`/
    `CPVTextureData`/`CPVDrawblData__CPVClothComponentData`/`CPedPropInfo`) et
    des règles de traversée XML→binaire dans
    `CodeWalker.Core/GameFiles/MetaTypes/XmlMeta.cs` (noms de balises
    case-sensitive = noms de champs C#, tableaux de structures via `<Item>`
    enfants, tableaux d'octets bruts via texte espacé, booléens via
    `value="true/false"`).
    - Champs racine corrects : `bHasTexVariations`, `bHasDrawblVariations`,
      `bHasLowLODs`, `bIsSuperLOD`, `availComp`, `aComponentData3`,
      `aSelectionSets`, `compInfos`, `propInfo`, `dlcName` (pas de `<Name>` —
      ce champ n'existe pas dans le struct binaire).
    - **Simplification documentée** : `aSelectionSets` et `compInfos` sont
      émis comme tableaux valides mais **vides** — `CComponentInfo` encode des
      métadonnées audio/expression (enum `ePedVarComp`, floats en dump hexa
      brut) que ce générateur ne tente pas de reproduire ; la permutation
      visuelle des composants (ce qui compte pour le ped lui-même) dépend de
      `availComp`/`aComponentData3`, pas de `compInfos`.
    - `tools/CodeWalkerCli` étendu : conversion `.ymt.xml` activée
      (`MetaFormat.RSC`, le format générique PSO — `.ymt.xml` ne correspond à
      aucun cas spécial de `XmlMeta.GetXMLFormat()`, il retombe sur RSC par
      défaut), plus une nouvelle sous-commande `codewalker-cli inspect
      <fichier binaire>` qui recharge le binaire produit via le vrai
      `YmtFile.Load()` de CodeWalker et affiche les blocs PSO reconnus.
    - **Preuve réelle (pas une supposition)** : un `.ymt` généré pour un ped à
      5 composants (head/uppr/lowr/hand/feet) convertit en 531 octets, et
      `codewalker-cli inspect` confirme au retour : bloc racine
      `CPedVariationInfo` de **112 octets exactement** (taille attendue du
      struct), plus les blocs imbriqués `CPVComponentData`/`CPVDrawblData`/
      `CPVTextureData` avec des tailles cohérentes avec 5 items. Testé via
      `tests/test_codewalker_cli_integration.py`
      (`test_real_ymt_round_trips_through_codewalker_inspect`).
    - Exécuté et vérifié sur **bora** (pas le sandbox d'origine) : CodeWalker
      CLI reconstruit là-bas avec succès (.NET 8 installé sans sudo via
      `dotnet-install.sh --install-dir "$HOME/.dotnet"`, nécessite
      `DOTNET_SYSTEM_GLOBALIZATION_INVARIANT=1` sur ce WSL faute de libicu).
      55 tests passent (était 51).

12. **Pipeline d'import réel validé bout en bout sur bora, Linux/WSL2
    (2026-10-11)** — l'utilisateur a extrait `a_f_y_business_02` (le vrai ped
    de base) via CodeWalker/OpenIV dans `peds/a_f_y_business_02/`
    (`.ydd`/`.yft`/`.ymt`/`.ytd`/`.yed`/`.awc`, jamais commité — voir
    `.gitignore`). Résultats :
    - Blender 4.5.14 LTS installé **sans sudo** (tarball officiel extrait
      dans `~/tools/`) + Sollumz v2.9.0 (release GitHub officielle, installée
      via `blender -c extension install-file Sollumz.zip --repo user_default`)
      + sa dépendance **réelle** `szio==1.3.0.dev9` (PAS `1.4.0.dev2` comme
      indiqué précédemment dans le skill — corrigé après l'avoir installée à
      la main avec la mauvaise version, ce qui masquait silencieusement la
      bonne version installée par Sollumz lui-même dans son dossier
      d'extension privé). Dépendances système additionnelles nécessaires même
      en headless : `libsm6 libice6 libxext6 libxrender1 libxi6
      libxkbcommon0 libgl1` (sudo, une fois).
    - **Tous les opérateurs Sollumz confirmés réellement enregistrés**
      (`sollumz.import_assets`, `sollumz.export_assets`, etc.) une fois
      `szio` correctement résolu — validant la correction du point 7
      (`sollumz.export_ped` n'existe pas).
    - **Blocage réel découvert** : importer un binaire `.yft`/`.ydd` brut
      extrait du jeu échoue avec *"Binary resource formats require PyMateria
      which is not supported on this platform"* — `PyMateria` est une lib
      Rockstar-licenciée **Windows uniquement**
      (`dependencies.py: sys.platform == "win32"`). Aucune solution de
      contournement côté Linux pour l'import binaire direct.
    - **Solution adoptée** : CodeWalker.Core sait aussi faire le chemin
      inverse de la conversion XML→binaire (point 10/11) : binaire→XML via
      `MetaXml.GetXml(...)`. Ajout de la sous-commande `codewalker-cli
      export-xml --in <binaire> --out <dossier>` (gère `.ydd/.yft/.ytd/.ydr`,
      utilise `RpfFile.GetResourceFile<T>()` pour parser le format RSC7
      autonome produit par CodeWalker/OpenIV, et doit fixer `file.Name`
      manuellement — cette propriété n'est peuplée automatiquement que par
      les métadonnées d'un répertoire RPF, absentes pour un fichier extrait
      isolé). Sollumz accepte ensuite le `.xml` produit sans jamais toucher à
      PyMateria. **Testé réellement** : `a_f_y_business_02.yft/.ydd/.ytd`
      exportés en XML (+ 50 textures `.dds` dépaquetées) puis importés avec
      succès dans Blender (`squelette` 104 os, meshes avec vertex groups
      `SKEL_*` correctement rigués) — premier `templates/template_female_a_f_y_business_02.blend`
      réel généré (non commité, voir `.gitignore`).
    - `scripts/blender_rig_export.py` : 4 bugs supplémentaires trouvés et
      corrigés en le faisant tourner pour de vrai contre ce template réel
      (pas seulement contre `bpy` mocké comme précédemment) :
      (a) `find_reference_mesh`/l'ancien lookup `Armature`/`skel` par nom
      fixe ne correspondent à rien dans un vrai import Sollumz (l'armature
      s'appelle `a_f_y_business_02`, les meshes `SKEL_ROOT`/`hash_XXXXXXXX`)
      — remplacé par une détection structurelle (armature = le plus grand
      nombre d'os ; mesh de référence = le mesh skinné sur cette armature
      avec le plus de vertices) ;
      (b) `obj.bound_box` retourne des `bpy_prop_array`, pas des `Vector` —
      multiplication matricielle échouait ;
      (c) l'addon Sollumz doit être réactivé explicitement à chaque
      exécution headless (l'activation ne persiste pas dans le `.blend`) ;
      (d) `sollumz.export_assets` nomme son fichier de sortie d'après le nom
      de l'objet Blender exporté, pas d'après `--model-name` — renommage de
      l'armature avant export.
    - **Découverte supplémentaire, corrigée dans `mcp_server/server.py`** :
      Sollumz met systématiquement le nom de fichier exporté en minuscules
      (`TestPed01` → `testped01.yft.xml`). Sans normalisation, le lookup
      `{model_name}.*` de `scripts/codewalker_convert.py` échoue
      silencieusement (fichier manquant traité comme "composant non
      produit", pas comme erreur). Ajout de `_normalize_target_name()`
      appelé en première ligne de chaque outil MCP pour que `target_name`
      soit en minuscules partout (staging, compiled, export Blender,
      manifest).
    - 55 tests toujours verts après ces corrections.

## Corrections appliquées par rapport à la spec d'origine
Voir le détail dans `docs/analyse-prerequis.md` §2. Résumé des points corrigés dans le code :
- Opérateur Sollumz : `sollumz.export_assets(...)`, pas `sollumz.export_ped` (inexistant).
- `vert_mapping` Blender valide (`POLYINTERP_VNORPROJ`), pas `POLY_NEAREST_VN` (inexistant).
- Bug de précédence Python corrigé dans la recherche du mesh de référence (head/body).
- Normalisation des poids + limite à 4 influences/vertex après le DataTransfer.
- `ap_m.xml` : parsing avec `defusedxml`, `IsStreamedGfx=false` cohérent avec la disposition de
  fichiers non-streamée, champs genrés dérivés du paramètre `gender` au lieu d'être en dur.
- Endpoints Meshy réels (`openapi/v2/text-to-3d`, `openapi/v1/rigging`), `art_style` retiré
  (déprécié), validation de taille de fichier remplacée par une validation de format/dimensions.
- `.ymt` (CPedVariationInfo) effectivement généré (absent de la spec d'origine).
- `.yft` traité comme copie/renommage du fragment squelette du ped de base, pas reconstruit.
