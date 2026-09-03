"""Rendu Midjourney retenu -> cutout detoure -> carte finale. (OFFLINE, dev only.)

Dernier maillon de la chaine S2 : prend le fichier choisi dans data/roster_s2.json
(champ image_file), le detoure au rembg, l'ecrit dans assets/cutouts/<id>.webp puis
compose la carte avec compose_v2().

Le recadrage 4:5 n'est pas fait ici : _crop_to_ratio() s'en charge dans le renderer,
ce qui garantit que la carte de preview est exactement celle que le bot produira.

Le detourage est verifie apres coup : si une plaque de fond a survecu (Midjourney
eclaire parfois le fond en degrade, cf. cutout_mj), on relance avec une tolerance de
coeur plus large. C'est mesure, pas devine, et ca ne touche pas les rendus propres.

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

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from scipy import ndimage

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


# Au-dela de ce taux, le detourage a laisse une plaque de fond : on relance plus large.
# Mesure sur les 193 rendus : les trois vraies plaques sortent entre 8 et 40 %, les
# detourages propres sous 1,5 %. 3 % laisse donc une marge des deux cotes.
FOND_TOLERE = 3.0
TOLERANCES = (20, 30, 45, 70)

# Ce qui fait une plaque de fond survivante, et ce qui n'en fait pas une :
#   - a la couleur du fond               (COULEUR)
#   - PLATE : le prompt impose "flat plain light grey background, completely empty",
#     donc le vrai fond n'a aucune texture. Un maillot blanc, une chevelure platine ou
#     un decolore en ont : c'est ce qui les sauve, alors qu'un simple seuil de couleur
#     les comptait comme du fond.                                          (ECART_TYPE)
#   - d'un seul tenant et large                                            (TAILLE_MIN)
#   - autour de la tete : plus bas, les epaules et le maillot occupent le cadre. (HAUT)
ECART_TYPE = 3.0     # ecart-type local maximal, en niveaux de gris, sur 9x9
TAILLE_MIN = 0.004   # part de la zone analysee sous laquelle une tache est du bruit
HAUT = 0.5           # moitie haute de l'image


def _fond_residuel(cut, src):
    """% de la moitie haute occupee par une plaque de fond encore opaque.

    La premiere version comptait TOUS les pixels opaques proches de la couleur du fond,
    ou qu'ils soient. Elle prenait donc un maillot blanc (Toulouse), une chevelure
    platine ou une ombre grise pour du fond survivant : Bono sortait a 6,7 % avec un
    detourage propre, la relance montait tol_core a 70 et lui mangeait les cheveux --
    le remede faisait le mal. On exige desormais une zone PLATE, large et haute.
    """
    a = np.asarray(Image.open(src).convert("RGB"), dtype=np.float32)
    b = 5
    cadre = np.concatenate([a[:b].reshape(-1, 3), a[:, :b].reshape(-1, 3),
                            a[:, -b:].reshape(-1, 3)])
    fond = np.median(cadre, axis=0)

    arr = np.asarray(cut, dtype=np.float32)[:int(cut.height * HAUT)]
    d = np.linalg.norm(arr[..., :3] - fond, axis=2)
    gris = arr[..., :3].mean(axis=2)
    moy = ndimage.uniform_filter(gris, 9)
    var = ndimage.uniform_filter(gris * gris, 9) - moy * moy
    plat = np.sqrt(np.clip(var, 0, None)) < ECART_TYPE

    plaque = (arr[..., 3] > 200) & (d < 45) & plat
    lab, n = ndimage.label(plaque)
    if not n:
        return 0.0
    tailles = ndimage.sum(plaque, lab, np.arange(1, n + 1))
    gros = np.nonzero(tailles >= TAILLE_MIN * plaque.size)[0] + 1
    return float(np.isin(lab, gros).mean() * 100)


# Rattrapage semantique. Le detourage par couleur est exact tant que le sujet et le
# fond ont des couleurs differentes -- ce qui n'est pas toujours vrai : sur le rendu de
# Tom Vinatier, Midjourney a peint les cheveux courts du cote de la tete dans EXACTEMENT
# le gris du fond (171/172/176 contre 170/171/175). Ils se relient au fond par le haut
# de la coiffure, et aucune regle de couleur ni de morphologie ne peut les distinguer :
# la fermeture morphologique n'en recupere que 1 800 px sur 14 000, la barriere d'encre
# rien du tout. rembg, lui, sait que c'est une tete.
#
# On ne l'utilise donc PAS pour detourer -- il avale les maillots sombres, c'est tout le
# sujet de cutout_mj -- mais seulement pour rendre au sujet ce que le detourage lui a
# pris. Union, jamais soustraction. Et seulement par plaques d'un seul tenant : le bruit
# de bord de rembg (0,1 a 0,3 % de l'image, disperse) ne passe pas le filtre de taille,
# la vraie balafre de Vinatier si. Mesure sur sept rendus : 0,54 % rattrape chez lui,
# 0,00 % partout ailleurs.
RATTRAPAGE_MIN = 0.002   # part de l'image sous laquelle une plaque rendue est du bruit
EROSION_SEM = 4          # marge de securite sur le masque semantique, en pixels
_SESSION = []


def _rattraper_sujet(cut, src):
    """Rend au sujet les plaques que le detourage a prises et que rembg reconnait."""
    try:
        from rembg import new_session, remove
    except ImportError:
        return cut
    if not _SESSION:
        _SESSION.append(new_session("u2net"))
    sem = np.asarray(remove(Image.open(src).convert("RGBA"), session=_SESSION[0],
                            **MATTING))[..., 3] > 200
    sem = ndimage.binary_erosion(sem, iterations=EROSION_SEM)

    arr = np.array(cut)
    manque = sem & (arr[..., 3] < 128)
    lab, n = ndimage.label(manque)
    if not n:
        return cut
    tailles = ndimage.sum(manque, lab, np.arange(1, n + 1))
    gros = np.nonzero(tailles >= RATTRAPAGE_MIN * manque.size)[0] + 1
    if not gros.size:
        return cut
    plaques = np.isin(lab, gros)
    arr[..., 3][plaques] = 255
    print(f"      rattrapage semantique : {plaques.mean()*100:.2f} % du sujet rendus")
    return Image.fromarray(arr, "RGBA")


# Une relance ne se justifie que si elle fait vraiment reculer la plaque. En dessous,
# c'est du bruit de mesure, et monter la tolerance ne fait plus que ronger le sujet.
GAIN_MINIMAL = 1.0


def decouper_propre(src):
    """Detoure, puis relance plus large tant que ca fait reculer le fond.

    On garde le MEILLEUR essai, pas le dernier. Luc Steins l'impose : son visage est
    rendu dans un gris-beige plat, tres proche du fond, donc la mesure le prend pour une
    plaque et ne redescend jamais sous le seuil. Sans cette garde, la boucle finissait a
    tol_core=70 et lui mangeait la barbe pour rien -- alors que le detourage d'origine
    etait bon. Une vraie plaque, elle, recule nettement des la premiere relance.
    """
    essais = [(_fond_residuel(cut := detourer(src), src), 0, cut)]
    for tol in TOLERANCES:
        if essais[-1][0] <= FOND_TOLERE:
            break
        cut = detourer(src, tol_core=tol)
        reste = _fond_residuel(cut, src)
        essais.append((reste, tol, cut))
        print(f"      fond residuel -> nouvel essai a tol_core={tol} ({reste:.1f} %)")

    meilleur = essais[0]
    for essai in essais[1:]:
        if essai[0] < meilleur[0] - GAIN_MINIMAL:
            meilleur = essai
    if meilleur is not essais[-1]:
        print(f"      relance sans effet : on garde tol_core={meilleur[1] or 'defaut'} "
              f"({meilleur[0]:.1f} %)")
    return _rattraper_sujet(meilleur[2], src)


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

    decoupe = decouper_propre
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
