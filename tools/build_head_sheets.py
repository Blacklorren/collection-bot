"""Assemble des planches contact de tetes a partir des refs. (OFFLINE, dev only.)

Sert a decrire la coiffure de chaque joueur : contrairement a la couleur du maillot,
elle n'est pas mesurable en pixels (rase, boucle, chignon, dreads, calvitie...). Il
faut regarder. Ces planches permettent de le faire 12 joueurs a la fois au lieu de 115
images separees.

Chaque vignette porte son numero et le nom du joueur, ce qui permet de verifier que la
description retombe bien sur le bon joueur.

Entree : data/roster_s2.json + refs/<id>.png
Sortie : out/heads/planche_<n>.png  +  out/heads/index.json

Usage :
    python tools/build_head_sheets.py
    python tools/build_head_sheets.py --par-planche 12 --seulement-sans-description
"""
import argparse
import json
import os
import sys

from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from jersey_colors import SENTINELLE, masque_fond  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MANIFEST = os.path.join(ROOT, "data", "roster_s2.json")
HAIR = os.path.join(ROOT, "data", "hair.json")
OUT_DIR = os.path.join(ROOT, "out", "heads")
FONTS = os.path.join(ROOT, "assets", "fonts")

VIG_W, VIG_H = 240, 300      # vignette (tete + bandeau de legende)
LEGENDE_H = 26
COLS = 4


def police(taille):
    for f in ("Oswald-VariableFont_wght.ttf", "Anton-Regular.ttf"):
        p = os.path.join(FONTS, f)
        if os.path.exists(p):
            return ImageFont.truetype(p, taille)
    return ImageFont.load_default()


def bbox_sujet(im):
    """Boite du joueur, fond studio retire. -> (g, h, d, b) ou None."""
    marque = masque_fond(im.convert("RGB"))
    masque = Image.new("L", marque.size, 0)
    px_m, px_s = masque.load(), marque.load()
    w, h = marque.size
    for y in range(h):
        for x in range(w):
            if px_s[x, y] != SENTINELLE:
                px_m[x, y] = 255
    return masque.getbbox()


def crop_tete(chemin):
    """Cadre la tete : haut du sujet, elargi lateralement pour garder les cheveux."""
    im = Image.open(chemin).convert("RGBA")
    fond = Image.new("RGBA", im.size, (255, 255, 255, 255))
    im = Image.alpha_composite(fond, im).convert("RGB")
    bb = bbox_sujet(im)
    if not bb:
        bb = (0, 0, im.width, im.height)
    g, ht, d, b = bb
    haut = int((b - ht) * 0.42)              # les 42 % hauts du sujet = tete + epaules
    cx = (g + d) // 2
    demi = max(int(haut * 0.42), (d - g) // 4)
    boite = (max(0, cx - demi), max(0, ht - 8),
             min(im.width, cx + demi), min(im.height, ht + haut))
    return im.crop(boite)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--par-planche", type=int, default=12)
    ap.add_argument("--seulement-sans-description", action="store_true")
    args = ap.parse_args()

    players = [p for p in json.load(open(MANIFEST, encoding="utf-8"))
               if not p.get("sorti") and p.get("ref_file")]
    deja = json.load(open(HAIR, encoding="utf-8")) if os.path.exists(HAIR) else {}
    if args.seulement_sans_description:
        players = [p for p in players if p["id"] not in deja]
    players.sort(key=lambda p: p["id"])

    if not players:
        print("Rien a decrire.")
        return

    os.makedirs(OUT_DIR, exist_ok=True)
    for f in os.listdir(OUT_DIR):
        os.remove(os.path.join(OUT_DIR, f))

    f_num, f_nom = police(26), police(17)
    index, n_planche = {}, 0
    for debut in range(0, len(players), args.par_planche):
        lot = players[debut:debut + args.par_planche]
        n_planche += 1
        lignes = (len(lot) + COLS - 1) // COLS
        planche = Image.new("RGB", (COLS * VIG_W, lignes * VIG_H), (24, 26, 30))
        d = ImageDraw.Draw(planche)

        for i, p in enumerate(lot):
            tete = crop_tete(os.path.join(ROOT, p["ref_file"]))
            tete.thumbnail((VIG_W - 8, VIG_H - LEGENDE_H - 8), Image.LANCZOS)
            x = (i % COLS) * VIG_W + (VIG_W - tete.width) // 2
            y = (i // COLS) * VIG_H + 4
            planche.paste(tete, (x, y))
            num = i + 1
            d.rectangle([x, y, x + 34, y + 30], fill=(0, 0, 0))
            d.text((x + 8, y + 1), str(num), font=f_num, fill=(255, 220, 60))
            ty = (i // COLS) * VIG_H + VIG_H - LEGENDE_H + 2
            d.text(((i % COLS) * VIG_W + 8, ty), p["nom"][:26], font=f_nom, fill=(225, 230, 235))
            index[f"{n_planche}-{num}"] = p["id"]

        chemin = os.path.join(OUT_DIR, f"planche_{n_planche}.png")
        planche.save(chemin)
        print(f"  planche {n_planche} : {len(lot)} joueurs -> {os.path.relpath(chemin, ROOT)}")

    json.dump(index, open(os.path.join(OUT_DIR, "index.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    print(f"\n{len(players)} joueurs sur {n_planche} planches. Index : out/heads/index.json")


if __name__ == "__main__":
    main()
