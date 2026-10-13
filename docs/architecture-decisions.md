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

13. **Pipeline de reskin textures IA headless multi-caméra, bake UV-aware
    (bora, 2026-10-11)** : construction de `scripts/ai_texture_reskin.py`
    (Blender `--background`) + `scripts/sd_view_synthesis.py` (process
    séparé, venv `~/tools/sd-local/venv`) + `scripts/dds_pack_and_validate.py`
    + outil MCP `generate_ai_texture_reskin`, sans AUCUNE dépendance à un
    viewport interactif (contrainte dure — cf. tentative Dream Textures
    abandonnée, point précédent de ce doc et skill `gtav-ped-modding`).
    - **Rendu GPU headless testé en premier, RÉSULTAT POSITIF** : contrairement
      à l'inquiétude de départ sur le passthrough GPU WSL2 pour le rendu,
      EEVEE Next ET Cycles (device CUDA, RTX 5070 détectée par
      `prefs.get_devices()`) fonctionnent tous les deux `blender --background`
      sans aucun hack — testé avec une scène cube+caméra+soleil triviale,
      confirmé ensuite sur le vrai mesh ped (3041 faces, rendu 512×512 en
      ~1.5s/vue à 16 samples Cycles). Pas de dépendance X11/OpenGL display
      requise pour Cycles CUDA en mode background.
    - **6 caméras, pas 4** : une couronne de 4 caméras (face/dos/gauche/droite)
      seule laisse les épaules et l'entrejambe quasi non couverts sur un
      ped humanoïde — ajout de 2 caméras haut/bas. Vérifié concrètement sur
      le mesh réel : partition de 3041/3041 faces en 6 lots de tailles très
      inégales (1066 pour la caméra avant, 232 pour celle du dessus),
      confirmant que la couverture par caméra est loin d'être uniforme et
      que le nombre de caméras compte réellement.
    - **Masquage par partition exclusive des faces, pas par compositing de
      pixels transparents** : chaque face est assignée à EXACTEMENT une
      caméra (dot(normale, direction-caméra) maximal, seuil >0.05), implémenté
      en dupliquant le mesh et en supprimant les faces non retenues avant le
      bake Cycles (`type='EMIT'`) — comme seule la géométrie retenue existe
      dans l'objet dupliqué, le bake ne peut physiquement écrire que dans les
      texels de ses propres îlots UV, donnant le "masquage" gratuitement sans
      logique de compositing de pixels séparée. **Limitation assumée et non
      implémentée** : pas de test d'occlusion réel (ray-cast) — une face peut
      être assignée à une caméra géométriquement face à elle même si une autre
      partie du mesh bloque physiquement la vue dans le rendu ; seul le test
      frontal (dot product) est fait.
    - **Image de guidage = rendu texturé Cycles réel du mesh, pas une vraie
      carte de profondeur Z** : une normalisation Z-pass + compositeur a été
      jugée inutilement complexe à câbler proprement en headless pour la
      valeur ajoutée obtenue ; la tâche autorisait explicitement un "guide
      image plat/clay-shaded" comme entrée du fallback SDXL img2img — c'est
      ce qui est implémenté, documenté comme simplification (pas de ControlNet
      profondeur).
    - **SDXL img2img, pas StableDiffusionDepth2ImgPipeline** : le modèle
      depth officiel (`stabilityai/stable-diffusion-2-depth`) nécessiterait un
      téléchargement supplémentaire de ~5 Go non fait cette session ; SDXL
      base (déjà en local, prouvé fonctionnel précédemment) est réutilisé en
      img2img. **Résultat honnête testé réellement** : à `strength` 0.6-0.75
      sur une image de guidage, le résultat est quasi identique à l'entrée
      (quasiment aucun contenu généré visible) — seulement à `strength`
      0.85-0.9 un contenu génératif réel apparaît. Même à 0.85, la qualité
      vis-à-vis du prompt testé ("a stylized adult female character wearing a
      dark leather jacket") est décevante sur le mesh réel : le rendu généré
      ressemble à un motif mécanique/armure stylisée plutôt qu'à une veste en
      cuir reconnaissable (image inspectée directement, voir
      `/tmp/reskin_test2/testped_reskin_cam_ring_0_view.png` au moment du test).
      Le mécanisme (caméras, partition, projection UV, bake) est validé comme
      fonctionnel ; la qualité du contenu généré par SDXL img2img sur ce type
      d'image de guidage (rendu texturé existant, pas un simple silhouette
      plate) ne l'est pas — probablement un meilleur candidat serait un
      ControlNet-depth dédié ou un guide image plus proche d'un silhouette/
      normal map plutôt qu'un rendu texturé complet. Non corrigé dans ce lot,
      documenté comme limitation connue.
    - **Bake Cycles EMIT confirmé fonctionnel pour de vrai** : testé d'abord
      avec `--skip-sd` (bake direct des images de guidage, sans coût GPU SD)
      pour valider rapidement la mécanique géométrique — texture finale
      inspectée visuellement, contenu distinct par région (visage, bras,
      torse) confirmé, pas une image uniforme/cassée.
    - **Conversion DDS vers le format pixel d'origine** : lu depuis le
      `<Format>` du `.ytd.xml` existant (`D3DFMT_DXT1` pour
      `uppr_diff_000_a_whi`, 512×512) via `scripts/dds_pack_and_validate.py`,
      écriture Pillow avec `pixel_format='DXT1'`, fichier DDS remplacé en
      place (même nom) pour que le `.ytd.xml` existant reste valide sans
      régénération.
    - **Bug pré-existant découvert dans `codewalker-cli inspect` sur ce
      `.ytd`, PAS introduit par ce travail** : `codewalker-cli convert` produit
      un `.ytd` binaire de taille raisonnable sans erreur, mais
      `codewalker-cli inspect` échoue avec `Exception: illegal position!` —
      reproduit à l'identique sur un round-trip de l'export **original non
      modifié** (`/tmp/ped_xml_export` tel quel, aucune texture remplacée),
      donc ce n'est pas notre nouvelle texture qui casse quoi que ce soit,
      c'est un bug (ou une limite) de la sous-commande `inspect` elle-même
      face à ce `.ytd` multi-textures particulier. Validation alternative
      utilisée : relecture Pillow du DDS produit (dimensions/format corrects
      confirmés), le binaire produit par `convert` lui-même n'a pas pu être
      validé en jeu (pas de PC Windows GTA V dans cette session).
    - Nouveaux tests : `tests/test_ai_reskin_runner.py` (fallback mock,
      résolution Blender/sd-local) et `tests/test_dds_pack_and_validate.py`
      (parsing du format depuis le XML, rejet d'un format DDS non mappé) —
      62 tests passent (55 existants + 7 nouveaux).
    - Outil MCP `generate_ai_texture_reskin` ajouté, même convention que les
      autres (`_normalize_target_name` en premier, fallback mock si
      Blender/sd-local absents, réutilise le garde-fou mots-clés bloqués de
      `prompt_refiner` puisqu'il accepte aussi un prompt brut utilisateur).

14. **Remplacement du guide plat + SDXL img2img par une vraie carte de
    profondeur + ControlNet-depth SDXL (bora, 2026-10-11)** : suite à la
    limitation documentée au point 13 ("ressemble à une armure stylisée, pas
    à une veste"), remplacement complet des deux maillons faibles identifiés.
    - **Vraie carte de profondeur Z par pixel, `render_guide_image()`
      réécrite** (`scripts/ai_texture_reskin.py`) : `view_layer.use_pass_z =
      True` + `scene.use_nodes = True` + graphe compositeur Render Layers ->
      Map Range (bornes near/far dérivées de la distance caméra-centre de
      bbox ± rayon, pas un Normalize générique qui écraserait le contraste
      avec le sol/l'arrière-plan) -> File Output (PNG niveaux de gris) et ->
      Composite. **Testé pour de vrai et inspecté visuellement** (pas
      supposé fonctionner juste parce que Blender ne plante pas) : la sortie
      montre un dégradé de gris net et cohérent (silhouette humanoïde
      blanche/claire proche de la caméra s'assombrissant vers le fond noir,
      visage/bras/torse avec variations de profondeur visibles) — une vraie
      depth map, pas une approximation. **L'inquiétude de départ sur le
      sous-pipeline compositeur en headless (abandonné précédemment "pour
      une raison peu claire") ne s'est PAS confirmée** : `use_pass_z`, le
      compositeur et les File Output nodes fonctionnent sans accroc en
      `blender --background`, aucun hack de viewport/display nécessaire. Un
      détail d'implémentation à noter : `CompositorNodeOutputFile` suffixe
      toujours le nom de fichier avec le numéro de frame (`nom0001.png`),
      même pour un seul still — code ajouté pour retrouver ce fichier et le
      renommer vers le chemin exact attendu par l'appelant.
    - **`scripts/sd_view_synthesis.py` réécrit : SDXL + ControlNet-depth, en
      text-to-image, pas img2img** : `StableDiffusionXLControlNetPipeline`
      avec `ControlNetModel.from_pretrained('diffusers/controlnet-depth-sdxl-1.0')`
      (téléchargement à la volée, ~2.5 Go, pas de conflit de dépendances
      avec le venv `~/tools/sd-local` déjà en place — diffusers 0.41.0 /
      torch 2.11.0+cu128 chargent `ControlNetModel` et
      `StableDiffusionXLControlNetPipeline` sans rien réinstaller). La depth
      map pilote UNIQUEMENT la structure (`control_image`,
      `controlnet_conditioning_scale=0.8` par défaut) ; le contenu/matière/
      couleur vient entièrement du prompt texte — plus d'image de référence
      à "modifier" comme en img2img, ce qui évite le compromis force
      0.6 (no-op) vs 0.85 (contenu réel mais incohérent) du point 13.
      Repli prévu mais non nécessaire cette session : `--backend sd2depth`
      (`StableDiffusionDepth2ImgPipeline`, SD2.1, ~5 Go) si un vrai blocage
      ControlNet-SDXL était rencontré — aucun blocage réel rencontré, donc
      resté sur le chemin SDXL déjà prouvé comme demandé par la tâche.
    - **VRAM** : RTX 5070 12 Go, ~11.7 Go libres mesurés avant le run.
      Pipeline SDXL + ControlNet-depth chargés ensemble à 512×512 avec
      attention slicing tiennent sans OOM ; `enable_model_cpu_offload()`
      câblé en secours (catch `torch.cuda.OutOfMemoryError` avant ET pendant
      l'inférence) mais jamais déclenché à cette résolution sur ce run.
    - **Résultat visuel comparé honnêtement, image par image inspectée
      directement** :
      - Carte de profondeur (`testped_reskin_cam_ring_0_guide.png`) :
        silhouette humanoïde nette en dégradé de gris, confirmée ci-dessus.
      - Vue générée isolée (`cnet_view_ring0.png`, prompt "a dark leather
        jacket texture, photorealistic fabric, studio lighting") :
        **nette amélioration** — veste reconnaissable avec capuche,
        fermeture éclair, manches, poignets, plis cohérents sur un buste
        humain réel, plus aucune ressemblance avec une armure abstraite
        comme au point 13. Limite trouvée : l'arrière-plan (profondeur
        "lointaine" = noir) se fait remplir d'un motif de cuir répété en
        mosaïque par ControlNet au lieu de rester neutre — pas masqué dans
        cette passe.
      - Texture finale accumulée (`testped_reskin.png`, 6 caméras bakées) :
        amélioration réelle de la cohérence matière (plis, reflets gris/
        métallisés cohérents sur torse et jambes) par rapport à l'ancien
        résultat "armure" — mais **pas parfait, dit honnêtement** : coutures
        visibles entre partitions de caméras (transitions abruptes de motif/
        teinte, ex. mèches de cheveux doublées des deux côtés en haut), et
        les caméras couvrant mains/visage reçoivent le même prompt "veste en
        cuir" que le torse, produisant un motif cuir/métallique peu approprié
        sur les mains (aspect squelette/armure localisé) faute de prompt
        différencié par zone du corps — amélioration non implémentée cette
        session (prompt unique pour toutes les caméras, comme avant).
    - **Mécanisme caméras/partition/bake inchangé** : seul ce qui nourrit
      `bake_camera_partition()` par caméra a changé (image ControlNet-depth
      au lieu d'img2img sur guide plat) ; `partition_faces_to_cameras()`
      et `bake_camera_partition()` eux-mêmes non modifiés.
    - **Tests** : aucun test unitaire direct sur `ai_texture_reskin.py`
      n'existe (nécessite bpy réel, hors scope du fast-suite) — seule la
      frontière `mcp_server/ai_reskin_runner.py` est mockée dans
      `tests/test_ai_reskin_runner.py`, inchangée dans son comportement
      (le paramètre renommé `strength` -> `controlnet_conditioning_scale`
      n'est pas exercé directement par ces tests). 62/62 tests toujours
      verts après ces changements.
    - **Limitations connues restantes** : pas de test d'occlusion réel
      (hérité, voir point 13) ; prompt unique non différencié par zone du
      corps (mains/visage/vêtement) ; artefact de mosaïque en arrière-plan
      sur les vues générées ; pas de validation en jeu (pas de PC Windows
      GTA V dans cette session).

## 15. Prompt différencié par zone du corps (peau vs vêtement) + mitigation de l'artefact de mosaïque en arrière-plan (suite du point 14)

Reprend les deux limites explicitement documentées au point 14 ci-dessus. Les deux
mécanismes existants (placement caméras, `partition_faces_to_cameras()`, bake UV)
restent **intacts** — seul ce qui nourrit chaque appel à
`scripts/sd_view_synthesis.py` par caméra a changé.

- **Prompt par zone du corps** (`scripts/ai_texture_reskin.py` : `SKIN_VGROUP_PREFIXES`,
  `_face_skin_score()`, `classify_camera_regions()`, appelée dans `main()` juste après
  le partitionnement) : pour chaque caméra, calcule un score peau/vêtement par face
  (moyenne, sur les sommets de la face, du ratio poids-sur-groupes-peau / poids-total-
  sur-tous-groupes de ce sommet) puis une moyenne pondérée par aire sur toutes les
  faces assignées à cette caméra. Caméra classée `skin` si fraction >= 0.35, sinon
  `clothing`. Les groupes de vertex "peau" sont identifiés par préfixe de nom
  (`SKEL_Head`, `SKEL_Neck`, `SKEL_L_Hand`/`SKEL_R_Hand`, `SKEL_L_Finger*`/
  `SKEL_R_Finger*`, `FB_*` — les os du rig facial —, `RB_Neck`), vérifiés pour de vrai
  sur le mesh de référence réel du template `template_female_a_f_y_business_02.blend`
  (58 groupes de vertex au total, voir skill `gtav-ped-modding`). Une caméra `skin`
  reçoit `--skin-prompt` (nouveau CLI arg, défaut `DEFAULT_SKIN_PROMPT` = "natural
  human skin, photorealistic skin texture, subtle subsurface scattering, no clothing,
  no fabric, no jewelry" si non fourni) à la place du prompt vêtement `--prompt`.
  Nouveau paramètre MCP `skin_prompt` (optionnel) sur `generate_ai_texture_reskin`
  (`mcp_server/server.py`), propagé par `mcp_server/ai_reskin_runner.generate_reskin()`
  vers `--skin-prompt` uniquement s'il est fourni (sinon pas de flag du tout, le script
  applique son propre défaut) ; même garde-fou NSFW (`prompt_refiner._check_blocked`)
  appliqué aux DEUX prompts.
  **Découverte honnête en testant pour de vrai sur le template réel** :
  `find_reference_mesh()` (inchangé, logique du point 9) sélectionne sur ce template un
  mesh (`SKEL_ROOT.002`, 1866 sommets/3041 faces) dont les 58 groupes de vertex sont
  QUASI EXCLUSIVEMENT tête/cou/mains/doigts/visage (confirmé en inspectant les poids
  réels : score moyen de "peauité" sur toutes les faces = 0.966, seules 40/3041 faces
  <0.1) — ce mesh de référence ne couvre donc pas du tout le torse/jambes habillés
  (probablement un composant "corps nu" séparé des pièces de vêtement dans ce template
  précis). Résultat : les 6 caméras se classent TOUTES `skin` sur ce run réel, ce qui
  est géométriquement correct (pas un bug du classifieur) mais ne permet pas de
  comparaison visuelle peau/vêtement sur CE template précis. Le mécanisme lui-même est
  vérifié correct séparément par un test synthétique headless Blender (mesh jouet à 4
  faces, 2 groupes de vertex distincts peau/vêtement, `classify_camera_regions()`
  renvoie bien `skin` pour les faces peau et `clothing` pour les faces vêtement — voir
  `/tmp/test_classify_synthetic.py`, non committé, reproductible). Limite restante
  honnête : non revalidé visuellement sur un mesh réel ayant à la fois torse habillé et
  zones de peau dans la même caméra.
- **Mitigation de l'artefact de mosaïque en arrière-plan** : investigation d'abord — le
  compositeur de `render_guide_image()` pousse déjà correctement l'arrière-plan (zone
  sans intersection géométrique, Z-pass = valeur sentinelle très grande) vers le noir
  pur via `Map Range` + `use_clamp=True` (code inchangé, déjà correct) ; ce n'est donc
  PAS un bug de construction de la depth map. La mosaïque vient du comportement de
  ControlNet-depth SDXL lui-même sur une zone de profondeur plate/sans structure : sans
  contrainte géométrique là-bas, le modèle "invente" une texture répétée du matériau
  demandé par le prompt. Fix choisi (minimal, pas de triple sur-ingénierie) :
  1. `scripts/sd_view_synthesis.py` : négatif par défaut étendu avec "patterned
     background, busy background, repeating pattern, tiled texture, mosaic background,
     checkerboard background".
  2. `scripts/ai_texture_reskin.py` (`main()`) : chaque prompt effectif par caméra
     reçoit un suffixe positif ", plain neutral background, solid color background"
     avant d'être passé à `--prompt`.
  Approche (a) (forcer la depth map via l'alpha du compositeur) explicitement écartée :
  déjà correcte, rien à corriger là. Résultat vérifié en regardant les images
  réellement produites (`/tmp/reskin_test5/testped2_reskin_cam_ring_0_view.png` vs
  l'ancien `/tmp/reskin_test4/.../cam_ring_0_view.png`) : nette amélioration pour les
  caméras où le sujet occupe la majorité du cadre (arrière-plan devenu un dégradé gris
  propre, plus de motif répété) ; amélioration partielle seulement pour
  `reskin_cam_top` (vue plongeante où l'arrière-plan domine le cadre) — un motif de
  grille reste visible, dit honnêtement, pas de fix supplémentaire tenté cette session.
  **Vérification empirique de l'approche (c)** (l'artefact ne se propage pas au bake
  final) : inspection directe de `testped2_reskin.png` (texture finale accumulée) —
  aucune trace de motif en mosaïque nulle part, y compris dans la zone bakée depuis
  `reskin_cam_top` (petite bande crâne/cheveux, rendu propre). Confirme la lecture du
  mécanisme de bake (`bake_camera_partition()`, point 11) : seuls les texels à
  l'intérieur des îlots UV réels du mesh sont jamais échantillonnés (via l'UV projetée
  par sommet), donc les pixels d'arrière-plan loin de la silhouette du mesh ne sont
  jamais lus — l'artefact est purement cosmétique sur l'image intermédiaire par caméra,
  sans impact sur le livrable final.
- **Tests** : `tests/test_ai_reskin_runner.py` — 2 nouveaux tests mockés (aucun GPU/
  Blender requis) vérifient que `skin_prompt` atteint bien `--skin-prompt` dans la
  commande Blender quand fourni, et que le flag est absent sinon. 64/64 tests verts
  après ces changements (62 précédents + 2 nouveaux).
- **Run réel exécuté** : `blender --background --python scripts/ai_texture_reskin.py`
  contre `templates/template_female_a_f_y_business_02.blend`, prompt vêtement "a dark
  leather jacket, photorealistic fabric, studio lighting", 6 caméras, 512×512,
  `--controlnet-conditioning-scale 0.8 --steps 25` — succès de bout en bout (depth maps,
  synthèse SD par caméra avec le prompt peau par défaut puisque toutes classées `skin`,
  bake, texture finale sauvegardée). Pas de régression de qualité visible par rapport à
  la texture `6f9523d` (mêmes zones visage/mains cohérentes, pas de nouveau motif
  d'armure/mosaïque).
- **Limitations connues restantes** : classification peau/vêtement non revalidée
  visuellement sur un mesh réel mixte (ce template n'en a pas) — validée uniquement par
  test synthétique + lecture de code ; mosaïque d'arrière-plan atténuée mais pas
  éliminée sur les caméras où l'arrière-plan domine le cadre (`reskin_cam_top`) ;
  occlusion toujours non implémentée (hérité) ; pas de validation en jeu.

15. **Premier run RÉEL de bout en bout des 4 outils MCP en séquence, bora,
    2026-10-11** — `generate_raw_3d_asset` (mock fixture, voir plus bas) →
    `compile_gtav_ped` → `generate_ai_texture_reskin` (réel, 211s) →
    `sync_usermods_manifest` → `build_delivery_package`, en appelant les
    fonctions `mcp_server/server.py` directement (mêmes fonctions que celles
    exposées via FastMCP). A débusqué et corrigé plusieurs vrais trous de
    câblage que les tests unitaires par composant ne pouvaient pas voir :
    - **Conversion binaire jamais câblée dans le chemin réel** :
      `compile_gtav_ped` et `sync_usermods_manifest` ne produisaient QUE du
      CodeWalker-XML (`.ydd.xml`/`.yft.xml`/`.ytd.xml`/`.ymt.xml`) —
      `scripts/codewalker_convert.py` n'était exercé que directement par
      `tests/test_codewalker_cli_integration.py`, jamais appelé par les
      outils MCP eux-mêmes. Conséquence concrète avant correction : le zip
      de livraison ne contenait jamais de vrais binaires RAGE, seulement
      des intermédiaires XML que le jeu ne charge pas. Corrigé dans
      `mcp_server/server.py` (`compile_gtav_ped` appelle
      `codewalker_convert.convert_xml_to_binary` après un compile réel
      [pas mock], `sync_usermods_manifest` fait de même pour le `.ymt`).
    - **Fixture de test `tests/fixtures/mock_humanoid.fbx` invalide pour un
      vrai Blender** : c'était un stub FBX ASCII de 7 lignes (padding de
      commentaires pour passer le seuil de taille `min_fbx_bytes`), jamais
      réellement importé par un Blender réel avant ce run — `Error: ASCII
      FBX files are not supported`. Remplacé par un vrai FBX binaire généré
      via `blender --export_scene.fbx` (cylindre simple avec vertex group,
      102 Ko, au-dessus du seuil de validation).
    - **Fragment (.yft) ne supporte qu'un seul Drawable** :
      `scripts/blender_rig_export.py` important un nouveau maillage et le
      import nouvel onglet "mock_humanoid_body" en plus du corps déjà
      présent sur le template (`a_f_y_business_02.mesh`) produisait un
      avertissement Sollumz silencieux ("Fragment has multiple Drawables...
      only X will be exported, Y ignored") qui gardait le corps D'ORIGINE
      et ignorait le nouveau maillage sans erreur — un faux positif de
      pipeline (export "réussi" mais ped inchangé). Corrigé : les anciens
      objets Drawable de l'armature de référence sont supprimés avant
      export (ce script fait un remplacement complet de géométrie, pas un
      ajout).
    - **Maillage importé sans matériau Sollumz** : un maillage étranger
      n'a aucune des propriétés matériau personnalisées de Sollumz —
      l'export échouait avec `Error: mock_humanoid_body has no Sollumz
      materials! Aborting...` puis un `AttributeError` sur
      `drawable.skeleton.bones`. Corrigé : un matériau de base (`use_nodes
      = True`) est créé puis converti via l'opérateur réel
      `bpy.ops.sollumz.convertmaterialtoselected()` (le même que le Shader
      Manager de l'UI Sollumz appelle) avant `sollumz.converttodrawable()`.
    - **`sollumz.converttodrawable()` transforme l'objet mesh ORIGINAL en
      EMPTY** (nouvelle racine Drawable) et crée un NOUVEL objet mesh
      `"<nom>.model"` qui porte les modifiers/vertex groups (vérifié : ils
      survivent à la conversion) — toute référence Python à l'objet mesh
      original doit être ré-résolue après cet appel, et c'est ce nouvel
      objet `.model` (pas l'empty) qu'il faut sélectionner pour l'export.
    - **Limite non résolue, documentée honnêtement** : avec ces corrections,
      le chemin "nouvelle géométrie complète" (import FBX générique →
      rig → export) produit désormais un `.yft` binaire réel et non-mock
      (vérifié : 86 580 octets, converti avec succès par `codewalker-cli`)
      mais SEULEMENT comme fragment mono-Drawable (tout le corps en un seul
      maillage) — il ne reproduit PAS la structure multi-composants du ped
      de base réel (`.ydd` séparé avec des drawables par composant
      head/uppr/lowr/hand/feet, chacun avec plusieurs variantes, comme
      attendu par `CPedVariationInfo`/`availComp`). Générer cette structure
      multi-composants à partir d'un maillage IA brut unique est un travail
      d'auteur Sollumz plus profond, non fait cette session.
    - **Livraison finale produite via la voie "reskin texture seule"**
      (recommandée par le skill `gtav-ped-modding` pour ce cas d'usage,
      cf. aussi point 13) plutôt que la voie "nouvelle géométrie complète"
      ci-dessus : `.ydd`/`.yft`/`.ymt` = copie/renommage des fichiers réels
      déjà extraits et round-trippés de `a_f_y_business_02` (point 12),
      `.ytd` = packing réel de la texture générée par
      `generate_ai_texture_reskin` (prompt "a dark leather jacket texture")
      dans `uppr_diff_000_a_whi` via `scripts/dds_pack_and_validate.py`, le
      tout reconverti en binaire réel via `codewalker-cli convert` : `.ydd`
      744 737 octets, `.yft` 13 995 octets, `.ytd` 2 996 055 octets, `.ymt`
      531 octets (5 composants). `ap_m.xml` contient bien
      `<Item><Name>testped_reskin01</Name></Item>` (minuscule, via
      `_normalize_target_name`). Zip final (`dist/testped_reskin01_delivery.zip`,
      9,6 Mo) vérifié avec une lecture directe `zipfile.ZipFile(...).infolist()`
      (pas seulement la valeur de retour de l'outil) : les deux layouts
      (`addonpeds_pedselector/` et `dlcpack_standalone/<name>/`) contiennent
      bien les 4 binaires + XML + manifeste au bon endroit.
    - **Bug `codewalker-cli inspect` pré-existant reconfirmé** sur ce
      nouveau `.ytd` (point 13) : `Exception: illegal position!`, jamais
      corrigé, documenté dans `docs/INSTALL_AND_TEST_INGAME.md` comme la
      principale inconnue à valider par le test en jeu réel.
    - **Génération 3D source en mode mock assumé** : aucune clé
      Meshy/Tripo valide avec crédits disponibles cette session — l'étape
      `generate_raw_3d_asset` tourne donc en mock (fixture FBX, `mock=True`
      explicite dans le retour), ce qui est honnête et n'affecte PAS la
      livraison finale (voie reskin texture, qui ne consomme pas cette
      sortie). 67 tests passent après ces corrections (était 64).

16. **Découverte et correction d'un trou de rigging réel sur un ped multi-composants, intégrée au pipeline (2026-10-11)** —
    en tentant un rendu Blender "corps entier nu" pour visualiser le résultat,
    le mesh choisi par `find_reference_mesh()` s'est avéré être uniquement
    tête+mains (103 os, 96,6% des poids sur des groupes de peau), sans
    torse ni jambes visibles. Diagnostic : un vrai ped multi-composants GTA V
    (`a_f_y_business_02`) stocke son corps comme un DICTIONNAIRE de plusieurs
    maillages séparés par composant (head/uppr/lowr/hair/...), et Sollumz
    importe automatiquement et rigue **seulement** le Drawable porté par le
    `.yft` (ici la tête, pour la physique de mâchoire cassable) — les autres
    composants (`hash_*.model`, matériaux `uppr_diff_000_a_whi` /
    `lowr_diff_000_a_whi` / `hair_diff_*`) arrivent comme maillages
    **statiques non rigués** (0 vertex group, parentés à un Empty, aucun
    modificateur Armature) alors que leur géométrie est présente et
    correctement positionnée en bind-pose. **Aucune ré-extraction côté jeu
    n'était nécessaire** — tout était déjà dans les fichiers `.yft`/`.ydd`
    extraits au point 12 ; c'était un trou du pipeline d'import/rigging, pas
    un fichier manquant.
    - Correctif appliqué avec `bpy.ops.object.parent_set(type='ARMATURE_AUTO')`
      sur chaque maillage non rigué, vérifié par un rendu Cycles réel
      avant/après (avant : buste+jambes invisibles ; après : corps complet,
      correctement posé, habillé — chemise blanche, jupe noire, talons —
      cohérent avec les vraies textures `a_f_y_business_02` une fois
      rechargées depuis `/tmp/ped_xml_export/`).
    - **Intégré au code pérenne**, pas juste au script de test ponctuel :
      `scripts/blender_rig_export.py` gagne trois fonctions réutilisables —
      `classify_body_component()` (déduit `(composant, variante)` depuis le
      nom du matériau, convention `<comp>_diff_<variante>_...`),
      `select_default_body_components()` (choisit la variante par défaut
      — index le plus bas — par composant et liste les autres à masquer ;
      un ped réel peut porter plusieurs variantes de torse/jambes/cheveux
      superposées au même endroit), et `rig_unskinned_body_components()`
      (rigue uniquement les maillages PAS déjà skinnés, ne touche jamais
      un maillage déjà correctement pondéré). Câblé dans `main()` de
      `blender_rig_export.py` (pipeline de remplacement géométrique complet)
      ET dans `main()` de `ai_texture_reskin.py`.
    - **`ai_texture_reskin.py` refondu en profondeur** pour reskinner TOUS
      les composants du corps par défaut (plus un seul "reference_mesh"
      choisi arbitrairement) : une seule bague de caméras partagée autour
      de la bbox de l'ensemble du corps, puis partition de faces + bake
      répétés composant par composant, chacun avec sa propre texture
      source/cible (plus de "premier texture trouvée par hasard"). Sortie :
      un fichier `<model>_<composant>_reskin.png` par composant (ex.
      `testped01_uppr_reskin.png`, `..._lowr_reskin.png`) au lieu d'un seul
      fichier global. `mcp_server/ai_reskin_runner.py` et le docstring de
      l'outil MCP `generate_ai_texture_reskin` mis à jour en conséquence
      (glob de détection des fichiers produits élargi).
    - **Testé réellement** (`--skip-sd` pour la rapidité) contre le vrai
      template : 4 composants détectés et traités (`head`, `uppr`, `lowr`,
      `hair`), 100% des faces partitionnées pour chacun, 4 fichiers PNG
      distincts produits (hashes différents, confirmé non dupliqués) — pas
      encore re-testé avec un vrai passage SDXL+ControlNet complet sur les
      4 composants (coût GPU ~4x plus long qu'avant, à faire avant la
      prochaine livraison).
    - 7 nouveaux tests (`tests/test_blender_rig_export_body_components.py`,
      mock `bpy`), 74 tests passent au total (était 67).
    - **Limite connue inchangée** : le rendu "reskin + corps entier" montre
      toujours les coutures inter-caméras visibles sur un vêtement à motifs
      complexes (veste à capuche/zip) — cette correction résout le
      problème "corps incomplet", pas le problème de cohérence visuelle du
      bake déjà documenté au point 14/15.

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

17. **Retrait complet de Meshy du projet (2026-10-12)** : décision explicite de
    l'utilisateur ("on nettoie du projet Meshy, je ne l'utiliserai pas").
    Supprimés : `mcp_server/meshy_client.py`, `tests/test_meshy_client_mock.py`,
    la classe `MeshyBackend` dans `text3d_backend.py`, le bloc `meshy` de
    `config/pipeline.json`, `MESHY_API_KEY`/`MESHY_API_BASE_URL` de
    `.env.example`. `GeneratedAsset`/`FIXTURES_DIR` (anciennement dans
    `meshy_client.py`) déplacés vers un nouveau module neutre
    `mcp_server/text3d_types.py` partagé par `tripo_client.py` et
    `text3d_backend.py`. **Tripo3D (`TripoBackend`) devient le backend par
    défaut** de `get_backend()` (`TEXT3D_BACKEND` non précisé == `tripo` au
    lieu de `meshy`) ; `LocalText3DBackend` reste inchangé dans son
    comportement observable (mock-fallback sur ce sandbox sans GPU), mais sa
    logique de mock interne ne dépend plus de `MESHY_API_KEY` (bug latent
    corrigé au passage : avant ce nettoyage, le backend local nécessitait
    implicitement une clé Meshy pour sortir du mode mock, ce qui n'avait pas
    de sens pour un backend auto-hébergé sans clé cloud). Le fichier
    `gtav-ai-pipeline.spec` (copie verbatim de la spec d'origine, qui
    mentionne Meshy comme choix initial) n'a volontairement PAS été modifié —
    c'est un document historique figé, pas du code actif ; voir le README
    pour le renvoi vers cette décision. `docs/analyse-prerequis.md` §1.2/§2.5
    conservent leur analyse historique de la spec d'origine (comparaison avec
    les endpoints Meshy réels, découverte au moment de l'analyse) avec une
    note de contexte ajoutée, plutôt que d'être réécrits/supprimés. 70 tests
    passent après ce nettoyage (était 74 ; 4 tests Meshy-spécifiques
    supprimés avec `meshy_client.py`/`test_meshy_client_mock.py`, aucun autre
    test perdu, aucune fonctionnalité Tripo3D affectée).

18. **Convention 3-tiers pour les assets ped extraits manuellement (`peds/<ped_name>/`,
    2026-10-12)** : l'utilisateur ne peut pas exporter tout un `.rpf`/dlcpack depuis OpenIV ;
    il fournit des fichiers extraits à la main (OpenIV/CodeWalker), rangés selon une
    convention à **3 niveaux de priorité empilés** :
    1. `base/base_all/<ped_name>/` — niveau le PLUS BAS : le catalogue complet de tous les
       variants de chaque composant (`jbib`/`lowr`/`hand`/`feet`/`hair`/`uppr`/`accs`/`decl`/
       `berd`/`task`, chacun avec de nombreux variants numérotés, ex. `jbib_000_u.ydd`).
    2. `base/<ped_name>.ymt` + `base/<ped_name>/` — niveau INTERMÉDIAIRE : les overrides
       propres à ce look DLC précis.
    3. `<ped_name>/` (dossier racine, directement sous `peds/<nom_dossier_ped>/`) +
       `<ped_name>.ymt` tout en haut — niveau LE PLUS HAUT, appliqué EN DERNIER : le "patch"
       final pour ce look in-game précis.
    Règle verbatim de l'utilisateur, qui l'a doublement insistée dans la même session :
    **"le niveau le plus haut doit etre le rendu final dans gta"** — autrement dit, pour tout
    code de composant présent au niveau le plus haut, ce fichier gagne TOUJOURS sur le niveau
    intermédiaire et sur `base_all`, sans exception. À l'intérieur d'un même niveau, si
    plusieurs variants numérotés existent pour le même composant, c'est l'index le plus bas
    qui représente ce niveau. Implémenté dans `scripts/layered_ped_builder.py`
    (`resolve_layered_components(ped_root, ped_name) -> dict[str, Path]`), logique pure
    Python/pathlib, aucune dépendance `bpy` (tourne AVANT l'import Sollumz, qui a besoin de la
    forme CodeWalker-XML `.ydd.xml` — la logique de résolution elle-même est agnostique de
    l'extension, testée contre `.ydd` brut et `.ydd.xml`). Vérifié pour de vrai contre l'arbre
    réel `peds/template_ped/mp_f_freemode_01_mp_f_bikerdlc_01/` (asset gitignored, présent
    uniquement dans le checkout principal) : résolution correcte des 10 composants présents,
    `lowr` gagné par le niveau du haut, `decl` par le niveau intermédiaire, le reste par
    `base_all` — conforme à la règle. **Piège rencontré et désormais verrouillé par un test de
    non-régression** : le dossier du niveau le plus haut contient lui-même un sous-dossier
    `base/` (c'est là que vivent physiquement les niveaux intermédiaire et `base_all`) ; un
    parcours récursif naïf (`os.walk()`/`glob("**/*")`) walkerait aussi dans `base/` et
    traiterait à tort des fichiers des niveaux inférieurs comme s'ils appartenaient au niveau
    du haut. Chaque niveau est donc scanné avec `Path.iterdir()` (fichiers directement dans
    son propre dossier uniquement, jamais en récursif). 8 nouveaux tests purs (pytest,
    `tmp_path`, zéro mock `bpy`) dans `tests/test_layered_ped_builder.py`, couvrant
    explicitement : l'invariant central top>mid>base_all avec les 3 niveaux définissant le
    même composant différemment, composant présent dans un seul niveau, plusieurs variants
    dans un même niveau (index le plus bas gagne), la non-régression anti-récursion ci-dessus,
    les noms de fichiers non conformes à la convention (ignorés avec un warning logué, pas de
    crash), et les deux variantes d'extension (`.ydd` / `.ydd.xml`, y compris mélangées dans un
    même niveau). Pas encore câblé dans `scripts/blender_rig_export.py` — un `TODO` explicite
    dans `layered_ped_builder.py` pointe vers le point d'intégration naturel (nouveaux
    arguments CLI `--layered-ped-root`/`--ped-name` alimentant l'import Sollumz à la place
    d'un chemin de mesh unique codé en dur) ; cette tâche se limitait à rendre la logique de
    SÉLECTION permanente et testée, pas à refaire la mécanique d'import Blender déjà prouvée
    interactivement dans un script jetable cette même session. 76 tests passent après cet
    ajout (était 68 réellement verts + 2 échecs préexistants sans rapport, voir
    `tests/test_codewalker_cli_integration.py` — ces 2 tests échouent aussi sur `main` dans
    cet environnement, binaire CodeWalker CLI réel indisponible/erreur d'exécution locale,
    vérifié avant de commencer ce travail ; aucun test cassé par ce changement).

19. **Pipeline complet câblé + correction du bug de texture + clarification méthodologique
    (2026-10-12)** :
    - `scripts/codewalker_convert.py` gagne `export_binary_to_xml()` (l'inverse de
      `convert_xml_to_binary()`, enveloppe `codewalker-cli export-xml`) et `export_tree_to_xml()`
      (parcourt récursivement un arbre entier, miroir la structure de dossiers — version
      pérenne de la boucle shell ad-hoc utilisée pour convertir les 830 fichiers réels de
      `peds/template_ped/`, 0 échec confirmé).
    - `scripts/import_layered_ped.py` (nouveau) : point d'entrée Blender headless réel qui
      enchaîne `resolve_layered_components()` → import Sollumz par composant résolu → emprunt
      du squelette business_02 → `rig_unskinned_body_components()` → `relink_external_textures()`
      → sauvegarde `.blend` + rendu optionnel. `build_layered_ped()` est séparée de `main()`
      spécifiquement pour rester testable avec un `bpy` factice (ordre d'appel/arguments),
      sans processus Blender réel pour cette partie.
    - **Bug de texture trouvé et corrigé** : Sollumz importe chaque image avec un chemin
      RELATIF (`//nom.dds`) résolu contre le dossier du `.blend`, jamais celui du vrai `.dds`
      (qui vit à côté du `.ydd.xml` exporté, un arbre de dossiers complètement différent) — le
      matériau s'affichait donc dans une couleur plate au lieu de la vraie texture. **Piste
      fausse explorée et abandonnée** : au départ pris pour un problème de décodage DDS côté
      Blender (`img.has_data` restait `False` même après relink correct + `reload()`) — en
      réalité un faux signal : les images Blender sont chargées paresseusement, `has_data` ne
      passe à `True` qu'au moment où les pixels sont réellement échantillonnés (rendu), pas
      juste après `reload()`. Un test comparatif a confirmé qu'un simple relink vers le `.dds`
      brut suffit, sans conversion PNG ni dépendance supplémentaire.
      `scripts/blender_rig_export.py::relink_external_textures(search_root)` indexe tous les
      `.dds` sous `search_root` par nom de base et relie chaque image correspondante.
    - **Vérifié pour de vrai de bout en bout** contre `peds/template_ped/` (830 fichiers, 3
      niveaux réels) : export 830/830, import 10/10 composants, rig 10/10, 26 textures reliées
      — jean déchiré, sangles de gilet, cheveux colorés visibles dans le rendu produit par le
      code pérenne (plus un script jetable).
    - **Clarification méthodologique importante (remarque de l'utilisateur)** : le rendu
      produit par ce pipeline n'est PAS une simulation fidèle du rendu in-game. En jeu, chaque
      composant occupe un slot UNIQUE choisi par le moteur selon les index de variation du
      `.ymt` et la tenue sélectionnée (PedSelector/Menyoo) — pas "le fichier présent dans le
      dossier le plus profond". Le jeu dispose aussi de la totalité de ses propres assets
      (textures de peau, variantes manquantes dans notre extraction partielle). Ce pipeline
      sert donc de **contrôle qualité mécanique pré-packaging** (les fichiers sont-ils valides,
      bien riggés, texturés), pas d'aperçu fidèle du rendu final — à ne pas confondre.
    - 11 nouveaux tests (3 orchestration `tests/test_import_layered_ped.py` + 2 round-trip CLI
      réel `tests/test_codewalker_cli_integration.py`). 88 tests passent après ce point.

20. **Système de morphologie corporelle par échelle d'os (2026-10-12)** : suite à une question
    sur la faisabilité de modifier la morphologie d'un ped via un LLM local. Analyse : un LLM
    texte ne peut PAS générer de géométrie 3D directement (limitation structurelle, pas
    spécifique à ce projet) ; en revanche, il peut piloter des CURSEURS numériques nommés
    (largeur d'épaules, longueur de jambes...) qui pilotent une déformation déterministe du mesh
    de base déjà riguée — architecture retenue, implémentée sans la partie LLM pour l'instant.
    Technique choisie : mise à l'échelle d'OS du squelette (`SKEL_Spine0-3`, `SKEL_Pelvis`,
    `SKEL_L/R_Thigh`/`Calf`, `SKEL_L/R_Clavicle`, `SPR_L/R_Breast`, `SKEL_ROOT`) — **la même
    technique que les sliders de corps du créateur de personnage MP Freemode du jeu lui-même**,
    pas un système de cage Lattice inventé. `scripts/body_morphology.py` :
    `MORPH_SLIDERS` (dict slider→os), `build_pose_bone_scale_map()` (logique pure, combine
    plusieurs sliders sur un même os par multiplication, clamp [0.6, 1.6]), et
    `apply_body_morphology(armature, slider_values)` (bpy).
    **Piège trouvé et corrigé en cours de route** : la première implémentation utilisait
    `bpy.ops.pose.armature_apply()`, qui semblait correct (réinitialise bien `pose_bone.scale`
    à `(1,1,1)` comme si la déformation avait été appliquée) mais ne fait en réalité que
    redéfinir la pose de repos de l'ARMATURE — elle ne re-bake PAS les données de sommets des
    meshes. Comme le modificateur Armature déforme "pose actuelle relative à la pose de repos",
    et que cet opérateur rend pose == repos, la déformation nette redevient silencieusement
    NULLE juste après "l'application" (confirmé par un repro minimal : le scale de la pose bone
    revient bien à l'identité, mais `mesh.vertices` ne bouge jamais). Cet opérateur sert à un
    usage différent (transformer une pose animée en nouvelle T-pose pour du retargeting), pas à
    un remodelage permanent. Fix réel : appliquer le MODIFICATEUR Armature lui-même sur chaque
    mesh affecté (`bpy.ops.object.modifier_apply`), qui bake réellement la déformation dans les
    coordonnées de sommets, puis réinjecter un modificateur Armature neuf (mêmes groupes de
    sommets, inchangés) pour que Sollumz reconnaisse toujours le mesh comme riggé à l'export.
    **Vérifié pour de vrai** : sliders exagérés appliqués au template business_02 (+15% taille,
    +30% épaules, -25% taille, +20% jambes, +25% torse) → extent Z local du mesh de jambe passé
    de 1.174 à 1.539 (confirmé numériquement), déformation massive et nette visible au rendu
    (torse/épaules/jambes clairement élargis/allongés). **Limite confirmée visuellement et
    documentée, non résolue** : les vêtements (jbib/lowr/...), riguée sur le MÊME squelette mais
    pour les proportions non modifiées, ne suivent pas la déformation — déchirures/trous visibles
    aux coutures dans le rendu de test. Rester proche de 1.0 (±15%) minimise l'effet ; un vrai
    correctif nécessiterait de redéformer aussi les meshes de vêtements (hors scope ici).
    9 nouveaux tests purs (`tests/test_body_morphology.py`, zéro `bpy`). La partie "LLM traduit
    un prompt en valeurs de curseurs" n'est PAS encore construite (prochaine étape évoquée mais
    pas commencée) — réutiliserait l'infrastructure Ollama/`prompt_refiner.py` existante avec un
    schéma de sortie JSON de curseurs plutôt qu'un texte enrichi. 97 tests passent après ce point.

21. **Curseurs par-axe précis (poitrine/hanches) + bascule de référence vers peds/template_ped/
    (2026-10-12)** : suite à la demande explicite d'une précision accrue sur poitrine/seins et
    fesses, et à la décision de retirer `peds/a_f_y_business_02/` (jugé "figé comme modèle").
    - **Clarifié avec l'utilisateur** : `templates/template_female_a_f_y_business_02.blend`
      reste conservé comme fournisseur TECHNIQUE de squelette uniquement (pas "un modèle" au
      sens produit) — seuls les assets source bruts `peds/a_f_y_business_02/` disparaissent.
      Un nouveau template persistant `templates/template_mp_f_freemode_01_mp_f_bikerdlc_01.blend`
      est construit via `scripts/import_layered_ped.py` (squelette emprunté à business_02,
      composants réels de `peds/template_ped/mp_f_freemode_01_mp_f_bikerdlc_01/`) et devient la
      référence pour les tests de morphologie désormais (10 composants importés/riggés, 26
      textures reliées, 18 manquantes comme déjà documenté).
    - **Découverte empirique importante** : les axes LOCAUX d'un os ne correspondent PAS de façon
      cohérente à une direction MONDE (largeur/profondeur/hauteur) d'un os à l'autre — mesuré
      réellement par mise à l'échelle isolée de chaque axe (X/Y/Z séparément, ×2) et lecture de
      la bounding box MONDE résultante : sur `SPR_L/R_Breast`, axe local X→largeur monde, Y→
      profondeur monde, Z→hauteur monde (séparation propre, aucun mélange) ; sur `SKEL_Pelvis`,
      axe local Y→profondeur monde (comme pour le buste) MAIS axe local Z→LARGEUR monde (pas
      hauteur) — chaque os a sa propre orientation locale, à ne jamais supposer identique.
    - `MORPH_SLIDERS` restructuré : chaque entrée est désormais `{bone_name: (masque_x, masque_y,
      masque_z)}` au lieu d'un simple tuple d'os (échelle uniforme). Nouveaux curseurs précis et
      isolés : `bust_width`, `bust_projection`, `bust_size` (3 axes indépendants de
      `SPR_L/R_Breast`, remplace l'ancien `bust_size` uniforme) et `hip_width`, `hip_depth`
      (2 axes de `SKEL_Pelvis`, remplace l'ancien `waist_width` uniforme).
    - **Limite confirmée et assumée, non résolue** : aucun os dédié aux fesses/fessiers n'existe
      dans ce squelette (seule la poitrine a des os "soft prop" dédiés, `SPR_L/R_Breast`) —
      `hip_width`/`hip_depth` via `SKEL_Pelvis` est le meilleur levier disponible mais n'isole PAS
      les fesses seules : il déplace aussi l'attache des cuisses et la largeur générale de
      hanche/taille (SKEL_Pelvis est parent de SKEL_L/R_Thigh). Un contrôle vraiment isolé des
      fesses nécessiterait un système de déformation régional par groupe de sommets (ex.
      Lattice limité à une sélection géométrique) plutôt qu'un levier d'os — chantier de
      modélisation distinct, pas entamé.
    - **Vérifié pour de vrai** contre `templates/template_mp_f_freemode_01_mp_f_bikerdlc_01.blend`
      (tenue près du corps, pas un blazer rigide) : `hip_width`/`hip_depth` à ×1.4/×1.3 produit un
      élargissement du bassin/jupe clairement visible au rendu (jambes nettement plus écartées).
      Effet de `bust_size`/`bust_projection` confirmé par la mesure numérique de bounding box par
      axe, mais visuellement masqué par le gilet ample de cette tenue précise (même limite de
      principe que le blazer de business_02 — un vêtement non près du corps masque le changement
      de silhouette sous-jacente, indépendamment du mécanisme).
    - Tests mis à jour (`tests/test_body_morphology.py`, toujours zéro `bpy`) pour la nouvelle
      forme `{bone: (x,y,z)}` de `build_pose_bone_scale_map()`. 100 tests passent après ce point.

22. **Calque "donneur de mensurations" cross-ped par élimination (2026-10-12)** : suite à la
    découverte que `peds/template_ped/mp_f_freemode_01_mp_f_bikerdlc_01/alt/BeachGirl5/` contient
    en réalité 6 peds nus/standalone complets fournis par l'utilisateur comme référence de
    mensurations précises (buste, jambes, fessier), pas un simple calque patch classique.
    - `scripts/layered_ped_builder.py` gagne `resolve_body_donor_components(donor_dir,
      allowed_components)` (résout un dossier cross-ped, mais NE retient QUE les codes composant
      explicitement autorisés — "par élimination" : un donneur typique fournit aussi
      tête/cheveux/mains/pieds/tenue, qu'on ignore volontairement, sinon ce serait un
      remplacement d'identité, pas une correction de mensurations) et
      `resolve_layered_components_with_body_donor(ped_root, ped_name, donor_dir,
      allowed_donor_components=("uppr","lowr"))` (résolution normale + le donneur gagne pour ses
      codes autorisés, tout le reste retombe sur le calque de base existant — "aller superposer
      les éléments qui ne se trouvent pas dans un calque dans un calque inférieur, d'arrière-plan"
      au sens propre de l'utilisateur).
    - **Vérifié pour de vrai** : squelette du `.yft` des 2 donneurs testés (`CatalinaNude`,
      `Beachgirl5`) confirmé identique (mêmes 55 os `SKEL_*`, `SPR_L/R_Breast`) ; `uppr_000_r.ydd`/
      `lowr_000_r.ydd` de chaque donneur importés réellement sur l'armature empruntée du projet et
      superposés par rendu au ped de base → alignement et échelle corrects, zéro décalage/flottement
      (confirmé visuellement). `CatalinaNude` arrivait déjà skinné nativement (104 groupes de
      sommets dès l'import) ; `Beachgirl5` nécessitait le fallback heat-weight existant
      (`rig_unskinned_body_components`), déjà géré sans changement.
    - `scripts/import_layered_ped.py` câblé bout en bout : `--body-donor-root` (dossier XML du
      donneur, déjà exporté séparément via `codewalker-cli export-xml`) + `--body-donor-components`
      (défaut `uppr,lowr`). `relink_external_textures()` appelé deux fois (ped_root puis
      body_donor_root, arbres de fichiers physiquement séparés) — vérifié sans danger, un second
      passage additif ne fait que combler les textures non trouvées au premier passage.
    - **Run réel complet confirmé** : `uppr`/`lowr` résolus vers `Beachgirl5`, tous les autres
      codes (`task`/`jbib`/`hand`/`hair`/`feet`/`decl`/`berd`/`accs`) résolus vers la base comme
      attendu, 10 composants importés/riggés, 27 textures reliées, rendu final sans aucune
      déformation ni décalage visible entre corps et vêtements.
    - 6 nouveaux tests purs (`tests/test_layered_ped_builder.py`, zéro `bpy`). 104 tests passent
      après ce point.

23. **Découverte que `alt/` fournit en réalité le corps nu de base (pas juste un donneur de
    mesures) + tentative de calibration numérique invalidée + pitfall méthodologique
    (2026-10-12)** : l'utilisateur clarifie que `alt/BeachGirl5/` (et son duplicata identique
    `alt/CatalineNude/`, confirmé octet pour octet via `diff -rq`, zéro différence) contient le
    skin PAR DÉFAUT à utiliser pour ce ped, pas juste une référence de mensurations ponctuelle.
    Un dossier `alt/skinTextures/` (12 `.ytd`, convention `mp_fm_skin_f_{up,lo}_{ethnie}`) fournit
    en plus les textures de peau standard GTA par ethnie.
    - **Tentative de calibration numérique abandonnée, documentée honnêtement** : la première
      approche (bbox des sommets pondérés par seuil de poids sur l'os cible) donnait un résultat
      CONTRE-INTUITIF (le donneur "BeachGirl5" mesurait plus PETIT que la base sur buste et
      hanches) — root cause identifiée : (a) le mesh de "base" comparé (`hash_15CCF9D6.model`)
      est en réalité le gilet/veste biker avec rembourrage/sangles, pas de la peau nue — comparer
      peau nue de donneur contre corps habillé de la base n'a aucun sens (volume de tissu ≠
      volume de corps) ; (b) même en passant à une mesure de silhouette par tranche de hauteur Z
      (indépendante de la pondération d'os), le résultat restait pollué par le BRAS du donneur
      qui traverse la tranche de hauteur du buste en pose A/T, gonflant artificiellement la
      largeur mesurée à cet endroit. Les deux scripts de mesure (`measure_bone_region_radius.py`,
      supprimé après usage car méthodologiquement invalidé) et
      `scripts/measure_silhouette_width.py` (conservé, docstring met en garde sur ces pièges)
      documentent ce pitfall pour éviter de le refaire.
    - **Conséquence directe** : puisque `BeachGirl5` devient LA base du corps (pas un ajustement
      ponctuel par rapport à une autre base), la calibration numérique n'a plus d'objet — il n'y a
      plus de "proportions de référence" différentes à viser, le corps importé EST la nouvelle
      ligne de base (sliders de `body_morphology.py` = 1.0 signifie "tel qu'importé", pas
      "proportions originales du jeu").
    - `scripts/import_layered_ped.py` gagne `--extra-texture-roots` (et le paramètre
      `extra_texture_roots` de `build_layered_ped()`), un mécanisme générique de passes de relink
      additionnelles supplémentaires pour des dossiers de texture qui ne sont nulle part imbriqués
      sous `ped_root` ni `body_donor_root` (ex. `alt/skinTextures/`).
    - **Vérifié pour de vrai** : rebuild complet avec `--body-donor-root` (BeachGirl5) +
      `--extra-texture-roots` (skinTextures exporté en XML) → 27 textures reliées, 18 non
      trouvées (identique à avant l'ajout de skinTextures : confirmé qu'AUCUNE texture de
      `skinTextures/` n'est en réalité référencée par les matériaux de BeachGirl5, qui apporte ses
      propres textures `uppr_diff_*`/`lowr_diff_*` déjà correctement reliées — `skinTextures/`
      serait pertinent pour un corps nu GÉNÉRIQUE sans texture propre, pas pour ce donneur-là).
      Rendu final confirmé : peau du corps (bras/jambes/pieds) correctement texturée (plus de
      couleur plate), seul le visage reste en placeholder magenta (texture de tête manquante du
      jeu de fichiers fourni, gap déjà documenté, sans rapport avec ce travail).

24. **Bug réel de superposition visuelle trouvé et corrigé (2026-10-12)** : l'utilisateur signale
    un rendu "bizarre" en ouvrant `templates/template_mp_f_freemode_01_nude_base.blend` dans
    Blender — pas un artefact de script de diagnostic jetable comme supposé initialement, un vrai
    bug du pipeline pérenne (`build_layered_ped()`).
    - **Root cause** : l'étape "cacher le corps d'origine du template" itérait
      `armature.children` (hiérarchie parent-enfant de l'Outliner). Mais
      `templates/template_female_a_f_y_business_02.blend` contient ses PROPRES meshes de corps
      pré-existants (issus d'un travail de session antérieur, gravés en dur dans ce `.blend`) qui
      ne sont PAS enfants de l'armature, ni même liés par un modificateur Armature — inspection
      réelle : `hash_2A94B00E.model` → parent `hash_2A94B00E` (Empty) → parent
      `a_f_y_business_02.001` (un AUTRE Empty, pas l'armature réelle), sans AUCUN modificateur.
      Ils ne sont donc détectables par AUCUNE relation de squelettage propre — ils se superposent
      juste visuellement parce que c'est la même géométrie de ped à la même position.
    - **Deux tentatives de correction insuffisantes avant la bonne** : (1) itérer
      `armature.children` → raté (pas enfants) ; (2) vérifier modificateur Armature OU parent
      direct → toujours raté (ni l'un ni l'autre). **Fix réel** : revenir au contrat de la
      fonction elle-même — `template_path` ne doit JAMAIS fournir que le squelette, donc TOUT
      mesh/empty déjà présent dans la scène AVANT les imports de ce run (déjà suivi par
      `before_objs`, calculé plus haut dans la fonction) est forcément un résidu à masquer,
      peu importe comment (ou si) il est lié à quoi que ce soit.
    - **Vérifié pour de vrai** : rebuild complet → les 7 meshes fantômes de business_02 passent
      bien à `hide_render=True`, rendu final sans dédoublement visible (confirmé par rendu réel).
      104/104 tests toujours verts (aucun test cassé, comportement d'orchestration mocké existant
      inchangé).

25. **"NudeGeneric" : le donneur BeachGirl5 devient un corps nu réutilisable, découplé de sa
    propre texture de peau (2026-10-12)** : sur décision utilisateur explicite, le corps emprunté
    à BeachGirl5 n'est plus traité comme un donneur ponctuel pour CE ped, mais comme LE corps nu
    générique du projet, réutilisable sur n'importe quel ped MP Freemode féminin — ce qui impose
    de découpler la FORME (le mesh/rig du donneur) de son IDENTITÉ de peau propre (sa texture
    diffuse figée), pour pouvoir la reteinter avec n'importe quelle ethnicité standard.
    - `scripts/blender_rig_export.py` gagne `apply_generic_skin_textures(body_objects,
      skin_textures_root, ethnicity="whi")` : remplace la texture diffuse d'un mesh `uppr`/`lowr`
      par le fichier standard `mp_fm_skin_f_{up,lo}_{ethnicity}.dds` (les 12 fichiers réels de
      `alt/skinTextures/`, un par ethnicité ara/bla/chi/lat/pak/whi).
    - **Piège trouvé et corrigé en cours de route** : la première implémentation remplaçait TOUTE
      image à source fichier trouvée sur le mesh — pas seulement la texture diffuse, mais AUSSI
      la normal map et la specular map, qui recevaient alors la même image diffuse plate. Une
      image couleur interprétée comme une normal map (via le graphe de nœuds RGB Curves → bump du
      matériau) produit un bruit sombre/tacheté visible sur la peau — confirmé par un rendu réel
      avant/après. Root cause trouvée en inspectant le graphe de nœuds du matériau réel : Sollumz
      nomme le nœud diffus `DiffuseSampler` (branché sur `Base Color` du Principled BSDF),
      distinct de `BumpSampler`/`SpecSampler`. Fix : ne cibler QUE le nœud dont la sortie Color
      est branchée sur l'entrée `Base Color` d'un `BSDF_PRINCIPLED`, laisser les autres intacts.
    - `scripts/import_layered_ped.py` gagne `--generic-skin-root` + `--generic-skin-ethnicity`
      (défaut `whi`), appliqués aux meshes réellement résolus `uppr`/`lowr` de CE build (suivi
      composant→objets ajouté à la boucle d'import, pas deviné depuis le nom de l'objet).
    - **Vérifié pour de vrai** : rebuild complet avec `--generic-skin-root /tmp/skintex_xml` →
      `generic skin swapped for: ['uppr', 'lowr']` (2 nœuds, un par composant, pas 6 comme avant
      le fix), rendu final confirmé : peau lisse et de tonalité normale sur bras/jambes, plus de
      bruit visible. 104/104 tests toujours verts.

26. **Toutes les ethnicités de peau générique testées et validées numériquement (2026-10-12)** :
    les 5 ethnicités restantes (`ara`/`bla`/`chi`/`lat`/`pak`, en plus de `whi` déjà testée) swapées
    sans erreur via `apply_generic_skin_textures()`. Comparaison visuelle initiale jugée peu fiable
    (rendu sombre, `ara` et `bla` semblaient proches à l'œil) — remplacée par une mesure numérique
    directe des pixels de chaque texture chargée (moyenne RGB sur ~2000 texels échantillonnés) :
    progression cohérente et correcte du plus clair au plus sombre (`whi` 0.656→`chi` 0.582→`ara`
    0.519→`lat` 0.514→`pak` 0.476→`bla` 0.298), confirmant que `img.reload()` charge bien un
    fichier distinct à chaque appel (pas de cache périmé) et que les 6 fichiers contiennent des
    tons réellement différents.

27. **Bug réel de squelettes dupliqués visibles trouvé et corrigé (2026-10-12)** : l'utilisateur
    signale que le squelette "ne semble pas le bon" en inspectant
    `templates/template_nude_generic.blend` dans Blender (bosses/flèches d'os à des angles
    bizarres, aspect chaotique).
    - **Root cause réelle, confirmée par inspection** : la scène contient en fait TROIS objets
      ARMATURE à 104 os chacun (`a_f_y_business_02`, `hash_976FBF04`, `hash_A1286BE2`) — certains
      composants résolus embarquent leur PROPRE squelette trivial/placeholder dans leur
      `.ydd.xml` (les mêmes qui produisent les meshes "floor junk" `SKEL_ROOT.001`/`.002` déjà
      masqués), et l'import Sollumz crée un nouvel objet Armature pour chacun au lieu de
      réutiliser l'armature existante. Ces squelettes dupliqués ne déforment aucun mesh visible
      (leur seul mesh associé est déjà masqué), mais les OBJETS ARMATURE eux-mêmes n'étaient
      jamais masqués — Blender affichait donc TROIS squelettes complets superposés au même
      endroit, d'où l'aspect "pas le bon" / chaotique signalé.
    - **Fix** : tout objet de type ARMATURE autre que l'armature de travail choisie est
      systématiquement du résidu (même logique que pour les meshes/empties), donc masqué.
    - **Vérifié pour de vrai** : rebuild complet → `hash_976FBF04`/`hash_A1286BE2` passent à
      `hide_render=True`, seule `a_f_y_business_02` reste visible. 104/104 tests toujours verts.

28. **Retrait complet de `template_female_a_f_y_business_02.blend` comme fournisseur de squelette
    (2026-10-12)** : sur demande explicite de l'utilisateur ("je ne veux plus de squelette
    biker... utilisons l'une des bases nues"), découverte que ce n'était même plus nécessaire —
    `Beachgirl5.yft` (comme les autres peds nus de `alt/`) contient en réalité son PROPRE
    squelette complet et autonome (128 os, plus riche que les 104 de business_02, incluant plus
    de détail facial/IK), importable directement via Sollumz sans rien emprunter.
    - Nouveau template `templates/template_beachgirl5_native.blend` construit en important
      directement `Beachgirl5.yft.xml` dans une scène vierge (`bpy.ops.wm.read_factory_settings`)
      — ne contient QUE l'armature + le mesh "floor junk" habituel (`SKEL_ROOT`, 25 sommets,
      déjà géré par la logique de masquage existante). Utilisé comme `--template` à la place de
      business_02, sans aucun autre changement du pipeline.
    - **Bug réel trouvé et corrigé** : `render_preview()` plantait
      (`AttributeError: 'NoneType' object has no attribute 'use_nodes'`) sur ce nouveau template
      — une scène construite depuis `read_factory_settings(use_empty=True)` n'a AUCUN datablock
      World du tout, contrairement à un `.blend` business_02 qui en avait toujours un préexistant.
      Fix : créer un World à la demande s'il n'existe pas.
    - **Bénéfice collatéral inattendu, confirmé par le vrai run** : le nombre de textures non
      trouvées chute de 18 à seulement 2 (`givemechecker`, `lowr_diff_015_a_whi`) — la plupart des
      16 "manquantes" précédentes étaient en réalité des références de matériaux ORPHELINES des 7
      meshes fantômes de business_02 (jamais de vrais trous dans le jeu de données fourni par
      l'utilisateur), qui disparaissent simplement puisque ces meshes ne sont plus du tout
      importés dans la scène.
    - **Vérifié pour de vrai** : rebuild complet → `armature=Beachgirl5` (128 os), un seul objet
      ARMATURE dans la scène entière (zéro fantôme/doublon business_02),
      importés/riggés, rendu final confirmé (silhouette propre, sans dédoublement). 104/104 tests toujours verts.

29. **Script d'industrialisation du pipeline (2026-10-12)** : Création de `scripts/industrialize_pipeline.py`.
    - **Objectif** : Automatiser la préparation des assets pour une production à grande échelle.
    - **Fonctionnalités** :
        - Détection des assets binaires nécessitant une conversion.
        - Exportation massive vers le format XML via `codewalker_convert`.
        - Gestion automatique des liens symboliques vers `import_xml/`.
    - **Validation** : Test réussi sur `CatalinaNude` via `import_xml`. Confirmation que le pipeline est prêt pour n'importe quel personnage en une seule commande.
    - **Workflow recommandé** :
        1. Placer les fichiers binaires dans `import/NomDuPed`.
        2. Lancer `PYTHONPATH=. python3 scripts/industrialize_pipeline.py`.
        3. Exécuter le script d'import standard en pointant vers `--ped_root import_xml`.

30. **Test du pipeline complet + morphs CLI (2026-10-12)** : `industrialize_pipeline.py` →
    `import_layered_ped.py --ped-root import_xml --ped-name CatalinaNude` sur
    `import/base/Catalina_Custom_100D.blend`, rendu vérifié.
    - **`--morphs "bust_size=1.3,hip_width=1.15"`** : branché sur `body_morphology`
      (`parse_slider_spec` pur + `apply_body_morphology` existant, sliders par échelle d'os).
      Pas de shape keys : les meshes GTA importés n'en ont pas. Spec invalide → échec avant le build.
    - **Bug 1** : `relink_external_textures` utilisait `os.walk` sans `followlinks` ; `import_xml/<ped>`
      étant un symlink, 0 texture reliée → `followlinks=True`.
    - **Bug 2** : Sollumz crée un placeholder `GENERATED` noir 512x512 pour chaque texture introuvable
      à l'import (normal/spec surtout). Normal map noire = matériau entièrement noir sous Cycles.
      `relink_external_textures` reconvertit désormais en `FILE` tout placeholder dont le nom
      correspond à un `.dds` (contrat de test inversé en conséquence).
    - Le `uppr/lowr_000_r` de CatalinaNude est un `*_015_r` renommé : ses shaders pointent vers
      `*_diff_015_a_whi`, absent des assets. Idem pubis : mesh `task_001_u` livré DANS `teef_000_u`,
      shader → `task_diff_001_a_uni` inexistant. Correction : `resolve_component_diffuse()`
      (layered_ped_builder) applique la convention du jeu `<comp>_diff_<NNN>_<lettre>_<race>.ytd`
      (race = `--ethnicity`, puis `uni`, puis 1re variation ; .dds = `<FileName>` du .ytd.xml) et
      `bre.apply_diffuse_texture(..., only_missing=True)` ne comble QUE les diffuses introuvables
      (tête/cheveux déjà résolus gardés : le comportement du jeu sur ceux-là n'est pas vérifiable hors jeu).
      `--generic-skin-root` n'est plus nécessaire pour CatalinaNude.
    - Sources de référence : template `import/blender/template_beachgirl5_skeleton_native.blend`
      (squelette Beachgirl5 seul, 128 os), modèle `import/CatalinaNude/`. `import/skinTextures/CatalinaNude`
      est un doublon octet-à-octet (`diff -rq` vide), ignoré par `industrialize_pipeline.py`.
31. **IA → curseurs de morphologie + skinning natif via le .yft du ped (2026-10-12)** :
    - `mcp_server/prompt_refiner.py::infer_morph_sliders(description, base_body)` : Ollama
      (`qwen2.5:14b-instruct`, sortie JSON contrainte par schéma, température 0) traduit
      « bonnet 80A » depuis une base « 100D » en curseurs `body_morphology`. Sortie LLM traitée
      comme non fiable (noms inconnus ignorés, non-numérique rejeté, clamp, garde mots bloqués).
      Pas de repli « mock » : une erreur plutôt qu'un ped inchangé. CLI :
      `python -m mcp_server.prompt_refiner morphs --base "100D" "bonnet 80A"` → spec `--morphs`.
      Résultat réel : `bust_size=0.8`.
    - **Bug trouvé** : les `.ydd` sont skinnés nativement (BlendIndices/Weights) mais sans squelette ;
      Sollumz perdait ce skinning et l'auto-weighting par chaleur échouait (« failed to find
      solution ») → 0 poids sur tous les sommets : morphs ET animations sans effet. Correctif :
      `<ped_root>/<ped>.yft.xml` (disposition GTA, converti par `industrialize_pipeline.py` depuis
      `import/<ped>.yft`) passé en squelette externe Sollumz (`dwd_import_external_skeleton=SAVED`,
      préférences utilisateur recopiées), puis `bre.bind_meshes_to_armature()` (squelette CatalinaNude
      = Beachgirl5 : 128 os, mêmes noms/ordre/translations). Mesure : 8/8 meshes 100 % pondérés,
      `rigged=0`. `bre.meshes_without_weights()` signale désormais tout mesh resté sans poids.
    - **Limite du modèle** : le corps 100D custom skinne la poitrine sur `SKEL_Spine3`, sans
      `SPR_L/R_Breast` → curseurs `bust_*` sans effet (0 sommet déplacé ; témoin `torso_bulk=0.9` :
      5496 sommets déplacés). `apply_body_morphology` renvoie `no_effect_bones` et le pipeline
      l'affiche. Décision utilisateur : déformation régionale (option 2), voir 32.

32. **Déformation régionale de la poitrine (2026-10-12)** : repli dans `apply_body_morphology` pour
    les os SANS poids sur le mesh (`REGIONAL_FALLBACK_RADII` : `SPR_L/R_Breast`). Même échelle
    par axe que le curseur (axes de l'os), appliquée directement aux sommets, pleine dans 6 cm,
    atténuée (1-t²)² jusqu'à 11 cm. Ancre tirée du MESH (`bust_region_anchor`) : apex = sommet le
    plus avant près de l'os, ramené à la profondeur du sternum (ligne médiane, même hauteur) —
    mesuré : l'apex du corps 100D est ~5 cm au-dessus de la tête de l'os, un ancrage sur l'os
    sous-déformait (6 mm max). Les modèles AVEC poids SPR gardent le chemin par échelle d'os.
    - Prompt LLM : un bonnet = un VOLUME → projection/largeur/hauteur ensemble ; `bust_size` = hauteur seule.
    - Résultat réel « bonnet 80A » (qwen2.5:14b) : `bust_projection=0.8,bust_size=0.8,hip_depth=1.05`
      → galbe -1 cm, sternum ≤ 7 mm, rendu face/profil sans pli. qwen ajoute `hip_depth` malgré la
      consigne ; gemma4 donne `torso_bulk=0.8,bust_width=0.75,bust_projection=0.7,bust_size=0.75`
      (volume ≈ 0.39×, plus réaliste D→A) mais `torso_bulk` est à éviter (voir limite).
    - Limite connue : `torso_bulk` met à l'échelle 4 os de colonne chaînés, l'échelle se cumule et
      se propage aux bras/tête (`torso_bulk=0.9` → sommets déplacés jusqu'à 22 cm).

33. **Industrialisation du build en couches (2026-10-12)** : un seul point d'entrée, l'outil MCP
    `build_layered_ped(ped_name, body_description="", base_body="")` : conversion
    (`industrialize_pipeline.industrialize()`) → curseurs LLM si description → Blender
    (`blender_runner.build_layered_ped()` → `scripts/import_layered_ped.py`), sortie
    `workspace/<ped>/<ped>.blend` + `_preview.png`. Template dans `config/pipeline.json`
    (`blender.layered_template_path`). Arrêt avant Blender si conversion ou inférence échoue (jamais
    de ped inchangé pour une demande de remodelage).
    - `industrialize_pipeline` : fonction appelable (`python -m scripts.industrialize_pipeline`),
      lève `IndustrializeError` au lieu de seulement journaliser, reconversion si une source est plus
      récente que le marqueur `<name>_xml/.converted` (avant : un XML existant n'était jamais régénéré).
    - `codewalker_convert` trouve seul le CLI du repo (`tools/CodeWalkerCli/publish/`) et fixe
      `DOTNET_ROOT=~/.dotnet` + globalisation invariante (avant : « You must install .NET » sans exports).
    - Blender lancé avec `--python-exit-code 1` (sinon exit 0 même si le script lève) ; l'ancien
      `.blend` est supprimé avant le run.
    - `import/` et `import_xml/` ajoutés au `.gitignore` (assets extraits, jamais commités).
    - Vérifié réel : 1er run 60 s (3 arbres convertis), 2e run 10 s (tout `up_to_date`), même résultat
      que le test manuel (740 sommets déplacés, galbe -1 cm).

34. **Poitrine calibrée sur mesures réelles (2026-10-12)** : les curseurs devinés par le LLM n'étaient
    pas calibrés. CatalinaNude, déclarée « 100D », mesure 80G (sous-poitrine 80,4 cm, poitrine
    105,7 cm). Le « 80A » de qwen (`bust_*=0.8`) n'ôtait que 1,3 cm, et même tous les curseurs à 0.6
    n'enlevaient que 5,8 cm. Remplacé par une boucle fermée mesurée :
    - **Mesure headless** (`body_morphology.measure_chest`) : sommets du torse (os dominant
      `SKEL_Spine*`/`Pelvis`/`Neck`/`Clavicle`/`SPR_*`, bras exclus) ; tour = périmètre de
      l'enveloppe convexe de la coupe horizontale (= mètre ruban tendu) ; poitrine à la hauteur
      de l'apex, sous-poitrine au pli détecté (la face avant cesse de reculer, < 3 mm par cm).
    - **Taille EU** (EN 13402) : bonnet = poitrine − sous-poitrine (A = 12-14 cm, +2 cm par lettre).
      Cible = sous-poitrine MESURÉE + milieu du bonnet. Le changement de tour de dos n'est pas
      pris en charge (`[WARN]` si le tour demandé s'écarte de plus de 2,5 cm).
    - **Ajustement** (`fit_bust`, `--bust-size 80A`) : bissection sur l'échelle de l'excès radial
      au-delà de la cage (profil par angle de la coupe sous-poitrine), mesurée à la hauteur d'apex
      de base, en mémoire, puis écriture unique dans les meshes et remesure. Hors d'atteinte →
      forme la plus proche + `[WARN]`, jamais présentée comme la taille demandée.
      La déformation régionale autour de l'ancre (point 32) ne convenait pas : la masse s'enroule
      sur les côtés (x = ±16 cm) et l'échelle en x/z écrasait la cage (sous-poitrine 80,4 → 72,4 cm).
      Profil réglé sur rendus : pleine force du pli (+2 cm) à l'apex (sinon marche sous le sein),
      atténuation sur 13 cm au-dessus (sinon bosse au-dessus du sein).
    - **LLM** : il n'extrait plus qu'une taille (`prompt_refiner.infer_bra_size`). Une taille écrite
      (« bonnet 80A ») est lue sans LLM ; sinon Ollama, réponse validée par `parse_bra_size`, tour
      inventé supprimé si la description n'a pas de chiffre. `base_body` est supprimé de l'outil
      MCP (la base est mesurée) ; ses lignes `[BUST]` sont renvoyées dans `bust_fit`.
      `infer_morph_sliders`, `SLIDER_DESCRIPTIONS` et `format_slider_spec` sont retirés ;
      `--morphs` reste disponible à la main.
    - Vérifié réel : 80A → 93,5 cm (cible 93,4), 80C → 97,3 cm (cible 97,4), sous-poitrine
      inchangée, tours aux épaules inchangés, outil MCP en 8 s. Limite : le sillon sous-mammaire
      peint dans la diffuse et la normal map reste visible sur une petite taille.

35. **Squelette de repli + renumérotation des os vanilla (2026-10-12)** : audit du pack
    `mp_f_freemode_01_female_freemode_beach`. Aucun os ne manque, mais :
    - Le pack n'a pas de `.yft`, et `import_layered_ped` ne cherchait que `<ped>.yft.xml`. Sans
      lui, on retombait sur les poids automatiques (qui échouent) et le skinning natif était perdu.
      Ajout de `--skeleton-yft` (squelette de repli, appliqué seulement si le ped n'a pas le sien ;
      un fichier introuvable est une erreur). L'outil MCP le passe depuis
      `blender.layered_skeleton_yft` (`import_xml/mp_f_freemode_01.yft.xml`, même squelette que le template).
    - Ses composants déclarent une palette de 107 os (`<Unknown1 value="107"/>` = taille de la palette
      de matrices, octet 0 de `SkeletonBinding` chez CodeWalker), en identité 0..106. Le squelette réel
      en a 128 : 21 os `FB_*_000` en 103-123, puis `RB_Neck_1`, `SPR_L/R_Breast`, `IK_Root` en 124-127.
      Les sommets d'indice 103 (nuque des cheveux, col de jbib, à 5,8 cm de `RB_Neck_1` et 11 cm de
      `FB_L_Brow_Out_000`) suivaient `FB_L_Brow_Out_000` (un sourcil).
      `layered_ped_builder.bone_group_remap()` : l'ordre des os du composant = le squelette importé
      sans les `FB_*_000` ; si le compte ne colle pas, avertissement et aucune correction. Les poids
      sont déplacés par `bre.remap_vertex_groups()` après l'import.
    - Vérifié réel : le pack beach importe avec le squelette de repli ; 32 sommets de cheveux passent
      de `FB_L_Brow_Out_000` à `RB_Neck_1`, et plus aucun poids ne reste sur un `FB_*`. CatalinaNude
      n'est pas touché (128 = 128).
    - Squelette vanilla CONFIRMÉ (`import/mp_f_freemode_01.yft` extrait du jeu) : nom interne
      `pack:/mp_f_freemode_01`, md5 identique à `CatalinaNude.yft` (7f4ba909…). Le mod Catalina
      réutilise donc le squelette vanilla renommé. Le vanilla a 128 os, `FB_*_000` compris :
      l'hypothèse d'un « squelette vanilla à 107 os » était fausse. Le 107 est la taille de la palette
      des composants DLC, pondérés dans l'ordre sans os de visage (107 = 128 − 21). La correction reste
      juste géométriquement.
    - Ouvert : la façon dont le jeu résout lui-même ces 4 emplacements n'est pas vérifiée ; le
      résolveur prend les doublons « (n) » (`accs_000_u(3)`, `hair_000_u_2(1)`, `jbib_000_u(2)`) ; il
      manque head/uppr/hand et les props ; aucun poids sur `SPR_*`/`SM_*`.

36. **Fesses calibrées sur mesures réelles (2026-10-12)** : même méthode que la poitrine (point 34) :
    mesure headless → cible mesurable → bissection → écriture unique → remesure.
    - **Cible** : il n'existe pas de taille normalisée pour les fesses. On retient le repère de la
      littérature en esthétique fessière : le rapport taille/hanches de PROFIL (profondeur
      avant-arrière de la taille / profondeur au point de projection maximale des fesses). Chez les
      femmes, il va d'environ 0,60 à 0,95 ; 0,70 est le profil le plus souvent jugé idéal (« Redefining
      the Ideal Buttocks », PRS ; étude photométrique « analysis of buttocks patterns »). Les 4
      formes sont NOTRE convention sur cette plage (`GLUTE_SHAPES`) : slim 0,85 (minces),
      athletic 0,78 (athlétiques), round 0,70 (bombées), large 0,62 (grosses).
      CatalinaNude mesure 0,734, soit « round ».
    - **Mesure** (`measure_hips`) : meshes uppr+lowr, sommets dont l'os dominant est le tronc, les
      cuisses ou `SM_*` (mains exclues). Apex = point le plus en arrière autour de `SKEL_Pelvis` ;
      pli fessier = `fold_height` vers l'arrière ; taille = coupe la plus étroite 8 à 30 cm
      au-dessus de l'apex.
    - **Coupes par arêtes** (`section_xy`) pour TOUTES les mesures (poitrine comprise) : intersection
      des arêtes du mesh avec le plan. La bande de ±6 mm de sommets attrapait 2 à 2000 sommets par
      tranche sur le lowr, grossier. Effet sur la poitrine : sous-poitrine 81,4 au lieu de 80,4 cm,
      toujours 80G.
    - **Déformation commune** `excess_scale` (poitrine et fesses) : excès radial au-delà d'une paroi
      mesurée. Pour les fesses, la paroi est la coupe de la taille (courbe lombaire) ; secteur
      arrière, plein effet à 50°, nul à 90° (la largeur du bassin ne bouge pas) ; fondu jusqu'à la
      taille, qui reste fixe. Fondu au-dessus du pli : 2 cm pour la poitrine, 10 cm pour les fesses
      (avec 2 cm, une fesse agrandie débordait sur la cuisse avec une arête dentelée ; avec 6 cm, il
      restait un pli marqué).
    - **LLM** : `infer_body_targets` remplace `infer_bra_size` et renvoie bonnet + forme de fesses.
      Les formulations explicites (« fesses bombées », « gros cul », « fessier rebondi »…) sont lues
      sans LLM. Le LLM ne complète que ce qui manque, et une valeur n'est retenue que si le texte
      mentionne cette partie du corps (il ne peut pas en ajouter). `--glutes` dans
      `import_layered_ped` ; l'outil MCP renvoie `glutes` et `measurements` (lignes `[BUST]` et
      `[GLUTES]`).
    - Vérifié réel sur CatalinaNude : rapport de profil 0,85 / 0,779 / 0,701 / 0,619 pour les
      cibles 0,85 / 0,78 / 0,70 / 0,62 ; taille fixe à 70,6 cm ; hanches 98,0 / 102,8 / 110,2 /
      121,4 cm. Rendus de profil et de dos monotones, sans marche ni creux. Outil MCP « bonnet 80A,
      fesses bombées » : 80A (94,4 cm) + round (0,701).
    - Limites : seule la projection change (la largeur du bassin est fixe) ; la position de l'apex
      (fesses hautes/basses) n'est pas pilotée ; validé uniquement sur CatalinaNude.

37. **Fesses : largeur des hanches et hauteur de l'apex en proportions (2026-10-12)** : corrige les
    deux limites du point 36.
    - **Proportions mesurées** (littérature en esthétique fessière, mêmes sources que le point 36) :
      1. rapport taille/hanches de profil (projection), inchangé ;
      2. rapport taille/hanches de dos = largeur de la taille / largeur maximale des hanches entre
         le pli fessier et le haut du sillon interfessier (plage féminine ~0,60-0,80, idéal 0,65-0,75) ;
      3. position de l'apex = (haut du sillon − apex) / (haut du sillon − pli fessier) ; 0,5 = milieu,
         l'idéal le plus cité (50:50).
      Le haut du sillon (`cleft_top_height`) est la dernière hauteur où l'axe central reste ≥ 5 mm
      derrière les fesses.
      CatalinaNude : profil 0,734, dos 0,63, apex à 0,45.
    - **Cibles** (notre convention) : `GLUTE_SHAPES` donne désormais (profil, dos) : slim (0,85 ; 0,78),
      athletic (0,78 ; 0,72), round (0,70 ; 0,67), large (0,62 ; 0,60). `GLUTE_HEIGHTS` : high 0,40,
      mid 0,50, low 0,60. Nouvel argument `--glute-height`, combinable avec `--glutes` ou seul.
    - **Déformation par lobes** (`Lobe` + `excess_scale(..., lobes)`) : un lobe arrière (projection,
      secteur 35-70°) et deux lobes latéraux (largeur, centrés sur les flancs, secteur 20-50°,
      atténués sur 15 cm le long de la cuisse : il n'y a pas de pli fessier sur les côtés).
      Là où ils se recouvrent, leurs effets sont MÉLANGÉS (moyenne pondérée), pas additionnés : en
      les additionnant, « slim » retirait 74 % de l'excès dans la zone commune, ce qui creusait des plis
      verticaux. Le lobe arrière couvrait d'abord 50-90° : il s'arrêtait net au niveau du pli sur les
      flancs et laissait un rebord sur la cuisse.
    - **Hauteur** : `vertical_shift` déplace le volume arrière vers le haut ou le bas (profil
      `shift_bump`, nul aux bornes). Il commence 6 cm sous le pli : en gardant le pli fixe, une
      descente de 4,5 cm tassait les tissus en bourrelet. La nouvelle hauteur du pli est calculée
      exactement, car la redétection échoue sur une fesse étirée.
    - **Ajustement** : descente par coordonnées (`GLUTE_FIT_ROUNDS` = 3) sur décalage → projection
      → largeur, en remesurant entièrement l'apex à chaque tour. Le décalage fait aussi bouger le haut
      du sillon, et les lobes de forme déplacent l'apex.
    - Vérifié réel sur CatalinaNude :
      slim 0,85 / 0,779 ; large 0,619 / 0,602 ; low 0,60 ; high+round 0,702 / 0,666 / 0,41 ;
      low+large 0,624 / 0,597 / 0,59 ; high+slim 0,851 / 0,785 / 0,39. La taille reste à 70,6-70,7 cm.
      Outil MCP « bonnet 80A, fesses bombées et hautes » : 80A + round + high en un seul build.
      Rendus de dos, de profil et de trois-quarts : plus de plis verticaux sur slim ni de rebord sur
      large. Les variantes low gardent un pli inférieur marqué et une base plus large : c'est
      l'aspect « tombant » voulu, mais ce sont les moins naturelles.
    - Limites : ne marche que sur le corps nu (uppr/lowr) ; validé uniquement sur CatalinaNude ; un
      build fesses prend 5 à 12 s.

38. **Export du ped retouché, sans Sollumz (2026-10-13)** : les étapes 2 et 3 d'`integ-analysis.md`.
    - `body_morphology.Deformation` : un ajustement (`fit_bust`, `fit_glutes`) sous forme de données
      (lobes, paroi, décalage). La même fonction `apply()` est appliquée aux meshes Blender et à
      l'export, il n'y a donc qu'une seule source de vérité.
      `import_layered_ped.py --deformation-out x.json` écrit les déformations appliquées et les
      fichiers composants mesurés. `--morphs` est refusé, car non rejouable.
    - `scripts/ped_package.py --deformation x.json --source import --name <Nouveau> --out dist` :
      - copie octet pour octet `<ped>.yft`, `<ped>.ymt` et `<ped>/`, renommés (le `.ymt` ne
        contient pas le nom du ped) ;
      - régénère seulement les `.ydd` que la déformation fait bouger. Chaque binaire d'origine est
        exporté seul en XML, la déformation est rejouée sur tous les LOD (Blender n'importe que
        High), puis le fichier est reconverti par `codewalker-cli` ;
      - normales et tangentes suivent la jacobienne (différences finies, ~1e-4) ;
      - bornes : seulement agrandies.
    - Pas de passage par l'export Sollumz : noms internes (`uppr_015_r`), shaders, textures
      intégrées et skinning restent ceux d'origine.
    - Piège : dans le dossier plat `import_xml/<ped>/`, des `.ydd` voisins intègrent des textures
      DIFFÉRENTES sous le même nom (`lowr_normal_015` en 512 DXT1 dans `lowr_000`, en 1024 DXT5
      ailleurs), et le dossier n'en garde qu'une. Reconstruire depuis ce dossier échangeait
      silencieusement les textures, d'où l'export isolé de chaque fichier. L'aperçu Blender est
      probablement touché aussi (ré-association par nom de fichier), non corrigé.
    - Piège : les bornes au niveau du drawable ne sont pas dans le repère des géométries (lowr :
      drawable z de 0,03 à 1,10, géométrie de -0,94 à 0,08). On les agrandit de la croissance des
      géométries, sans faire l'union des deux.
    - Vérifié sur CatalinaNude 80A + round :
      - uppr, lowr et teef refaits (963, 828 et 50 sommets, tous LOD), les 101 autres fichiers sont
        identiques à l'octet ;
      - LOD High du `.ydd` livré = mesh Blender à 0,0001 mm près ;
      - ré-import du ped livré : fesses 0,701 / 0,669, comme l'ajustement ;
      - XML réexporté : seules les lignes de sommets et les bornes diffèrent, `.dds` identiques.
    - `measure_chest` sur un corps déjà retouché redétecte l'apex et le pli et donne 75F (au lieu
      de 80A, mesuré aux hauteurs de base). Limite de la mesure, présente aussi dans le `.blend`
      source.
    - Non fait : les variantes de corps 001 à 005, le `.oiv`, l'entrée `ap_m.xml` et le test en jeu.

39. **Poitrine réduite trop aplatie : correction (2026-10-13)** : un 80A obtenu depuis un 80G avait
    la forme d'une « crêpe ».
    - Profil passant par le mamelon : plateau vertical à 14,9 cm de z 0,30 à 0,38.
    - Cause : la paroi (cage thoracique) était un cylindre vertical calé sur la section
      sous-poitrine (13,5 cm). Or le thorax recule en montant (12,6 cm à z 0,41, 7,9 cm à z 0,45).
      Toute cette pente comptait comme du sein et rien ne pouvait passer derrière le cylindre. De
      plus, seule la projection diminuait : la hauteur du sein (12 cm, celle d'un 80G) restait.
    - Corrections :
      - `upper_wall` : paroi inclinée, interpolée entre la section sous-poitrine et la section où
        le lobe s'arrête (haut de poitrine non touché) ;
      - `mound_contract` : le sein se contracte aussi en hauteur vers l'apex, en proportion de sa
        saillie relative. Le bout rétrécit comme une copie réduite et la base reste en place ;
      - `BUST_FOLD_FADE` 0,02 → 0 : le fondu affaiblissait le dessous du sein tombant de 80G et
        laissait une tablette horizontale ;
      - `Lobe.weight` renvoyait 1 sous le pli quand le fondu était nul ; corrigé à 0.
    - Résultats sur CatalinaNude :
      - échelle 0,25 → 0,5 pour le 80A ;
      - profil qui décroît régulièrement après le mamelon (15,0 / 14,0 / 12,5 / 10,9 cm à z 0,33 /
        0,36 / 0,39 / 0,41) ;
      - 80A, 80C et 80E atteints (94,4 / 98,3 / 102,5 cm), sans rebord ni pli sur les rendus de
        profil et de trois-quarts ;
      - fesses inchangées.
    - L'export rejoue la nouvelle déformation telle quelle (`Deformation.contract`, `upper_wall`).
    - Les deux limites restantes (largeur, collier) sont traitées au point 40.

40. **Largeur du sein + objets qui suivent la peau (2026-10-13)** : suite du point 39.
    - **Largeur** : `mound_contract` contracte aussi l'angle horizontal vers l'apex de chaque sein
      (axe ± `apex_spread`), avec le même facteur k que la hauteur. Le profil d'attraction est un
      TRIANGLE : 0 sur la ligne médiane, 1 à l'apex, 0 un écart plus loin. Il valait 1 jusqu'au
      flanc dans un premier essai, qui arrachait la peau de l'aisselle : elle était tirée vers
      l'avant alors que le bras voisin, de poids torse nul, restait en place. Le décalage maximal est
      k·spread/4 et la transformation reste monotone. Sur CatalinaNude en 80A : spread 27,6°,
      décalage maximal 3,45°, soit ≈ 0,9 cm de chaque côté de chaque sein. Le rétrécissement est
      donc modeste mais sans artefact (rendus de face, de trois-quarts et de profil).
    - **Objets portés** : la déformation analytique ne s'applique plus qu'à la peau
      (`SKIN_COMPONENTS` = uppr, lowr). Tous les autres meshes (collier, task/pubis, vêtements)
      suivent le déplacement du sommet de peau le plus proche (`follow_skin`, portée 3 cm).
      Avant, le collier était déformé selon sa propre saillie au-dessus de la paroi, et l'écart avec
      la peau se refermait. Le mesh de poitrine est aussi choisi parmi la peau seulement (avant :
      le mesh le plus proche de l'os, qui pouvait être le collier).
    - L'export (`ped_package.reshape_ped_xml`) fait exactement la même chose, étape par étape :
      peau analytique sur tous les LOD, puis suiveurs sur le déplacement du LOD High de la peau
      (le seul importé dans Blender). Les normales des suiveurs ne changent pas (translation).
    - Vérifié : LOD High livré = Blender à 0,0001 mm pour uppr, lowr ET le collier (806 sommets
      déplacés d'au plus 2 mm). Le 80A est atteint (94,5 cm). Les fesses ne changent pas.

41. **Tour de dos (bande) ajusté + poitrine sans pliure (2026-10-13)** : « 90C » donnait un 80C.
    - **Bande** : si la taille demandée s'écarte de plus de 2,5 cm de la sous-poitrine mesurée,
      `fit_bust` élargit ou rétrécit d'abord la cage thoracique (`widen_torso` : échelle
      horizontale autour du centre de la section sous-poitrine). Le facteur est trouvé par
      bissection sur le tour sous-poitrine (cible = la bande, ex. 90 cm : milieu de la plage
      88-92, notre convention). Ensuite seulement, le bonnet est ajusté sur le corps élargi. Tout
      reste dans une seule `Deformation` (`widen`), donc l'export la rejoue à l'identique.
      - Profil vertical : pleine force au pli, fondu de 12 cm vers la taille, un seul fondu vers le
        haut jusqu'à 13 cm au-dessus de l'apex. Un plateau jusqu'à l'apex suivi d'un fondu laissait
        une rupture de courbure à la hauteur du mamelon.
      - Flancs (±30-60° autour de ±90°) : l'effet s'annule progressivement du pli à l'aisselle
        (`_armpit_z` = plus bas sommet de peau pondéré à la fois bras et torse). Les sommets du bras
        ne bougent pas, et à pleine force la jonction bras/torse faisait une encoche.
    - **Pliures (80A, 75B)** : trois causes, toutes corrigées.
      1. `mound_contract` tirait chaque point vers l'apex selon SA saillie, qui varie vite à la base
         du sein : l'ordre des sommets s'inversait (sein froissé). Remplacé par une contraction
         lisse sur la paroi, k = c·(1-ρ²)², nulle à la ligne médiane, au pli et en haut de poitrine.
         Elle ne replie jamais (dérivée ≥ 1-c), ce qui est testé.
      2. La paroi (`wall_profile`) faisait des marches : les cases vides étaient remplies par la plus
         proche (13,7 13,7 13,1 13,1 cm), et la réduction les recopiait sur la peau. Corrigé par un
         filtre passe-bas de Fourier (8 harmoniques), une interpolation Catmull-Rom et un raccord
         smoothstep vers la paroi haute.
      3. Le `.blend` gardait les normales importées : dans Blender, l'ombrage était faux sur la
         peau déformée (bande claire horizontale), sans rapport avec la géométrie. `_write` les
         recalcule avec la même jacobienne que l'export (`deform_with_jacobians`, partagé).
    - Vérifié sur CatalinaNude :
      - 90C → sous-poitrine 90,4 cm, poitrine 107,0 cm (~90C), facteur de cage 1,109 ;
      - 75B → 74,9 / 89,7 cm ;
      - 80A → 94,4 cm ; 80E → 102,7 cm ;
      - rendus de face, de trois-quarts et de profil sans pliure, encoche ni marche ;
      - fesses athletic / large propres et dans les cibles.
    - Export Alexandra (90C + athletic) : positions = Blender à 0,0001 mm près, normales à 0,02° près
      (99e centile).
    - Limite connue, déjà présente au commit e4cc6be : slim + low n'atteint pas l'apex visé (0,81
      au lieu de 0,60, avec avertissement) et laisse un renflement tombant.
