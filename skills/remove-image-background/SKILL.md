---
name: remove-image-background
description: Cut the background out of a photo or image and save a transparent PNG next to the original, fully on device. Use when the user wants a background removed, a cut-out, a sticker or a transparent image.
license: MIT
compatibility: Requires Python 3.10+ with rembg and Pillow (pip install "rembg[cpu]" pillow); the U2-Net model downloads once (about 170 MB)
metadata:
  author: forge
  version: "1.0"
---

# Removing an image background

In Forge, call the `remove_background` tool with the image name, or an empty path for the newest image on
the Desktop. In any other agent, run the bundled script:

```
python scripts/remove_bg.py "<image path>"
```

It writes `<name>_nobg.png` next to the original and prints the output path.

- Inputs: PNG, JPG, WEBP. For HEIC, convert first: `sips -s format png in.heic --out in.png`.
- The original file is never modified.
- Works best on a clear subject (person, product, pet). Busy scenes may need a manual touch-up.
