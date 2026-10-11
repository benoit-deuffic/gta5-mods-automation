# gtav-ai-pipeline

Pipeline agentique Hermes : prompt texte -> Meshy (génération 3D) -> Blender
headless + Sollumz (rigging/export) -> CodeWalker (conversion binaire RAGE)
-> paquet d'installation (AddonPeds/PedSelector + dlcpack autonome) pour
GTA V.

Voir :
- `gtav-ai-pipeline.spec` — spécification technique d'origine (copie verbatim).
- `docs/analyse-prerequis.md` — audit des écarts/erreurs de la spec vs réalité
  des outils (Sollumz, CodeWalker, Meshy, Blender).
- `docs/architecture-decisions.md` — décisions prises avec l'utilisateur
  (orchestrateur, cible de livraison, transport, ped de base, mode mock).

## État actuel

Ce dépôt tourne en **mode mock complet** (pas de clé Meshy, pas de Blender ni
CodeWalker installés sur cette machine) : toute la logique pure-Python
(génération XML `ap_m.xml`, `.ymt`, packaging zip, validation) est réelle et
testée ; les étapes qui nécessitent des outils externes absents produisent des
artefacts placeholder explicitement marqués `[MOCK]` plutôt que de fabriquer
de faux binaires RAGE.

## Installation

```bash
uv venv --python 3.11 .venv
uv pip install --python .venv -e . --group dev
python tests/make_fixtures.py   # génère les fixtures mock Meshy
.venv/bin/pytest -v
```

## Lancer le serveur MCP

```bash
uv run --directory . python -m mcp_server.server
```

Déclaration dans `~/.hermes/config.yaml` (`mcp_servers`) :

```yaml
mcp_servers:
  gtav-ped-mcp:
    command: uv
    args: ["run", "--directory", "/chemin/vers/gtav-ai-pipeline", "python", "-m", "mcp_server.server"]
    env:
      PYTHONUNBUFFERED: "1"
```

## Outils MCP exposés

1. `refine_prompt_with_ollama(raw_prompt, target_name)` — enrichit le prompt via
   un LLM Ollama local (silhouette, tenue, coiffure, pose neutre), repli
   automatique sur le prompt original si Ollama indisponible. Garde-fou NSFW
   sur l'entrée et la sortie. **N'est pas un générateur 3D** (voir
   `docs/architecture-decisions.md` point 7).
2. `generate_raw_3d_asset(prompt, target_name, art_style="realistic")` — Meshy
   (ou mock, ou backend local si `TEXT3D_BACKEND=local`) -> FBX + textures
   validés dans `workspace/<target_name>/staging/`.
3. `compile_gtav_ped(target_name, fbx_path, gender="female")` — Blender headless
   (ou mock) -> `workspace/<target_name>/compiled/*.ydd.xml/.yft.xml/.ytd.xml`
   (CW-XML ; conversion binaire séparée, voir `scripts/codewalker_convert.py`).
4. `sync_usermods_manifest(target_name, ped_type, gender, is_streamed_gfx)` —
   génère le `.ymt` et met à jour `workspace/ap_m.xml`.
5. `build_delivery_package(target_name)` — construit
   `dist/<target_name>_delivery.zip` (AddonPeds/PedSelector + dlcpack autonome,
   avec README_INSTALL.txt dans chaque dossier).

## Génération 3D : Meshy, Tripo3D (cloud) ou local (futur poste GPU)

- Par défaut `TEXT3D_BACKEND=meshy` (ou mock si `MESHY_API_KEY` absent).
- `TEXT3D_BACKEND=tripo` bascule sur Tripo3D (`mcp_server/tripo_client.py`),
  vérifié en direct contre l'API réelle le 2026-10-11 (authentification OK,
  mais nécessite des crédits sur le compte — voir
  `docs/architecture-decisions.md` point 9). Produit un GLB unique (textures
  embarquées), pas encore importé par `blender_rig_export.py` (FBX seulement).
- `TEXT3D_BACKEND=local` bascule sur `mcp_server/text3d_backend.LocalText3DBackend`,
  pensé pour un poste avec GPU CUDA ≥8 Go VRAM (TripoSR / Hunyuan3D-2). Sur ce
  sandbox (pas de GPU), ce backend tombe systématiquement en mock, comme Meshy.
  `scripts/bootstrap_local_3d_env.sh <triposr|hunyuan3d-2>` prépare l'environnement
  Python sur le futur poste (non exécuté ici).

## Prompt enrichi par Ollama (LLM local)

```bash
scripts/bootstrap_ollama.sh            # détecte le matériel, installe Ollama,
                                        # tire le modèle adapté (testé sur ce
                                        # sandbox : qwen2.5:0.5b-instruct)
scripts/bootstrap_ollama.sh --dry-run  # affiche le plan sans rien installer
```

Le script écrit `config/ollama.json` (endpoint + modèle choisi), lu automatiquement
par `mcp_server/prompt_refiner.py`.

## Conversion XML → binaire RAGE (CodeWalker.Core) — fonctionne sous Linux

Contrairement à l'hypothèse prudente initiale, `CodeWalker.Core` compile et
s'exécute réellement sous Linux (.NET 8), vérifié de bout en bout (voir
`tools/CodeWalkerCli/README.md` et `docs/architecture-decisions.md` point 10) :

```bash
scripts/fetch_codewalker.sh
dotnet publish tools/CodeWalkerCli -c Release -r linux-x64 \
  --self-contained true -p:PublishSingleFile=true -o tools/CodeWalkerCli/publish
export CODEWALKER_CLI_PATH="$(pwd)/tools/CodeWalkerCli/publish/codewalker-cli"
```

Convertit réellement `.ytd.xml`/`.ydd.xml`/`.yft.xml` en binaires RAGE.
**`.ymt.xml` n'est pas encore converti** : le schéma généré par
`scripts/ymt_builder.py` ne correspond pas à la vraie structure
`CPedVariationInfo` (découvert en comparant au code source CodeWalker) — à
corriger avant tout usage réel, voir le détail dans
`docs/architecture-decisions.md` point 10.

## Pré-requis pour un run réel (non-mock)

Voir `docs/analyse-prerequis.md` §1 et `templates/README.md` :
- `MESHY_API_KEY` (contenu SFW uniquement).
- Blender 4.2+ avec l'extension Sollumz v2.9.0 activée.
- `templates/template_female_a_f_y_business_02.blend` /
  `template_male_a_m_y_business_02.blend` extraits de ta propre copie du jeu.
- Un CLI CodeWalker.Core pour la conversion XML -> binaire (`CODEWALKER_CLI_PATH`).
- Sur le PC de jeu Windows : GTA V Legacy, ScriptHookV + SHVDN3, AddonPeds/
  PedSelector installé, Menyoo (critères d'acceptation manuels, voir spec §8.4).
