"""Detourage des rendus Midjourney. (OFFLINE, dev only.)

rembg est un modele semantique : il devine ou est "le sujet". Sur nos rendus il se
trompe deux fois — il avale les maillots sombres (Guiraudou perdait epaules et torse,
pris pour du fond) et il laisse des plaques grises dans les chevelures.

Or notre fond n'a rien d'inconnu : le prompt impose "flat plain light grey background,
completely empty". C'est donc un probleme de fond uni, pas de segmentation semantique,
et il se resout exactement :

  1. couleur de fond = mediane du cadre exterieur de l'image
  2. masque du fond STRICT (tres proche de cette couleur) -> composantes connexes
  3. seules les composantes qui touchent le bord sont du fond. Un maillot gris clair
     n'est jamais parfaitement uni : il ne forme pas une composante reliee au bord,
     donc il survit — la ou un seuil global l'aurait efface.
  4. les petites poches de gris ENFERMEES dans le sujet (les residus dans les cheveux)
     sont supprimees, mais seulement en dessous d'une taille : un vetement gris, lui,
     est trop grand pour tomber dans ce filet.
  5. alpha progressif sur la frange, calcule sur la distance a la couleur de fond :
     c'est ce qui rend les cheveux propres sans halo.
  6. on ne garde que la plus grande composante opaque : ca elimine les eclats de
     pixels sur les bords, qui gonflaient la bbox et faussaient le cadrage.

Pre-requis : pip install numpy scipy pillow  (pas de rembg, pas d'onnxruntime)

Usage direct pour controle :
    python tools/cutout_mj.py <image> [sortie.png]
"""
import os
import sys

import numpy as np
from PIL import Image
from scipy import ndimage

# Distances RGB a la couleur de fond (0 = pile la couleur du fond).
TOL_CORE = 26      # en deca : fond franc, sert a la connexite
TOL_EDGE = 105     # au dela : sujet franc. Entre les deux : alpha progressif
BANDE = 9          # largeur de la frange adoucie autour du fond, en pixels
TROU_MAX = 0.004   # poche de gris enfermee supprimee sous 0,4 % de l'image
# ...et seulement si elle est A MOINS DE CE NOMBRE DE PIXELS du vrai fond. Sans cette
# condition la regle percait les visages : le fond est un gris moyen, et les ombres
# froides du prompt ("cool desaturated slate-blue shadows") tombent dans la meme
# teinte. Une poche prise dans une chevelure touche le fond ; une ombre sur une joue
# en est loin (mediane mesuree : 62 px).
POCHE_PROCHE = 12


def _couleur_fond(arr):
    """Mediane du cadre exterieur : robuste meme si un cheveu touche le bord."""
    b = 5
    cadre = np.concatenate([
        arr[:b].reshape(-1, 3), arr[-b:].reshape(-1, 3),
        arr[:, :b].reshape(-1, 3), arr[:, -b:].reshape(-1, 3),
    ])
    return np.median(cadre, axis=0)


def detourer(source):
    """-> PIL.Image RGBA detouree."""
    im = Image.open(source).convert("RGB") if isinstance(source, (str, os.PathLike)) \
        else source.convert("RGB")
    arr = np.asarray(im, dtype=np.float32)
    h, w = arr.shape[:2]

    fond = _couleur_fond(arr)
    dist = np.linalg.norm(arr - fond, axis=2)

    # 1) fond franc + connexite : seules les plaques reliees au bord sont du fond
    coeur = dist < TOL_CORE
    lab, n = ndimage.label(coeur)
    if n:
        bords = np.unique(np.concatenate([lab[0], lab[-1], lab[:, 0], lab[:, -1]]))
        bords = bords[bords > 0]
        fond_mask = np.isin(lab, bords)

        # 2) poches de gris enfermees PRES DU BORD du sujet : residus dans les cheveux.
        #    Le filtre de distance est ce qui evite de trouer les visages.
        tailles = ndimage.sum(coeur, lab, np.arange(1, n + 1))
        petites = np.nonzero(tailles < TROU_MAX * h * w)[0] + 1
        petites = np.setdiff1d(petites, bords)
        if petites.size:
            depuis_fond = ndimage.distance_transform_edt(~fond_mask)
            d = np.atleast_1d(ndimage.minimum(depuis_fond, lab, petites))
            petites = petites[d <= POCHE_PROCHE]
        if petites.size:
            fond_mask |= np.isin(lab, petites)
    else:
        fond_mask = np.zeros((h, w), bool)

    # 3) alpha progressif, applique uniquement dans une bande autour du fond :
    #    ailleurs le sujet reste plein, meme s'il est grisatre
    alpha = np.ones((h, w), np.float32)
    bande = ndimage.binary_dilation(fond_mask, iterations=BANDE) & ~fond_mask
    rampe = np.clip((dist - TOL_CORE) / max(TOL_EDGE - TOL_CORE, 1), 0.0, 1.0)
    alpha[bande] = rampe[bande]
    alpha[fond_mask] = 0.0

    # 4) une seule silhouette : on jette les eclats de pixels des bords
    plein = alpha > 0.5
    lab2, n2 = ndimage.label(plein)
    if n2 > 1:
        tailles = ndimage.sum(plein, lab2, np.arange(1, n2 + 1))
        garde = np.argmax(tailles) + 1
        alpha[(lab2 != garde) & (lab2 != 0)] = 0.0

    out = np.dstack([np.asarray(im, dtype=np.uint8),
                     (alpha * 255).astype(np.uint8)])
    return Image.fromarray(out, "RGBA")


def main():
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    src = sys.argv[1]
    dst = sys.argv[2] if len(sys.argv) > 2 else os.path.splitext(src)[0] + "_cut.png"
    img = detourer(src)
    img.save(dst)
    bb = img.getchannel("A").getbbox()
    print(f"{dst}  bbox={bb}  taille={img.size}")


if __name__ == "__main__":
    main()
