import bpy
import json
import os
from pathlib import Path

def main():
    # The file path must be absolute or relative to the current working directory
    # Since we run from the project root, relative is fine.
    input_path = Path("output_test_final.blend")
    if not input_path.exists():
        print(json.dumps({"error": f"File {input_path} not found"}))
        return

    # Open blend file
    # In background mode, we often use a simpler way or wait for it to load
    # If bpy.data.files.load fails, we use wm.open_main_file 
    # but many versions require context. Let's try the most robust way:
    try:
        # Trying a simpler load
        bpy.context.scene.file_path = str(input_path)
        bpy.ops.wm.open_main_file()
    except Exception:
        try:
            # Fallback for some versions
            bpy.ops.wm.open_main_file(filepath=str(input_path))
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
