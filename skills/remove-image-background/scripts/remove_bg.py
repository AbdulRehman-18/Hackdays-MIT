"""Remove the background from an image: python remove_bg.py <image> [output.png]"""
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: python remove_bg.py <image> [output.png]", file=sys.stderr)
        return 2
    src = Path(sys.argv[1]).expanduser()
    if not src.exists():
        print(f"error: {src} does not exist", file=sys.stderr)
        return 1
    try:
        from PIL import Image
        from rembg import remove
    except ImportError:
        print('error: install the dependencies first: pip install "rembg[cpu]" pillow', file=sys.stderr)
        return 1
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else src.with_name(f"{src.stem}_nobg.png")
    remove(Image.open(src)).save(out)
    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
