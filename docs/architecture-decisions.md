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
