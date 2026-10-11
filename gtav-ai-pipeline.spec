# Technical Specification: Autonomous AI-to-GTA V Ped Asset Pipeline

## 1. System Overview & Objectives

This specification defines an end-to-end autonomous pipeline orchestrated by **Hermes Agent**. The goal is to take high-level natural language prompts, generate raw 3D humanoid assets via external AI APIs (Meshy), process geometry and weight transfers headlessly in Blender using the Sollumz ecosystem, compile native GTA V formats (`.yft`, `.ydd`, `.ytd`), and register them dynamically into the game runtime via the "addon-pedselector" mod specification.

```
[User Prompt]
      │
      ▼
[Hermes Orchestrator] ──(HTTP REST)──> [Meshy 3D API]
      │                                       │ (.fbx + textures)
      │                                       ▼
      └──(MCP Tool Call)─────────────> [GTA V Blender MCP Server]
                                              │ (CLI Subprocess)
                                              ▼
                                      [Blender Headless + Sollumz]
                                              │ (.ydd, .yft, .ytd)
                                              ▼
                                      [Filesystem Delivery: UserMods/]
                                              │
                                              ▼
                                      [Update ap_m.xml Config]

```

## 2. Architecture & Components

### 2.1 Hermes Agent Harness & Orchestration Layer

* **Role**: Coordinates asynchronous tasks, tracks execution states, handles retries, and formats parameters.
* **Workflow Engine**: Dispatches calls across two external interfaces:
1. Meshy REST API client (task polling loop).
2. Model Context Protocol (MCP) tool caller executing Blender transformation routines.



### 2.2 Model Context Protocol (MCP) Server: `gtav-ped-mcp`

* **Runtime**: Python 3.11+ using the official `mcp` SDK (`FastMCP` or low-level Server interface) over `stdio`.
* **Exposed Tools**:
* `process_ped_asset(model_name: str, raw_fbx_path: str, texture_paths: list[str], gender: str = "female") -> dict`: Executes Blender in background mode, applies bones/vertex groups projection, exports `.ydd`/`.yft`/`.ytd`.
* `register_usermods_entry(model_name: str, target_dir: str, streamed_gfx: bool = true) -> dict`: Appends or updates the XML node `<Item>` in `ap_m.xml`.



### 2.3 Headless 3D & Rigging Engine

