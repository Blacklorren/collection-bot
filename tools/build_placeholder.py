"""Silhouette de remplacement pour les joueurs sans portrait. (OFFLINE, dev only.)

Trois joueurs de la saison 2 n'ont aucune photo de reference : la LNH les laisse en
silhouette, ou ne les liste pas du tout. Sans cutout, publier_s2.py les ecarte et
l'album de leur club reste incomplet dans /collection -- ce qui se voit.

La silhouette n'est pas dessinee a la main : c'est la MEDIANE des cutouts de la
collection. Chaque rendu Midjourney est cadre de la meme facon (meme prompt, meme
graine), on peut donc empiler leurs masques alpha et garder ce qui est du sujet chez
plus de la moitie d'entre eux. On obtient une tete, des oreilles, un cou et des
epaules aux proportions exactes de la collection -- ce qu'aucune forme geometrique ne
donnait : le cadrage "buste" met la tete a 60 % de la hauteur de carte, et a cette
taille un ovale se lit comme un oeuf, pas comme une tete.

Elle n'imite personne : par construction, elle ne ressemble a aucun joueur en
particulier.

Entree : data/roster_s2.json + assets/cutouts/*.webp
Sortie : assets/cutouts/<id>.webp   (pour les joueurs sans image_file)

Usage :
    python tools/build_placeholder.py                  # rapport seul
    python tools/build_placeholder.py --go
    python tools/build_placeholder.py --go --ids hosni-oussama brolin-pontus
"""
import argparse
import json
import os

import numpy as np
from PIL import Image, ImageFilter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MANIFEST = os.path.join(ROOT, "data", "roster_s2.json")
CUTOUTS = os.path.join(ROOT, "assets", "cutouts")

TEINTE = (96, 104, 118)    # ardoise moyenne, neutre : ni un trou noir, ni un aplat clair
LISSAGE = 2.5              # le detourage des vrais rendus laisse une frange douce
# L'alpha reste PLEIN. Le calage du renderer ne compte que les pixels franchement
# opaques (BUSTE_ALPHA_MIN = 128) : une silhouette translucide n'a pas de boite pour
# lui, et le cadrage part en erreur. La discretion vient donc de la teinte, pas de
# l'opacite.


def silhouette(joueurs):
    """Mediane des masques alpha des cutouts existants. -> PIL.Image RGBA."""
    somme, n, taille = None, 0, None
    for p in joueurs:
        chemin = os.path.join(CUTOUTS, f"{p['id']}.webp")
        if not p.get("image_file") or not os.path.exists(chemin):
            continue
        a = np.asarray(Image.open(chemin).convert("RGBA"))[..., 3]
        if taille is None:
            taille = a.shape
        elif a.shape != taille:
            continue          # un rendu d'un autre format ne s'empile pas
        somme = (a > 128).astype(np.float32) if somme is None else somme + (a > 128)
        n += 1
    if not n:
        raise SystemExit("Aucun cutout exploitable : lance d'abord tools/finalize_s2.py")

    masque = ((somme / n) > 0.5).astype(np.uint8) * 255
    alpha = Image.fromarray(masque, "L").filter(ImageFilter.GaussianBlur(LISSAGE))
    im = Image.new("RGBA", alpha.size, TEINTE + (0,))
    im.putalpha(alpha)
    return im, n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--go", action="store_true", help="ecrit les fichiers")
    ap.add_argument("--ids", nargs="*", help="ids a traiter (defaut : tous ceux sans rendu)")
    args = ap.parse_args()

    roster = [p for p in json.load(open(MANIFEST, encoding="utf-8")) if not p.get("sorti")]
    im, n = silhouette(roster)
    print(f"silhouette mediane calculee sur {n} cutouts")

    cibles = [p for p in roster if not p.get("image_file")]
    if args.ids:
        cibles = [p for p in cibles if p["id"] in args.ids]
    if not cibles:
        print("Aucun joueur sans rendu : rien a faire.")
        return

    for p in cibles:
        dest = os.path.join(CUTOUTS, f"{p['id']}.webp")
        print(f"  {p['nom']:<26} {p['club']:<12} -> {os.path.relpath(dest, ROOT)}")
        if args.go:
            im.save(dest, "WEBP", quality=90, method=6)
    print(f"\n{len(cibles)} silhouettes{'' if args.go else ' (simulation)'}.")


if __name__ == "__main__":
    main()
