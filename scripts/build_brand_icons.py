"""Deterministically size the supplied Avelin logo for browser/PWA icons."""
from pathlib import Path
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
DESTINATION = ROOT / "frontend" / "public" / "brand"

if __name__ == "__main__":
    with Image.open(ROOT / "docs" / "dark_logo.png") as image:
        image = image.convert("RGB")
        for size, name in [(192,"icon-192.png"),(512,"icon-512.png"),(180,"apple-touch-icon.png")]:
            image.resize((size,size), Image.Resampling.LANCZOS).save(DESTINATION / name)
    with Image.open(ROOT / "docs" / "light_logo.png") as image:
        image.convert("RGB").resize((32,32),Image.Resampling.LANCZOS).save(DESTINATION / "favicon-32.png")