* **Runtime**: Blender 4.x / 3.6 LTS execution via CLI: `blender --background --python scripts/rig_and_export.py -- <args>`.
* **Core Plugin**: Sollumz (configured and pre-activated in Blender's user script directory).
* **Reference Rig (Template)**: Pre-rigged base skeleton templates (`template_female_ped.blend` or `template_male_ped.blend`) containing the GTA V bone armature and vertex weight topology.
* **Weight Mapping**: Geometry projection via Blender's `DataTransfer` modifier (`NEAREST_POLY` / `POLY_NEAREST_VN` with vertex group mapping).

### 2.4 Game Ingestion Specification (`addon-pedselector`)

* **Delivery Path**: Target root directory `UserMods/` under the GTA V installation root.
* **Artifacts per Ped**:
* `UserMods/<model_name>.yft`
* `UserMods/<model_name>.ymt`
* `UserMods/<model_name>.ydd` (or subfolder if streamed variations)
* `UserMods/<model_name>.ytd`


* **Manifest Sync**: Updating `UserMods/ap_m.xml` under `<CPedModelInfo__InitDataList><InitDatas>`.

## 3. Tool Interface Contracts (MCP Definition)

```json
{
  "tools": [
    {
      "name": "generate_raw_3d_asset",
      "description": "Submits a prompt to Meshy API, polls task status, and downloads FBX + texture maps to a staging directory.",
      "inputSchema": {
        "type": "object",
        "properties": {
          "prompt": { "type": "string", "description": "Text description of the humanoid character." },
          "art_style": { "type": "string", "enum": ["realistic", "stylized"], "default": "realistic" },
          "target_name": { "type": "string", "description": "Unique identifier for the ped (e.g., BeachSurfer01)." }
        },
        "required": ["prompt", "target_name"]
      }
    },
    {
      "name": "compile_gtav_ped",
      "description": "Invokes Blender headless to re-rig FBX onto GTA V ped skeleton, maps weights, and exports Sollumz archives.",
      "inputSchema": {
        "type": "object",
        "properties": {
          "target_name": { "type": "string" },
          "fbx_path": { "type": "string" },
          "gender": { "type": "string", "enum": ["female", "male"], "default": "female" }
        },
        "required": ["target_name", "fbx_path"]
      }
    },
    {
      "name": "sync_usermods_manifest",
      "description": "Appends model entry to UserMods/ap_m.xml and copies compiled assets to the game folder.",
      "inputSchema": {
        "type": "object",
        "properties": {
          "target_name": { "type": "string" },
          "ped_type": { "type": "string", "default": "CIVFEMALE" }
        },
        "required": ["target_name"]
      }
    }
  ]
}

```

## 4. Project Directory Structure

```
gtav-ai-pipeline/
├── hermes.json                  # Hermes agent runner configuration & prompt instructions
├── pyproject.toml               # Python project dependencies (managed with uv)
├── .env.example                 # Environment variables template
├── config/
│   └── pipeline.json            # Game paths, templates location, tolerances
├── templates/
│   ├── template_female.blend    # Reference ped skeleton with RAGE bone hierarchy
│   ├── template_male.blend
│   └── ap_m_item_template.xml   # Canonical XML snippet for new ped nodes
├── scripts/
│   ├── blender_rig_export.py    # Headless bpy script executed inside Blender
│   └── xml_manifest_updater.py  # Pure XML tree manipulator for ap_m.xml
├── mcp_server/
│   ├── __init__.py
│   ├── server.py                # Main FastMCP implementation
│   ├── meshy_client.py          # Asynchronous HTTP polling client for Meshy
│   └── validator.py             # Pre-flight and artifact sanity checker
└── tests/
    ├── test_xml_update.py
    └── test_blender_headless.py

```

## 5. Bootstrap & Configuration Files

### 5.1 `hermes.json`

Configuration spec for the Hermes orchestrator run loop:

```json
{
  "name": "gta-ped-architect",
  "version": "1.0.0",
  "description": "Autonomous builder transforming text prompts into playable GTA V peds.",
  "system_prompt": "You are an autonomous systems engineering agent. Your objective is to create complete GTA V peds from text prompts. Follow this sequence strictly: 1) Call generate_raw_3d_asset with the validated prompt; 2) Once downloaded, call compile_gtav_ped to run headless Blender rigging and Sollumz conversion; 3) Call sync_usermods_manifest to copy artifacts to the GTA V UserMods directory and update ap_m.xml; 4) Verify all output files exist before reporting completion.",
  "mcpServers": {
    "gtav-ped-mcp": {
      "command": "uv",
      "args": ["run", "--directory", "./mcp_server", "python", "-m", "mcp_server.server"],
      "env": {
        "PYTHONUNBUFFERED": "1"
      }
    }
  },
  "max_steps": 15,
  "telemetry": {
    "log_level": "INFO"
  }
}

```

### 5.2 `pyproject.toml`

Dependency specification optimized for `uv`:

```toml
[project]
name = "gtav-ai-pipeline"
version = "0.1.0"
description = "Hermes Agent tooling for automated GTA V ped generation"
readme = "README.md"
requires-python = ">=3.11"
dependencies = [
    "mcp[cli]>=1.3.0",
    "httpx>=0.27.0",
    "pydantic>=2.7.0",
    "pydantic-settings>=2.2.0",
    "defusedxml>=0.7.1",
    "rich>=13.7.0"
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.uv]
dev-dependencies = [
    "pytest>=8.2.0",
    "pytest-asyncio>=0.23.0"
]

```

### 5.3 `.env.example`

```bash
# Meshy AI credentials
MESHY_API_KEY="msy_your_meshy_api_key_here"
MESHY_API_BASE_URL="https://api.meshy.ai/v2.0"

# Blender Configuration
BLENDER_EXECUTABLE_PATH="/usr/bin/blender"
# Or Windows: "C:\\Program Files\\Blender Foundation\\Blender 4.2\\blender.exe"

# Target GTA V Installation Path
GTAV_INSTALL_ROOT="C:\\Program Files (x86)\\Steam\\steamapps\\common\\Grand Theft Auto V"
USERMODS_DIR="${GTAV_INSTALL_ROOT}\\UserMods"

# Working Staging Directory
PIPELINE_WORKSPACE_DIR="./workspace"

```

### 5.4 `config/pipeline.json`

```json
{
  "blender": {
    "timeout_seconds": 300,
    "template_female_path": "templates/template_female.blend",
    "template_male_path": "templates/template_male.blend",
    "data_transfer": {
      "max_distance": 0.05,
      "ray_radius": 0.0
    }
  },
  "pedselector": {
    "manifest_relative_path": "ap_m.xml",
    "default_relationship_group": "CIVFEMALE",
    "default_clipset": "move_f@generic"
  }
}

```

## 6. Headless Blender Execution Script (`scripts/blender_rig_export.py`)

This script runs non-interactively via Blender CLI to transfer weights and prepare Sollumz exports:

```python
import sys
import argparse
import bpy

def parse_args():
    # Split argv after '--' separator for user arguments
    raw_args = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    parser = argparse.ArgumentParser(description="Headless GTA V Rig & Weight Transfer")
    parser.add_argument("--template", required=True, help="Path to base ped template .blend")
    parser.add_argument("--source-fbx", required=True, help="Path to input raw FBX from Meshy")
    parser.add_argument("--output-dir", required=True, help="Destination folder for exported assets")
    parser.add_argument("--model-name", required=True, help="Model identifier")
    return parser.parse_args(raw_args)

def main():
    args = parse_args()

    # 1. Load reference template containing valid GTA V armature & Sollumz setup
    bpy.ops.wm.open_mainfile(filepath=args.template)
    
    reference_armature = bpy.data.objects.get("Armature") or bpy.data.objects.get("skel")
    reference_mesh = [obj for obj in bpy.data.objects if obj.type == 'MESH' and "head" in obj.name.lower() or "body" in obj.name.lower()][0]
    
    if not reference_armature or not reference_mesh:
        raise RuntimeError("Template file lacks expected Armature or Base Mesh objects.")

    # 2. Import raw generated FBX
    bpy.ops.import_scene.fbx(filepath=args.source_fbx, use_anim=False)
    imported_objects = bpy.context.selected_objects
    target_mesh = next((obj for obj in imported_objects if obj.type == 'MESH'), None)

    if not target_mesh:
        raise RuntimeError("No valid mesh geometry found in imported FBX.")

    # 3. Apply scales and clean transforms
    bpy.context.view_layer.objects.active = target_mesh
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)

    # 4. Attach DataTransfer Modifier for vertex weight projection
    modifier = target_mesh.modifiers.new(name="WeightTransfer", type='DATA_TRANSFER')
    modifier.object = reference_mesh
    modifier.use_vert_data = True
    modifier.data_types_verts = {'VGROUP_WEIGHTS'}
    modifier.vert_mapping = 'POLY_NEAREST_VN'

    # Generate matching vertex groups from reference armature
    for group in reference_mesh.vertex_groups:
        if group.name not in target_mesh.vertex_groups:
            target_mesh.vertex_groups.new(name=group.name)

    bpy.ops.object.modifier_apply(modifier=modifier.name)

    # 5. Parent to reference GTA V Armature
    target_mesh.parent = reference_armature
    armature_mod = target_mesh.modifiers.new(name="Armature", type='ARMATURE')
    armature_mod.object = reference_armature

    # 6. Execute Sollumz headless export commands
    # (Triggers Sollumz ped drawable dictionary / YDD & YFT exporter operators)
    export_path = f"{args.output_dir}/{args.model_name}"
    
    # Ensure Sollumz addon is activated
    if "sollumz" in bpy.context.preferences.addons:
        bpy.ops.sollumz.export_ped(filepath=export_path)
    else:
        print("[WARN] Sollumz addon not present. Exporting placeholder OBJ for pipeline validation.")
        bpy.ops.wm.obj_export(filepath=f"{export_path}.obj")

if __name__ == "__main__":
    main()

```

## 7. XML Manifest Manipulation (`scripts/xml_manifest_updater.py`)

This utility provides atomic modifications to `UserMods/ap_m.xml`:

```python
import xml.etree.ElementTree as ET
from pathlib import Path

def add_ped_to_manifest(xml_path: Path, model_name: str, ped_type: str = "CIVFEMALE") -> bool:
    tree = ET.parse(xml_path)
    root = tree.getroot()
    init_datas = root.find("InitDatas")
    
    if init_datas is None:
        raise ValueError("Invalid ap_m.xml: <InitDatas> node not found.")

    # Check for existing node
    for item in init_datas.findall("Item"):
        name_node = item.find("Name")
        if name_node is not None and name_node.text == model_name:
            print(f"Ped '{model_name}' already registered in {xml_path}.")
            return False

    # Build new <Item> element according to pedselector requirements
    new_item = ET.SubElement(init_datas, "Item")
    
    fields = [
        ("Name", model_name),
        ("PropsName", "null"),
        ("ClipDictionaryName", "move_f@generic"),
        ("BlendShapeFileName", "null"),
        ("ExpressionSetName", "expr_set_ambient_female"),
        ("ExpressionDictionaryName", "null"),
        ("ExpressionName", "null"),
        ("Pedtype", ped_type),
        ("MovementClipSet", "move_f@generic"),
        ("StrafeClipSet", "move_ped_strafing"),
        ("MovementToStrafeClipSet", "move_ped_to_strafe"),
        ("InjuredStrafeClipSet", "move_strafe_injured"),
        ("FullBodyDamageClipSet", "dam_ko"),
        ("AdditiveDamageClipSet", "dam_ad"),
        ("DefaultGestureClipSet", "ANIM_GROUP_GESTURE_F_GENERIC"),
        ("FacialClipsetGroupName", "facial_clipset_group_gen_female"),
        ("DefaultVisemeClipSet", "ANIM_GROUP_VISEMES_F_LO"),
        ("SidestepClipSet", "CLIP_SET_ID_INVALID"),
        ("PoseMatcherName", "Male"),
        ("PoseMatcherProneName", "Male_prone"),
        ("GetupSetHash", "NMBS_SLOW_GETUPS"),
        ("CreatureMetadataName", "null"),
        ("DecisionMakerName", "DEFAULT"),
        ("MotionTaskDataSetName", "STANDARD_PED"),
        ("DefaultTaskDataSetName", "STANDARD_PED"),
        ("PedCapsuleName", "STANDARD_FEMALE"),
        ("PedLayoutName", ""),
        ("PedComponentSetName", ""),
        ("PedComponentClothName", ""),
        ("PedIKSettingsName", ""),
        ("TaskDataName", ""),
        ("IsStreamedGfx", {"value": "false"}),
        ("AmbulanceShouldRespondTo", {"value": "true"}),
        ("CanRideBikeWithNoHelmet", {"value": "false"}),
        ("CanSpawnInCar", {"value": "true"}),
        ("IsHeadBlendPed", {"value": "false"}),
        ("bOnlyBulkyItemVariations", {"value": "false"}),
        ("RelationshipGroup", ped_type),
        ("NavCapabilitiesName", "STANDARD_PED"),
        ("PerceptionInfo", "DEFAULT_PERCEPTION"),
        ("DefaultBrawlingStyle", "BS_AI"),
        ("DefaultUnarmedWeapon", "WEAPON_UNARMED"),
        ("Personality", "DEFAULT"),
        ("CombatInfo", "DEFAULT"),
        ("VfxInfoName", "VFXPEDINFO_HUMAN_GENERIC"),
        ("AmbientClipsForFlee", "FLEE"),
        ("Radio1", "RADIO_GENRE_PUNK"),
        ("Radio2", "RADIO_GENRE_JAZZ"),
        ("PedVoiceGroup", "BAR_PERSON_PVG"),
        ("AbilityType", "SAT_NONE"),
        ("ThermalBehaviour", "TB_WARM"),
        ("SuperlodType", "SLOD_HUMAN"),
        ("ScenarioPopStreamingSlot", "SCENARIO_POP_STREAMING_NORMAL"),
        ("DefaultSpawningPreference", "DSP_NORMAL"),
        ("DefaultRemoveRangeMultiplier", {"value": "1.000000"}),
        ("AllowCloseSpawning", {"value": "false"})
    ]

    for tag, val in fields:
        sub = ET.SubElement(new_item, tag)
        if isinstance(val, dict):
            sub.attrib = val
        elif val:
            sub.text = val

    # Pretty format indenting
    ET.indent(tree, space="  ", level=0)
    
    # Atomic write to temporary file before replacing
    temp_path = xml_path.with_suffix(".tmp")
    tree.write(temp_path, encoding="utf-8", xml_declaration=True)
    temp_path.replace(xml_path)
    return True

```

## 8. Verification & Acceptance Criteria

1. **Meshy Generation Step**:
* API status transitions through `SUCCEEDED`.
* Resulting `.fbx` and PNG texture files validate against minimal byte thresholds (> 500 KB).


2. **Blender Headless Transformation**:
* Exit code `0` returned from the subprocess call.
* Target mesh contains all mandatory vertex groups present on the base template rig.
* Generated `.ydd` and `.yft` are exported to the target destination.


3. **Delivery Validation**:
* Target assets exist inside `<GTAV_INSTALL_ROOT>/UserMods/`.
* XML document `ap_m.xml` validates syntactically and contains an `<Item>` entry whose `<Name>` corresponds to `model_name`.


4. **Game Ingestion & Menyoo Interaction**:
* **Initial Injection**: The model becomes immediately playable via the PedSelector injection menu without causing texture allocation crashes.
* **Menyoo Cloning**: Once the player is transformed via PedSelector, the model must be stably duplicated using Menyoo's "Player Options > Clone Player" function.
* **Spooner Persistence**: The cloned NPC must be targetable by Menyoo's "Object Spooner", and its addition to the database ("Add to Database") must succeed for future sessions.
* **Animation Compatibility**: The skeleton generated and transferred by the Blender script must accept animations triggered by Menyoo or the "Forever Together" script without severe geometric distortion (e.g., no infinite polygon stretching during limb movement).