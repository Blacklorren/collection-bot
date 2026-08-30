"""Planches de rapprochement rendus <-> portraits officiels, par club. (OFFLINE, dev only.)

Les fichiers telecharges depuis Midjourney ne portent qu'un UUID : rien n'indique quel
rendu correspond a quel joueur. Ces deux planches permettent de les apparier a l'oeil,
club par club, avant d'ecrire les affectations dans le manifest.

Sortie : out/match/<club>_rendus.png   les rendus, numerotes
         out/match/<club>_refs.png     les portraits LNH, nommes, tries par id
         out/match/<club>_ids.json     l'ordre des ids de la planche refs

Usage :
    python tools/build_match_sheets.py --club Limoges
    python tools/build_match_sheets.py --club Nantes --downloads "C:/Users/quent/Downloads/mj"
"""
import argparse
import glob
import json
import os
import re
import unicodedata

from PIL import Image, ImageDraw, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MANIFEST = os.path.join(ROOT, "data", "roster_s2.json")
OUT_DIR = os.path.join(ROOT, "out", "match")
FONTS = os.path.join(ROOT, "assets", "fonts")

VIG_W, VIG_H, COLS = 250, 340, 4


def slugify(s):
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Za-z0-9]+", "-", s).strip("-").lower()


def police(t):
    return ImageFont.truetype(os.path.join(FONTS, "Oswald-VariableFont_wght.ttf"), t)


def planche(vignettes, chemin, numerote):
    """vignettes : liste de (PIL.Image, legende)."""
    lignes = max(1, (len(vignettes) + COLS - 1) // COLS)
    img = Image.new("RGB", (COLS * VIG_W, lignes * VIG_H), (24, 26, 30))
    d = ImageDraw.Draw(img)
    for i, (im, leg) in enumerate(vignettes):
        im = im.copy()
        im.thumbnail((VIG_W - 8, VIG_H - 34), Image.LANCZOS)
        x = (i % COLS) * VIG_W + (VIG_W - im.width) // 2
        y = (i // COLS) * VIG_H + 4
        img.paste(im, (x, y))
        if numerote:
            d.rectangle([x, y, x + 44, y + 34], fill=(0, 0, 0))
            d.text((x + 10, y + 2), str(i + 1), font=police(28), fill=(255, 220, 60))
        if leg:
            d.text(((i % COLS) * VIG_W + 8, (i // COLS) * VIG_H + VIG_H - 26),
                   leg[:26], font=police(18), fill=(228, 233, 238))
    img.save(chemin)
    return img.size


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--club", required=True)
    ap.add_argument("--downloads", default=r"C:/Users/quent/Downloads/mj")
    args = ap.parse_args()

    club = args.club
    joueurs = sorted((p for p in json.load(open(MANIFEST, encoding="utf-8"))
                      if not p.get("sorti") and p["club"].lower() == club.lower()
                      and p.get("ref_file")), key=lambda p: p["id"])
    if not joueurs:
        raise SystemExit(f"Aucun joueur avec ref pour {club}.")
    club = joueurs[0]["club"]  # libelle canonique

    dossier = os.path.join(args.downloads, slugify(club))
    rendus = sorted(glob.glob(os.path.join(dossier, "*.png")))
    if not rendus:
        raise SystemExit(f"Aucun rendu dans {dossier}")

    os.makedirs(OUT_DIR, exist_ok=True)
    s = slugify(club)
    a = planche([(Image.open(f).convert("RGB"), "") for f in rendus],
                os.path.join(OUT_DIR, f"{s}_rendus.png"), numerote=True)
    b = planche([(Image.open(os.path.join(ROOT, p["ref_file"])).convert("RGB"), p["nom"])
                 for p in joueurs], os.path.join(OUT_DIR, f"{s}_refs.png"), numerote=False)
    json.dump([p["id"] for p in joueurs],
              open(os.path.join(OUT_DIR, f"{s}_ids.json"), "w", encoding="utf-8"), indent=1)

    print(f"{club} : {len(rendus)} rendus / {len(joueurs)} joueurs")
    if len(rendus) != len(joueurs):
        print(f"  ECART de {len(joueurs) - len(rendus)} : des joueurs resteront sans rendu")
    print(f"  out/match/{s}_rendus.png {a}")
    print(f"  out/match/{s}_refs.png   {b}")


if __name__ == "__main__":
    main()
