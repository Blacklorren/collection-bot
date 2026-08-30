"""Deduit la couleur du maillot d'un joueur depuis son portrait de reference.
(OFFLINE, dev only.)

Midjourney ne sait pas interpreter "la meme couleur que l'image de reference" : il
faut lui NOMMER la couleur. Ce module lit refs/<id>.png, isole le torse et rend une
formule courte ("black and gold", "royal blue") a injecter dans le prompt.

Trois pieges evites :
  - le fond blanc du studio : detoure par remplissage depuis les bords, sinon il
    ecrase tout. Un remplissage connexe et non un simple seuil, sinon un maillot
    blanc (Cesson, Chartres) serait efface avec le fond.
  - la peau : le visage et les bras sortiraient en tete des couleurs dominantes.
  - les sponsors : nombreux mais petits, ils sont noyes par le regroupement.

Usage direct pour controle :
    python tools/jersey_colors.py            # toutes les refs
    python tools/jersey_colors.py banke-gustaf
"""
import os
import sys

from PIL import Image, ImageDraw

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REFS_DIR = os.path.join(ROOT, "refs")

SENTINELLE = (255, 0, 255)   # marqueur de fond, absent des vraies photos

# Ancres de couleurs, choisies pour couvrir les maillots de Starligue. Le libelle
# est ce qui part dans le prompt, donc il doit rester un terme que Midjourney
# comprend sans ambiguite.
ANCRES = [
    ("white", (245, 245, 245)),
    ("light grey", (190, 190, 190)),
    ("grey", (128, 128, 128)),
    ("black", (28, 28, 28)),
    ("navy blue", (25, 35, 80)),
    ("royal blue", (30, 75, 190)),
    ("sky blue", (110, 175, 230)),
    ("teal", (0, 128, 128)),
    ("dark green", (20, 85, 45)),
    ("green", (45, 160, 70)),
    ("lime green", (150, 200, 60)),
    ("yellow", (240, 220, 60)),
    ("gold", (200, 160, 45)),
    ("orange", (235, 130, 35)),
    ("red", (205, 40, 40)),
    ("crimson", (150, 25, 45)),
    ("maroon", (105, 30, 40)),
    ("pink", (230, 130, 170)),
    ("purple", (110, 55, 155)),
    ("violet", (150, 100, 205)),
    ("brown", (110, 75, 45)),
    ("beige", (215, 195, 165)),
]


# Deux teintes de la meme famille dans un prompt ("purple and violet", "grey and
# light grey") sont redondantes et peuvent pousser Midjourney vers un bicolore qui
# n'existe pas. On ne garde alors que la dominante. Les familles distinctes, elles,
# sont conservees : "royal blue and crimson" decrit vraiment le maillot du PSG.
FAMILLES = {
    "navy blue": "bleu", "royal blue": "bleu", "sky blue": "bleu", "teal": "bleu",
    "purple": "violet", "violet": "violet", "pink": "violet",
    "yellow": "jaune", "gold": "jaune",
    "white": "neutre", "light grey": "neutre", "grey": "neutre",
    "red": "rouge", "crimson": "rouge", "maroon": "rouge",
    "green": "vert", "dark green": "vert", "lime green": "vert",
}


def nommer(rgb):
    r, g, b = rgb
    return min(ANCRES, key=lambda a: (r - a[1][0]) ** 2 + (g - a[1][1]) ** 2 + (b - a[1][2]) ** 2)[0]


def est_peau(r, g, b):
    """Filtre large : la peau reste rouge > vert > bleu avec un ecart contenu."""
    return r > 90 and r > g > b and 10 < r - b < 130 and abs(g - b) < 70


def masque_fond(im, tol=34):
    """Remplit le fond depuis les 4 coins et rend l'image marquee a la SENTINELLE."""
    im = im.copy()
    w, h = im.size
    for xy in ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)):
        try:
            ImageDraw.floodfill(im, xy, SENTINELLE, thresh=tol)
        except ValueError:
            pass
    return im


def couleurs_maillot(chemin, n=2):
    """-> "black and gold", "royal blue", ou None si indecidable."""
    im = Image.open(chemin).convert("RGBA")
    fond = Image.new("RGBA", im.size, (255, 255, 255, 255))
    im = Image.alpha_composite(fond, im).convert("RGB")   # aplatit la transparence
    im = masque_fond(im)

    w, h = im.size
    # Le torse : bande centrale, moitie basse. On evite le visage (haut) et les
    # bords ou trainent encore des restes de fond.
    boite = im.crop((int(w * 0.22), int(h * 0.52), int(w * 0.78), int(h * 0.95)))
    boite = boite.resize((min(boite.width, 120), min(boite.height, 120)), Image.BILINEAR)

    # On nomme CHAQUE pixel avant de compter, au lieu de regrouper par teinte puis
    # de nommer. Sur un maillot a motif charge (Limoges), les dizaines de nuances
    # de bleu se dispersaient en petits paquets dont aucun n'etait majoritaire ;
    # nommees d'abord, elles s'additionnent toutes sur "royal blue".
    compte = {}
    data = boite.tobytes()
    total = 0
    for i in range(0, len(data) - 2, 3):
        r, g, b = data[i], data[i + 1], data[i + 2]
        if (r, g, b) == SENTINELLE or est_peau(r, g, b):
            continue
        compte[nommer((r, g, b))] = compte.get(nommer((r, g, b)), 0) + 1
        total += 1

    if total < 200:            # torse trop petit ou trop masque : on n'invente pas
        return None

    tries = sorted(compte.items(), key=lambda kv: -kv[1])
    noms = [tries[0][0]]
    familles = {FAMILLES.get(tries[0][0], tries[0][0])}
    for nom, n_px in tries[1:]:
        if len(noms) >= n or n_px / total < 0.15:  # sous 15 %, sponsor ou ombre
            break
        fam = FAMILLES.get(nom, nom)
        if fam in familles:                        # meme famille : redondant
            continue
        noms.append(nom)
        familles.add(fam)
    return noms[0] if len(noms) == 1 else f"{noms[0]} and {noms[1]}"


def main():
    cibles = sys.argv[1:]
    fichiers = ([os.path.join(REFS_DIR, f"{c}.png") for c in cibles] if cibles
                else sorted(os.path.join(REFS_DIR, f) for f in os.listdir(REFS_DIR)
                            if f.endswith(".png")))
    for f in fichiers:
        if not os.path.exists(f):
            print(f"  {os.path.basename(f):40s} ABSENT")
            continue
        print(f"  {os.path.basename(f)[:-4]:40s} {couleurs_maillot(f) or '(indecidable)'}")


if __name__ == "__main__":
    main()
