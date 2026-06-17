# This script extracts JPEG thumbnails from RAW image files.
# You can provide a single RAW file or a directory containing RAW files as input.
# Extracted thumbnails are saved to the specified output directory (defaulting to the Desktop).


import sys
from pathlib import Path

import rawpy

target = Path(sys.argv[1]).expanduser() if len(sys.argv) > 1 else Path(".").expanduser()
output_dir = Path(sys.argv[2]).expanduser() if len(sys.argv) > 2 else Path.home() / "Desktop"

if not output_dir.is_dir():
    print(f"❌ The output directory {output_dir} is not valid.")
    sys.exit(1)

if target.is_dir():
    thumbs_dir = output_dir / "thumbs"
    thumbs_dir.mkdir(parents=True, exist_ok=True)
    count = 0
    for raw_file in target.rglob("*"):
        try:
            with rawpy.imread(str(raw_file)) as raw:
                thumb = raw.extract_thumb()
                if isinstance(thumb.data, bytes):
                    jpg_output_path = thumbs_dir / f"{raw_file.stem}.jpg"
                    with open(jpg_output_path, "wb") as f:
                        f.write(thumb.data)
                    count += 1
                    # print(f"✅ Extracted thumbnail for {raw_file.name} saved at: {jpg_output_path}")
        except Exception as e:
            # print(f"❌ Failed to process {raw_file.name}: {e}")
            continue
    print(f"✅ Total {count} JPG thumbnails extracted.")

elif target.is_file():
    try:
        with rawpy.imread(str(target)) as raw:
            thumb = raw.extract_thumb()
            if isinstance(thumb.data, bytes):
                jpg_output_path = output_dir / f"{target.stem}.jpg"
                with open(jpg_output_path, "wb") as f:
                    f.write(thumb.data)
                print(f"✅ Extracted thumbnail for {target.name} saved at: {jpg_output_path}")
            else:
                print(f"❌ No JPG thumbnail found for {target.name}.")
    except Exception as e:
        print(f"❌ Failed to process {target.name}: {e}")

else:
    print("❌ The input is neither a valid RAW file nor a valid directory.")
