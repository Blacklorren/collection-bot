"""Batch de detourage des portraits -> PNG/WebP transparents (OFFLINE, dev only).

NE PAS lancer sur Railway : rembg + onnxruntime sont lourds. C'est une etape one-shot
sur une machine de dev. Le bot ne consomme ensuite que les fichiers produits.

Pre-requis :
    pip install rembg onnxruntime pillow

Usage :
    python tools/build_cutouts.py            # toutes les cartes manquantes
    python tools/build_cutouts.py --force    # re-detoure tout

Sortie : assets/cutouts/<id>.webp  (RGBA transparent, ~240 Ko/carte, ~63 Mo au total)
Le renderer v2 lira ces fichiers ; fallback sur le portrait brut si absent.
"""
import io
import json
import os
import sys
import urllib.request

from PIL import Image
from rembg import remove, new_session

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CARDS_JSON = os.path.join(ROOT, "cards.json")
OUT_DIR = os.path.join(ROOT, "assets", "cutouts")

# Parametres alpha-matting valides en test (bons contours cheveux, pas de halo)
MATTING = dict(alpha_matting=True,
               alpha_matting_foreground_threshold=240,
               alpha_matting_background_threshold=15,
               alpha_matting_erode_size=8)


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return Image.open(io.BytesIO(r.read()))


def main():
    force = "--force" in sys.argv
    os.makedirs(OUT_DIR, exist_ok=True)
    cards = json.load(open(CARDS_JSON, encoding="utf-8"))
    session = new_session("u2net")

    done = skipped = failed = 0
    fails = []
    for i, c in enumerate(cards, 1):
        out = os.path.join(OUT_DIR, f"{c['id']}.webp")
        if os.path.exists(out) and not force:
            skipped += 1
            continue
        try:
            src = fetch(c["image_url"]).convert("RGBA")  # gif anime -> 1re frame
            cut = remove(src, session=session, **MATTING)
            cut.save(out, "WEBP", quality=85, method=6)
            done += 1
            print(f"[{i}/{len(cards)}] OK  {c['id']} {c['nom']}")
        except Exception as e:
            failed += 1
            fails.append((c["id"], c["nom"], str(e)))
            print(f"[{i}/{len(cards)}] ERR {c['id']} {c['nom']} -> {e}")

    print(f"\nTermine : {done} faits, {skipped} deja presents, {failed} echecs.")
    if fails:
        print("Echecs (a relancer / verifier l'URL) :")
        for cid, nom, err in fails:
            print(f"  - {cid} {nom}: {err}")


if __name__ == "__main__":
    main()
