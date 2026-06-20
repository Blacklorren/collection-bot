"""Implementation de REFERENCE du layout v2 valide (à fusionner dans utils/card_renderer.py).

Layout : bordure noire epaisse à coins biseautes (bas-gauche / haut-droite, bordure
d'epaisseur UNIFORME sur la diagonale), fond couleur de rarete (degrade) + joueur detoure,
ecusson du club sur disque blanc en bas-gauche, nom (grand) + poste (dessous) en bas-droite.

Usage rapide (apercu) :
    python tools/prototype_layout_v2.py <cutout.png> "NOM JOUEUR" <Club> <Rarete> "<Poste>"
-> ecrit ./layout_v2_preview.png

`compose_v2(cutout, nom, club, rarete, poste)` est la fonction à reprendre dans card_renderer.
`cutout` est une image PIL RGBA AVEC FOND TRANSPARENT (cf. tools/build_cutouts.py).
"""
import math
import os
import sys

from PIL import Image, ImageDraw

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from utils import card_renderer as cr  # reutilise W,H,RARITY_RGB,fonts,slugify,_vgradient,lighten,darken,LOGOS

W, H = cr.W, cr.H              # 992 x 1240
CHAMFER = 140                 # taille du coin coupe (bas-gauche + haut-droite)
BORDER = 42                   # epaisseur de la bordure noire


def _outer_poly():
    c = CHAMFER
    return [(0, 0), (W - c, 0), (W, c), (W, H), (c, H), (0, H - c)]


def _inner_poly(b):
    """Contour interieur = offset perpendiculaire de `b` sur chaque arete
    -> bordure d'epaisseur uniforme, diagonales comprises."""
    c = CHAMFER
    k = b * math.sqrt(2)
    c_tr = (W - c) - k         # arete haut-droite : x - y = c_tr
    c_bl = (c - H) + k         # arete bas-gauche : x - y = c_bl
    return [
        (b, b),
        (b + c_tr, b),
        (W - b, (W - b) - c_tr),
        (W - b, H - b),
        ((H - b) + c_bl, H - b),
        (b, b - c_bl),
    ]


def _poly_mask(size, points, ss=3):
    big = Image.new("L", (size[0] * ss, size[1] * ss), 0)
    ImageDraw.Draw(big).polygon([(x * ss, y * ss) for x, y in points], fill=255)
    return big.resize(size, Image.Resampling.LANCZOS)


def _bottom_shade(size, start=0.5, max_a=210):
    w, h = size
    g = Image.new("L", (1, h), 0)
    s = int(h * start)
    for y in range(h):
        g.putpixel((0, y), 0 if y < s else int(max_a * ((y - s) / (h - s)) ** 1.4))
    layer = Image.new("RGBA", (w, h), (8, 9, 12, 0))
    layer.putalpha(g.resize((w, h)))
    return layer


def compose_v2(cutout, nom, club, rarete, poste=""):
    rgb = cr.RARITY_RGB.get(rarete, (150, 150, 150))
    out = _outer_poly()
    inn = _inner_poly(BORDER)

    # Carte noire (silhouette a coins coupes)
    card = Image.new("RGBA", (W, H), (12, 13, 16, 255))
    card.putalpha(_poly_mask((W, H), out))

    # Fond rarete + joueur detoure + ombre basse, clippe au polygone interieur
    bg = cr._vgradient((W, H), cr.darken(rgb, 0.15), cr.darken(rgb, 0.6))
    art = cutout.convert("RGBA").resize((W, H), Image.Resampling.LANCZOS)
    bg.alpha_composite(art)
    bg.alpha_composite(_bottom_shade((W, H)))
    bg.putalpha(_poly_mask((W, H), inn))
    card.alpha_composite(bg)

    cd = ImageDraw.Draw(card)
    cd.line(inn + [inn[0]], fill=rgb, width=3, joint="curve")

    # Ecusson sur disque blanc, bas-gauche
    disc_c = (BORDER + 118, H - BORDER - 130)
    r = 92
    disc = Image.new("RGBA", (r * 2 + 8, r * 2 + 8), (0, 0, 0, 0))
    ImageDraw.Draw(disc).ellipse([4, 4, r * 2 + 3, r * 2 + 3], fill=(248, 249, 250, 255),
                                 outline=(12, 13, 16, 255), width=4)
    card.alpha_composite(disc, (disc_c[0] - r - 4, disc_c[1] - r - 4))
    logo_path = os.path.join(cr.LOGOS, cr.slugify(club) + ".png")
    if os.path.exists(logo_path):
        ls = 150
        logo = Image.open(logo_path).convert("RGBA").resize((ls, ls), Image.Resampling.LANCZOS)
        card.alpha_composite(logo, (disc_c[0] - ls // 2, disc_c[1] - ls // 2))

    # Nom (grand) + poste (dessous), alignes a droite, bas-droite
    x_r = W - BORDER - 36
    name = nom.upper()
    size = 80
    nf = cr.anton(size)
    max_w = x_r - (disc_c[0] + r + 28)
    while cd.textlength(name, font=nf) > max_w and size > 40:
        size -= 3
        nf = cr.anton(size)
    pf = cr.oswald(38, 500)
    nb = cd.textbbox((0, 0), name, font=nf, anchor="ra")
    pb = cd.textbbox((0, 0), (poste or "").upper(), font=pf, anchor="ra")
    nh, ph = nb[3] - nb[1], pb[3] - pb[1]
    gap = 8
    bottom_y = H - BORDER - 46
    if poste:
        poste_top = bottom_y - ph
        name_top = poste_top - gap - nh
        cd.text((x_r, name_top), name, font=nf, fill=(255, 255, 255), anchor="ra")
        cd.text((x_r, poste_top), poste.upper(), font=pf, fill=cr.lighten(rgb, 0.55), anchor="ra")
    else:
        cd.text((x_r, bottom_y - nh), name, font=nf, fill=(255, 255, 255), anchor="ra")
    return card


if __name__ == "__main__":
    if len(sys.argv) < 5:
        print("usage: python tools/prototype_layout_v2.py <cutout.png> <nom> <club> <rarete> [poste]")
        raise SystemExit(1)
    cutout = Image.open(sys.argv[1])
    poste = sys.argv[5] if len(sys.argv) > 5 else ""
    img = compose_v2(cutout, sys.argv[2], sys.argv[3], sys.argv[4], poste)
    img.convert("RGB").save("layout_v2_preview.png")
    print("ecrit layout_v2_preview.png")
