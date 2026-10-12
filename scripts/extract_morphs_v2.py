import bpy
import json
from pathlib import Path
import os

def main():
    input_path = Path("output_test_final.blend")
    if not input_path.exists():
        print(json.dumps({"error": f"File {input_path} not found"}))
        return

    # Direct load
    try:
        # This works in background mode if path is provided
        bpy.data.files.load(str(input_path))
    except Exception as e:
        print(json.dumps({"error": str(e)}))
        return

    obj = None
    for o in bpy.data.objects:
        if o.name == "body":
            obj = o
            break
    
    if not obj or not obj.data.shape_keys:
        print(json.dumps({"error": "Body object or Shape Keys data not found"}))
        return

    shapes = []
    kb = obj.data.shape_keys.key_blocks
    for k in kb:
        if k.name != "Basis":
            shapes.append(k.name)

    print(json.dumps({"shapes": shapes}))

if __name__ == "__main__":
    main()
