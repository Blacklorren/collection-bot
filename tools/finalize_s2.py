"""Rendu Midjourney retenu -> cutout detoure -> carte finale. (OFFLINE, dev only.)

Dernier maillon de la chaine S2 : prend le fichier choisi dans data/roster_s2.json
(champ image_file), le detoure au rembg, l'ecrit dans assets/cutouts/<id>.webp puis
compose la carte avec compose_v2().

Le recadrage 4:5 n'est pas fait ici : _crop_to_ratio() s'en charge dans le renderer,
ce qui garantit que la carte de preview est exactement celle que le bot produira.

Pre-requis : pip install rembg onnxruntime pillow

Entree : data/roster_s2.json (image_file renseigne)
Sortie : assets/cutouts/<id>.webp   (detoure, consomme par le bot)
         out/cards/<id>.png         (carte de controle)
         out/cards/_planche.png     (planche contact de tout le lot)

Usage :
    python tools/finalize_s2.py                 # tous les joueurs pourvus d'un rendu
    python tools/finalize_s2.py --club Aix
    python tools/finalize_s2.py --force         # re-detoure meme si le cutout existe
"""
import argparse
import json
import os
import sys

from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cutout_mj import detourer  # noqa: E402
from utils import card_renderer  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MANIFEST = os.path.join(ROOT, "data", "roster_s2.json")
CUTOUTS = os.path.join(ROOT, "assets", "cutouts")
OUT_DIR = os.path.join(ROOT, "out", "cards")

# Memes reglages que tools/build_cutouts.py : contours de cheveux propres, pas de halo
MATTING = dict(alpha_matting=True,
               alpha_matting_foreground_threshold=240,
               alpha_matting_background_threshold=15,
               alpha_matting_erode_size=8)


def planche(cartes, chemin, cols=4, larg=300):
    """Planche contact du lot, pour juger l'homogeneite d'un coup d'oeil."""
    if not cartes:
        return
    haut = int(larg * card_renderer.H / card_renderer.W)
    lignes = (len(cartes) + cols - 1) // cols
    police = ImageFont.truetype(
        os.path.join(ROOT, "assets", "fonts", "Oswald-VariableFont_wght.ttf"), 17)
    p = Image.new("RGB", (cols * larg, lignes * (haut + 24)), (18, 20, 24))
    d = ImageDraw.Draw(p)
    for i, (nom, img) in enumerate(cartes):
        v = img.convert("RGB").resize((larg - 8, haut - 8), Image.LANCZOS)
        x, y = (i % cols) * larg + 4, (i // cols) * (haut + 24) + 4
        p.paste(v, (x, y))
        d.text((x, y + haut), nom[:30], font=police, fill=(225, 230, 235))
    p.save(chemin)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--club", default="")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--rembg", action="store_true",
                    help="repli sur rembg (avale les maillots sombres, a eviter)")
    args = ap.parse_args()

    decoupe = detourer
    if args.rembg:
        from rembg import new_session, remove
        session = new_session("u2net")
        # Memes reglages que tools/build_cutouts.py (portraits S1)
        matting = dict(alpha_matting=True, alpha_matting_foreground_threshold=240,
                       alpha_matting_background_threshold=15, alpha_matting_erode_size=8)

        def decoupe(src):
            return remove(Image.open(src).convert("RGBA"), session=session, **matting)

    players = [p for p in json.load(open(MANIFEST, encoding="utf-8"))
               if not p.get("sorti") and p.get("image_file")]
    if args.club:
        players = [p for p in players if p["club"].lower() == args.club.lower()]
    if not players:
        raise SystemExit("Aucun joueur avec un rendu choisi (champ image_file).")

    os.makedirs(CUTOUTS, exist_ok=True)
    os.makedirs(OUT_DIR, exist_ok=True)

    cartes, faits, echecs = [], 0, []
    for i, p in enumerate(sorted(players, key=lambda x: x["nom"]), 1):
        src = p["image_file"]
        if not os.path.isabs(src):
            src = os.path.join(ROOT, src)
        cut_path = os.path.join(CUTOUTS, f"{p['id']}.webp")
        try:
            if os.path.exists(cut_path) and not args.force:
                cut = Image.open(cut_path).convert("RGBA")
            else:
                cut = decoupe(src)
                cut.save(cut_path, "WEBP", quality=88, method=6)
            carte = card_renderer.compose_v2(cut, p["nom"], p["club"], p["rarete"],
                                             p.get("poste") or "", cadrage="buste")
            carte.save(os.path.join(OUT_DIR, f"{p['id']}.png"))
            cartes.append((p["nom"], carte))
            faits += 1
            print(f"[{i}/{len(players)}] OK  {p['nom']} ({p['rarete']})")
        except Exception as e:
            echecs.append((p["nom"], str(e)))
            print(f"[{i}/{len(players)}] ERR {p['nom']} -> {e}")

    # Une planche par club : sinon chaque nouveau club ecrase la precedente.
    nom_planche = f"_planche-{args.club.lower()}.png" if args.club else "_planche.png"
    planche(cartes, os.path.join(OUT_DIR, nom_planche))
    print(f"\n{faits} cartes -> {os.path.relpath(OUT_DIR, ROOT)}  (+ {nom_planche})")
    if echecs:
        print(f"{len(echecs)} echecs :")
        for nom, err in echecs:
            print(f"  - {nom}: {err}")


if __name__ == "__main__":
    main()
