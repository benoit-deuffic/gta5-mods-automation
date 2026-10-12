import bpy
import json
from pathlib import Path
import os

def main():
    # Use environment variable or hardcoded fallback
    input_path = Path(os.environ.get("MORPH_INPUT", "import/NudeGeneric/NudeGeneric.blend"))
    
    if not input_path.exists():
        print(json.dumps({"error": f"File {input_path} not found"}))
        return

    # Load the blend file
    # We use the internal bpy.ops to load the file correctly in background mode
    bpy.ops.wm.open_main_file(filepath=str(input_path))
    
    obj = None
    # Look for body object in collections or scene
    for o in bpy.data.objects:
        if o.name == "body":
            obj = o
            break
    
    if not obj or not obj.data.shape_keys:
        print(json.dumps({"error": "Body object or Shape Keys data not found"}))
        return

    # Collect shape keys
    shapes = []
    kb = obj.data.shape_keys.key_blocks
    for k in kb:
        if k.name != "Basis":
            shapes.append(k.name)

    print(json.dumps({"shapes": shapes}))

if __name__ == "__main__":
    main()
