"""Rendu de carte style TCG (layout v2) : silhouette noire a coins biseautes, fond radial
couleur de rarete + joueur detoure pose dessus (fondu en pied), ecusson du club sur disque
blanc (bas-gauche, omis si le club n'a pas de logo), nom (Anton) + poste (Oswald) alignes
a droite (bas-droite).

Expose :
- compose_v2(cutout, nom, club, rarete, poste) -> PIL.Image  (rendu pur, synchrone, layout actif)
- compose(portrait, nom, club, rarete) -> PIL.Image          (ancien layout v1, conserve)
- get_card_bytes(card, session=None) -> bytes | None          (cutout/portrait + rend + cache disque)
"""
import asyncio
import io
import math
import os
import unicodedata

from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(__file__)
ROOT = os.path.dirname(HERE)
FONTS = os.path.join(ROOT, "assets", "fonts")
LOGOS = os.path.join(ROOT, "assets", "logos")
CUTOUTS = os.path.join(ROOT, "assets", "cutouts")

# Cache sur le volume persistant si dispo : sinon il est vide a chaque deploiement
# et on repaie ~350 ms de rendu par carte.
_DATA_DIR = "/data"
CACHE_DIR = (os.path.join(_DATA_DIR, "card_cache")
             if os.path.isdir(_DATA_DIR)
             else os.path.join(ROOT, "assets", "card_cache"))

# Incrementer pour invalider le cache disque quand le design change
DESIGN_VERSION = "v7"

W, H = 992, 1240
R = 40          # rayon des coins (carte, layout v1)
BLACK = 30      # bordure noire externe (v1)
FRAME = 18      # epaisseur du cadre stylise (v1)

CHAMFER = 140   # taille du coin biseaute (bas-gauche + haut-droite, v2)
BORDER = 42     # epaisseur de la bordure noire (v2)

# Reglages design v2 (bandeau bas + cadrage joueur)
PLAYER_ZOOM = 0.78        # echelle du joueur detoure (dezoom : on retrouve les epaules)
PLAYER_TOP_MARGIN = 54    # air entre le liseré superieur et le sommet du crane
BAND_TOP = 1028           # y du haut du bandeau noir / du separateur de couleur

# --- Profil de cadrage "buste" : portraits S2 generes par Midjourney ---------------
# Les rendus MJ sont deja cadres serre sur le buste et remplissent leur image. Leur
# appliquer le dezoom S1 (PLAYER_ZOOM, une fraction fixe de la carte) les reduit a
# 773 px de large sur une carte qui en fait 992 : d'ou le vide de part et d'autre des
# epaules. Ici on met la BBOX DU SUJET a l'echelle de la carte, ce qui s'adapte au
# cadrage reel de chaque image au lieu de le supposer.
# Ce profil est opt-in (compose_v2(..., cadrage="buste")) : les cartes S1, dont les
# portraits sources sont cadres tout autrement, gardent exactement le rendu actuel.
BUSTE_RECOUVREMENT = 30   # de combien le bas du buste doit passer sous le separateur.
# Le bandeau est opaque : ce qui compte est qu'il n'y ait pas de jour, pas que le
# buste descende loin. A 60 px, deux rendus au buste court (Mohamed, Garciandia)
# etaient agrandis pour rien, ce qui rognait leur chevelure de plus de 100 px.
# Taille de tete visee, en fraction de la hauteur de carte. C'est LE reglage
# d'homogeneite de la collection.
#
# Ce qu'on veut egaliser, c'est le VISAGE, et il n'est pas directement mesurable :
# la silhouette ne dit pas ou s'arrete la chevelure. Deux reperes s'en approchent,
# et ils se trompent dans des sens opposes :
#   - crane -> menton : exact, mais gonfle par les cheveux (un afro, une coiffure
#     bouffante, et le visage se retrouve reduit d'autant) ;
#   - largeur du cou : insensible a la coiffure, mais suit la carrure.
# On prend leur moyenne geometrique, ce qui divise par deux l'erreur de chacun.
# BUSTE_COU_RATIO ramene la largeur de cou a l'echelle des hauteurs de tete : c'est
# le rapport des medianes mesure sur les 98 rendus S2, pas une constante anatomique.
BUSTE_TETE_H = 0.605
BUSTE_COU_RATIO = 1.81
BUSTE_MENTON_DEFAUT = 0.62  # repli quand le cou n'est pas detectable (cheveux longs)
# Le menton est cale a une hauteur FIXE et c'est le sommet du crane qui flotte : une
# chevelure haute monte dans le cadre au lieu de rapetisser le visage. Quand elle
# depasse, on la ROGNE contre le liseré plutot que de reduire le visage — un crane
# coupe par le bord est un cadrage de portrait ordinaire, un visage plus petit que
# celui d'a cote se voit tout de suite. Au-dela de BUSTE_ROGNAGE_MAX on reduit quand
# meme : deux joueurs sur 98 (Tritta, de 0,5 %, et Mohamed et son afro, de 4 %).
BUSTE_MENTON_Y = 830
BUSTE_ROGNAGE_MAX = 55
# Encadrement de la hauteur crane -> menton, en fraction de la hauteur de carte.
# BUSTE_TETE_H vise le VISAGE via une estimation (moyenne geometrique hauteur/cou) ;
# sur un joueur au cou epais cette estimation surevalue le visage, la tete est donc
# dessinee plus petite et, le menton etant fixe, elle "tombe" dans le cadre : Ben Salem
# et Melo se retrouvaient avec 145 px de vide au-dessus du crane et le cou mange par le
# bandeau. On borne donc ce que l'estimation peut produire. Mesure sur les 255 rendus :
# mediane 740 px, d'ou une fourchette de +-6 % autour d'elle.
BUSTE_TETE_MIN = 0.585
BUSTE_TETE_MAX = 0.645
# Seuil d'opacite au-dela duquel un pixel compte comme du sujet pour le CALAGE.
# getbbox() prend tout pixel non nul : la frange semi-transparente laissee par la rampe
# alpha du detourage suffisait alors a etirer la boite jusqu'au bord de l'image, et le
# centrage horizontal partait avec (10 joueurs decales de plus de 40 px, jusqu'a 100).
BUSTE_ALPHA_MIN = 128
_BUSTE_PROFIL_H = 256     # hauteur de travail de l'analyse de silhouette
_BUSTE_LISSAGE = 5        # moyenne glissante sur le profil reduit, en lignes
_BUSTE_CREUX = 0.88       # le cou doit etre 12 % plus etroit que la tete pour etre cru
LOGO_SIZE = 180           # taille de l'ecusson dans le bandeau
DISC_R = 108              # rayon du disque blanc sous l'ecusson (suit la taille du logo)

# Bloc texte du bandeau (nom + poste, alignes a droite)
NAME_SIZE = 80            # corps max du nom, auto-reduit si trop long ou trop haut
NAME_MIN_SIZE = 40        # plancher de l'auto-reduction
POSTE_SIZE = 62           # corps du poste, sous le nom
# Le bloc texte est cale sur les LIGNES DE BASE (cf compose_v2), pas sur les boites
# d'encre : c'est ce qui garantit que tous les noms s'alignent, accentues ou non.
POSTE_BASELINE_FROM_BOTTOM = 44   # ligne de base du poste, depuis le bas de la carte
NAME_POSTE_GAP = 19       # base du nom -> haut des capitales du poste
TEXT_TOP_CLEARANCE = 6    # garde entre le haut de l'encre du nom et le separateur

RARITY_RGB = {
    "Commun": (150, 154, 162),
    "Peu Commun": (70, 185, 105),
    "Rare": (55, 130, 235),
    "Épique": (170, 90, 235),
    "Légendaire": (240, 188, 55),
    "Noël": (215, 50, 70),
}

# Polices systeme de secours si les .ttf bundles sont absents
_FALLBACK_FONTS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
]


def slugify(name):
    n = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return n.lower().replace(" ", "-")


def _font(filename, size, variation=None):
    path = os.path.join(FONTS, filename)
    try:
        f = ImageFont.truetype(path, size)
        if variation is not None:
            try:
                f.set_variation_by_axes([variation])
            except Exception:
                pass
        return f
    except (IOError, OSError):
        for fb in _FALLBACK_FONTS:
            try:
                return ImageFont.truetype(fb, size)
            except (IOError, OSError):
                continue
    return ImageFont.load_default()


def anton(size):
    return _font("Anton-Regular.ttf", size)


def oswald(size, weight=400):
    return _font("Oswald-VariableFont_wght.ttf", size, variation=weight)


def lighten(rgb, f):
    return tuple(int(c + (255 - c) * f) for c in rgb)


def darken(rgb, f):
    return tuple(int(c * (1 - f)) for c in rgb)


def _rrect_mask(size, box, radius):
    m = Image.new("L", size, 0)
    ImageDraw.Draw(m).rounded_rectangle(box, radius=radius, fill=255)
    return m


def _vgradient(size, top_rgb, bot_rgb):
    w, h = size
    col = Image.new("RGB", (1, h))
    for y in range(h):
        t = y / max(h - 1, 1)
        col.putpixel((0, y), tuple(int(top_rgb[i] + (bot_rgb[i] - top_rgb[i]) * t) for i in range(3)))
    return col.resize((w, h)).convert("RGBA")


def compose(portrait, nom, club, rarete):
    """Compose la carte finale (PIL RGBA) a partir d'un portrait PIL."""
    rgb = RARITY_RGB.get(rarete, (150, 150, 150))

    frame_box = [BLACK, BLACK, W - BLACK - 1, H - BLACK - 1]
    frame_r = R - BLACK
    art_in = BLACK + FRAME
    art_box = [art_in, art_in, W - art_in - 1, H - art_in - 1]
    art_r = max(frame_r - FRAME, 6)

    # 1) Carte noire (bordure externe)
    card = Image.new("RGBA", (W, H), (12, 13, 16, 255))
    card.putalpha(_rrect_mask((W, H), [0, 0, W - 1, H - 1], R))

    # 2) Cadre stylise fin : degrade metallique de la couleur de rarete
    grad = _vgradient((W, H), lighten(rgb, 0.35), darken(rgb, 0.35))
    grad.putalpha(_rrect_mask((W, H), frame_box, frame_r))
    card.alpha_composite(grad)
    cd = ImageDraw.Draw(card)
    cd.rounded_rectangle(frame_box, radius=frame_r, outline=lighten(rgb, 0.6) + (180,), width=2)

    # 3) Art du joueur, insere et masque au rectangle interieur
    art = portrait.convert("RGBA").resize((W, H), Image.Resampling.LANCZOS)
    art.putalpha(_rrect_mask((W, H), art_box, art_r))
    card.alpha_composite(art)
    cd.rounded_rectangle(art_box, radius=art_r, outline=(10, 10, 12, 255), width=3)

    # --- Ecusson du club (haut gauche) ---
    logo_path = os.path.join(LOGOS, slugify(club) + ".png")
    if os.path.exists(logo_path):
        logo = Image.open(logo_path).convert("RGBA").resize((118, 118), Image.Resampling.LANCZOS)
        bx, by = art_in + 16, art_in + 16
        badge = Image.new("RGBA", (150, 150), (0, 0, 0, 0))
        ImageDraw.Draw(badge).ellipse([0, 0, 149, 149], fill=(12, 13, 16, 220), outline=rgb + (255,), width=3)
        card.alpha_composite(badge, (bx, by))
        card.alpha_composite(logo, (bx + 16, by + 16))

    # --- Plaque de nom NOIRE (bas) : nom + club a gauche, rarete a droite ---
    plate_h = 152
    py0 = H - art_in - plate_h
    black_plate = Image.new("RGBA", (W, plate_h), (14, 15, 18, 250))
    black_plate.putalpha(_rrect_mask((W, plate_h), [art_in, 0, W - art_in - 1, plate_h - 1], 16))
    card.alpha_composite(black_plate, (0, py0))
    cd.rounded_rectangle([art_in, py0, W - art_in - 1, py0 + plate_h - 1], radius=16,
                         outline=rgb + (255,), width=3)

    # Accent vertical couleur rarete (cote gauche)
    cd.rounded_rectangle([art_in + 24, py0 + 28, art_in + 32, py0 + plate_h - 28], radius=4, fill=rgb)

    # Pastille de rarete integree a droite
    rl = rarete.upper()
    rf = oswald(32, 600)
    rtw = cd.textlength(rl, font=rf)
    pill_w, pill_h = int(rtw) + 44, 56
    pxr = W - art_in - 26 - pill_w
    pyr = py0 + (plate_h - pill_h) // 2
    cd.rounded_rectangle([pxr, pyr, pxr + pill_w, pyr + pill_h], radius=pill_h // 2,
                         fill=rgb + (255,), outline=darken(rgb, 0.45) + (255,), width=2)
    cd.text((pxr + pill_w / 2, pyr + pill_h / 2 + 1), rl, font=rf, fill=(16, 16, 20), anchor="mm")

    # Bloc nom + club, centre verticalement, a gauche
    text_x = art_in + 52
    max_w = pxr - text_x - 20
    name = nom.upper()
    size = 70
    nf = anton(size)
    while cd.textlength(name, font=nf) > max_w and size > 38:
        size -= 3
        nf = anton(size)
    cf = oswald(36, 500)
    nb = cd.textbbox((0, 0), name, font=nf, anchor="la")
    cb = cd.textbbox((0, 0), club, font=cf, anchor="la")
    nh, ch = nb[3] - nb[1], cb[3] - cb[1]
    gap = 12
    ty0 = py0 + (plate_h - (nh + gap + ch)) // 2 - nb[1]
    cd.text((text_x, ty0), name, font=nf, fill=(255, 255, 255), anchor="la")
    cd.text((text_x + 2, ty0 + nh + gap), club, font=cf, fill=lighten(rgb, 0.5), anchor="la")

    return card


# ---------------------------------------------------------------------------
# Layout v2 (actif) : silhouette biseautee + joueur detoure
# ---------------------------------------------------------------------------
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


def _crop_to_ratio(img, target_ratio):
    """Recadre `img` (centre) au ratio largeur/hauteur `target_ratio`, sans deformer.
    Rogne la dimension en trop (largeur ou hauteur) au lieu d'etirer au resize."""
    w, h = img.size
    cur_ratio = w / h
    if abs(cur_ratio - target_ratio) < 1e-3:
        return img
    if cur_ratio > target_ratio:
        new_w = max(1, round(h * target_ratio))
        x0 = (w - new_w) // 2
        return img.crop((x0, 0, x0 + new_w, h))
    new_h = max(1, round(w / target_ratio))
    y0 = (h - new_h) // 2
    return img.crop((0, y0, w, y0 + new_h))


def _radial(size, inner_rgb, outer_rgb, cx, cy, radius):
    """Degrade radial : eclaire le fond derriere la tete pour detacher le joueur.
    Calcule en basse resolution puis agrandi (le degrade est doux, aucune perte visible)."""
    w, h = size
    ss = 6
    small = Image.new("RGB", (max(1, w // ss), max(1, h // ss)), outer_rgb)
    d = ImageDraw.Draw(small)
    steps = 48
    for i in range(steps, 0, -1):
        t = i / steps
        r = radius / ss * t
        col = tuple(int(outer_rgb[k] + (inner_rgb[k] - outer_rgb[k]) * (1 - t) ** 1.6) for k in range(3))
        d.ellipse([cx / ss - r, cy / ss - r, cx / ss + r, cy / ss + r], fill=col)
    return small.resize(size, Image.Resampling.LANCZOS).convert("RGBA")


def _bottom_shade(size, start=0.5, max_a=210):
    w, h = size
    g = Image.new("L", (1, h), 0)
    s = int(h * start)
    for y in range(h):
        g.putpixel((0, y), 0 if y < s else int(max_a * ((y - s) / (h - s)) ** 1.4))
    layer = Image.new("RGBA", (w, h), (8, 9, 12, 0))
    layer.putalpha(g.resize((w, h)))
    return layer


def _bbox_opaque(alpha, boite=None):
    """bbox des pixels franchement opaques (cf. BUSTE_ALPHA_MIN), pas de la frange."""
    a = alpha.crop(boite) if boite else alpha
    bb = a.point(lambda v: 255 if v > BUSTE_ALPHA_MIN else 0).getbbox()
    if bb and boite:
        return (bb[0] + boite[0], bb[1] + boite[1], bb[2] + boite[0], bb[3] + boite[1])
    return bb


def _profil_largeur(alpha):
    """Largeur de la silhouette ligne par ligne, en px de l'image reduite.

    Mesure faite sur une reduction a _BUSTE_PROFIL_H lignes : a cette echelle une
    ligne vaut ~5 px source, ce qui suffit largement pour situer un cou, et la
    boucle Python reste negligeable. Renvoie (profil lisse, echelle source/reduit).
    """
    ech = alpha.height / _BUSTE_PROFIL_H
    # Seule la hauteur est reduite : garder la largeur d'origine preserve la mesure
    # des extremites de chaque ligne, qui est justement ce qu'on veut lire.
    petit = alpha.resize((alpha.width, _BUSTE_PROFIL_H),
                         Image.Resampling.BILINEAR).point(lambda v: 255 if v > 32 else 0)
    larg = []
    for y in range(petit.height):
        bb = petit.crop((0, y, petit.width, y + 1)).getbbox()
        larg.append(bb[2] - bb[0] if bb else 0)
    n = _BUSTE_LISSAGE // 2
    lisse = [sum(larg[max(0, y - n):y + n + 1]) / len(larg[max(0, y - n):y + n + 1])
             for y in range(len(larg))]
    return lisse, ech


def _menton(alpha, bb):
    """-> (y du menton sous le sommet du crane en px source ; largeur du cou ; fiable).

    Le profil largeur(ligne) d'un buste a une forme caracteristique : nul au sommet
    du crane, un ventre a hauteur des oreilles, un ETRANGLEMENT au cou, puis les
    epaules qui saturent la largeur du cadre. Le menton est cet etranglement.

    Il n'existe pas toujours : une chevelure longue (locs, dreadlocks) comble le cou
    et le profil devient monotone. On le detecte au lieu de renvoyer n'importe quoi.
    """
    tete = alpha.crop(bb)
    lisse, ech = _profil_largeur(tete)
    n = len(lisse)
    repli = (int((bb[3] - bb[1]) * BUSTE_MENTON_DEFAUT), 0.0, False)
    if n < 8:
        return repli

    # Haut des epaules : premiere ligne, dans la moitie basse, ou la silhouette sature.
    seuil = 0.95 * max(lisse)
    y_ep = next((y for y in range(n // 2, n) if lisse[y] >= seuil), n - 1)
    # Cou : minimum du profil entre le quart superieur et les epaules.
    a = max(1, n // 4)
    if y_ep <= a + 1:
        return repli
    y_cou = min(range(a, y_ep), key=lambda y: lisse[y])
    # Oreilles : ligne la plus large au-dessus du cou. Il faut un vrai creux dessous.
    y_or = max(range(y_cou), key=lambda y: lisse[y])
    if not (y_or < y_cou and lisse[y_cou] < _BUSTE_CREUX * lisse[y_or]):
        return repli
    return int(round(y_cou * ech)), lisse[y_cou], True


def _cadre_buste(art_src):
    """Cadrage S2 -> calque W x H contenant le joueur cale et mis a l'echelle.

    Deux invariants, et c'est tout ce qui fait l'homogeneite de la planche :
      - le menton est a BUSTE_MENTON_Y, quelle que soit la coiffure ;
      - la tete est mise a l'echelle sur une estimation du VISAGE et non de la
        silhouette (cf. BUSTE_TETE_H), pour qu'une coiffure haute monte dans le
        cadre au lieu de rapetisser le visage.

    Deux butees, qui n'interviennent qu'aux extremes : la chevelure n'est rognee que
    jusqu'a BUSTE_ROGNAGE_MAX (au-dela on rapetisse), et le buste doit descendre sous
    le separateur (sinon un trou apparait au-dessus du bandeau).

    A la difference du profil S1, l'image finit PLUS GRANDE que la carte : les
    coordonnees de collage sont donc negatives et il faut decouper la zone visible
    soi-meme, alpha_composite() n'acceptant pas de destination hors cadre."""
    alpha = art_src.getchannel("A")
    bb = _bbox_opaque(alpha) or (0, 0, *art_src.size)
    bh = max(1, bb[3] - bb[1])
    y_menton, l_cou, fiable = _menton(alpha, bb)
    # Moyenne geometrique hauteur de tete / largeur de cou (cf. BUSTE_TETE_H). Sans
    # cou exploitable, on retombe sur la seule hauteur, chevelure comprise.
    tete = ((y_menton * l_cou * BUSTE_COU_RATIO) ** 0.5
            if fiable and l_cou > 0 else y_menton)
    k = (H * BUSTE_TETE_H) / max(tete, 1)
    # Le buste doit descendre sous le separateur, sinon un trou apparait au-dessus du
    # bandeau. Le menton etant fixe, ce qu'il reste a couvrir est ce qui est SOUS lui.
    sous_menton = bh - y_menton
    if sous_menton > 0:
        k = max(k, ((BAND_TOP + BUSTE_RECOUVREMENT) - BUSTE_MENTON_Y) / sous_menton)
    # Les bornes de taille passent APRES le recouvrement, et non avant. Un rendu cadre
    # en gros plan (Minel, sorti de l'ancien prompt "headshot") n'a presque rien sous le
    # menton : pour couvrir le bandeau il fallait l'agrandir de moitie, et le sommet du
    # crane sortait de la carte -- le recouvrement annulait la borne. Dans cet ordre
    # c'est la taille de tete qui a le dernier mot, et il ne reste au pire que quelques
    # pixels de fond au-dessus du bandeau, la ou le fondu en pied l'a deja noirci.
    # L'estimation du visage peut deraper (cou epais, cou fin) : on borne la hauteur
    # crane -> menton qu'elle produit, sinon la tete flotte haut ou ecrase le cou.
    k = min(max(k, (H * BUSTE_TETE_MIN) / max(y_menton, 1)),
            (H * BUSTE_TETE_MAX) / max(y_menton, 1))
    k = min(k, (BUSTE_MENTON_Y - BORDER + BUSTE_ROGNAGE_MAX) / max(y_menton, 1))

    scaled = art_src.resize((max(1, round(art_src.width * k)),
                             max(1, round(art_src.height * k))), Image.Resampling.LANCZOS)
    sa = scaled.getchannel("A")
    sb = _bbox_opaque(sa) or (0, 0, *scaled.size)

    # Centrage horizontal sur la TETE et non sur la silhouette entiere : un buste de
    # trois quarts a les epaules decalees, et c'est le visage qu'on veut au milieu.
    haut_tete = _bbox_opaque(sa, (0, sb[1], scaled.width,
                                  min(scaled.height, sb[1] + max(1, int(y_menton * k)))))
    cx = ((haut_tete[0] + haut_tete[2]) / 2 if haut_tete else (sb[0] + sb[2]) / 2)
    x = int(round(W / 2 - cx))
    y = BUSTE_MENTON_Y - int(round(y_menton * k)) - sb[1]

    art = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    sx, sy = max(0, -x), max(0, -y)
    dx, dy = max(0, x), max(0, y)
    cw = min(scaled.width - sx, W - dx)
    ch = min(scaled.height - sy, H - dy)
    if cw > 0 and ch > 0:
        art.alpha_composite(scaled.crop((sx, sy, sx + cw, sy + ch)), (dx, dy))
    return art


def compose_v2(cutout, nom, club, rarete, poste="", zoom=PLAYER_ZOOM, cadrage=None):
    """Compose la carte (PIL RGBA) layout v2 : joueur detoure (dezoom `zoom`, cale
    sur sa bbox alpha — centre sur le joueur et non sur l'image, sommet du crane a
    PLAYER_TOP_MARGIN sous le liseré) sur fond radial de rarete, fondu en pied, puis
    bandeau noir pleine largeur englobant l'ecusson (a cheval sur le separateur de
    couleur) + le nom + le poste. `cutout` : portrait detoure (fond transparent) ;
    un portrait brut plein cadre marche en fallback (passer zoom=1.0)."""
    rgb = RARITY_RGB.get(rarete, (150, 150, 150))
    out = _outer_poly()
    inn = _inner_poly(BORDER)

    # Carte noire (silhouette a coins coupes)
    card = Image.new("RGBA", (W, H), (12, 13, 16, 255))
    card.putalpha(_poly_mask((W, H), out))

    # Fond radial de rarete + joueur, clippe au polygone interieur
    bg = _radial((W, H), lighten(rgb, 0.10), darken(rgb, 0.62), W * 0.5, H * 0.34, H * 0.78)
    art_src = _crop_to_ratio(cutout.convert("RGBA"), W / H)
    if cadrage == "buste":
        art = _cadre_buste(art_src)
    elif zoom >= 1.0:
        art = art_src.resize((W, H), Image.Resampling.LANCZOS)
    else:
        sw, sh = max(1, int(W * zoom)), max(1, int(H * zoom))
        scaled = art_src.resize((sw, sh), Image.Resampling.LANCZOS)
        # Calage sur le contenu reel (bbox alpha), pas sur le cadre de l'image :
        # sinon les chevelures volumineuses sont tranchees par le liseré superieur.
        bb = scaled.getchannel("A").getbbox() or (0, 0, sw, sh)
        art = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        art.alpha_composite(scaled, ((W - (bb[2] - bb[0])) // 2 - bb[0],
                                     (BORDER + PLAYER_TOP_MARGIN) - bb[1]))
    bg.alpha_composite(art)
    # Ombre en pied : la nuque se fond dans le bandeau au lieu d'etre tranchee net
    bg.alpha_composite(_bottom_shade((W, H), start=0.58, max_a=225))
    bg.putalpha(_poly_mask((W, H), inn))
    card.alpha_composite(bg)

    cd = ImageDraw.Draw(card)
    # Liseré rarete autour de la partie haute (dessine avant le bandeau)
    cd.line(inn + [inn[0]], fill=rgb, width=3, joint="curve")

    # Bandeau noir en pied, clippe a la silhouette EXTERIEURE (masque le liseré bas)
    band_mask = _poly_mask((W, H), out)
    ImageDraw.Draw(band_mask).rectangle([0, 0, W, BAND_TOP - 1], fill=0)
    band = Image.new("RGBA", (W, H), (10, 11, 14, 255))
    band.putalpha(band_mask)
    card.alpha_composite(band)
    # Seul liseré conserve : le separateur entre la partie haute et le bandeau
    cd.line([(BORDER, BAND_TOP), (W - BORDER, BAND_TOP)], fill=rgb, width=3)

    # Ecusson sur disque blanc, a cheval sur le separateur (bas-gauche).
    # Rien du tout si le club n'a pas de logo : sinon un disque blanc VIDE
    # (cas du set « Legendes Starligue », qui n'a pas de fichier d'ecusson).
    r = DISC_R
    disc_c = (BORDER + 118, BAND_TOP)
    logo_path = os.path.join(LOGOS, slugify(club) + ".png")
    has_logo = os.path.exists(logo_path)
    if has_logo:
        disc = Image.new("RGBA", (r * 2 + 8, r * 2 + 8), (0, 0, 0, 0))
        ImageDraw.Draw(disc).ellipse([4, 4, r * 2 + 3, r * 2 + 3], fill=(248, 249, 250, 255),
                                     outline=(12, 13, 16, 255), width=4)
        card.alpha_composite(disc, (disc_c[0] - r - 4, disc_c[1] - r - 4))
        ls = LOGO_SIZE
        logo = Image.open(logo_path).convert("RGBA").resize((ls, ls), Image.Resampling.LANCZOS)
        card.alpha_composite(logo, (disc_c[0] - ls // 2, disc_c[1] - ls // 2))

    # Nom (grand) + poste (dessous), alignes a droite, bas-droite
    x_r = W - BORDER - 36
    name = nom.upper()
    max_w = x_r - (disc_c[0] + (r if has_logo else 0) + 28)
    plabel = (poste or "").upper()
    pf = oswald(POSTE_SIZE, 500)

    # Calage sur les LIGNES DE BASE (ancre "s"), jamais sur la boite d'encre : celle-ci
    # grandit avec les accents, si bien qu'un nom accentue (RÉMI) remontait de la
    # hauteur de son accent et une cedille (GONÇALO) de sa descendante — les deux
    # finissaient reduits sans raison. Avec la ligne de base, tous les noms s'alignent
    # et seule l'encre reellement plus haute est prise en compte par la garde.
    poste_baseline = H - POSTE_BASELINE_FROM_BOTTOM
    if plabel:
        # Hauteur de capitale mesuree sur un glyphe de REFERENCE et non sur le libelle :
        # « ARRIÈRE DROIT » a une encre plus haute que « PIVOT » a cause du E accent
        # grave, ce qui decalerait la ligne de base du nom d'une carte a l'autre.
        cap = -cd.textbbox((0, 0), "H", font=pf, anchor="rs")[1]
        name_baseline = poste_baseline - cap - NAME_POSTE_GAP
    else:
        name_baseline = poste_baseline

    # Auto-reduction sur DEUX contraintes : la largeur dispo, et le fait que l'encre
    # du nom ne vienne pas toucher le separateur de couleur.
    size = NAME_SIZE
    while True:
        nf = anton(size)
        nb = cd.textbbox((0, 0), name, font=nf, anchor="rs")
        fits_w = cd.textlength(name, font=nf) <= max_w
        fits_h = (name_baseline + nb[1]) >= BAND_TOP + TEXT_TOP_CLEARANCE
        if (fits_w and fits_h) or size <= NAME_MIN_SIZE:
            break
        size -= 3

    cd.text((x_r, name_baseline), name, font=nf, fill=(255, 255, 255), anchor="rs")
    if plabel:
        cd.text((x_r, poste_baseline), plabel, font=pf, fill=lighten(rgb, 0.55), anchor="rs")
    return card


def _cache_path(card_id):
    return os.path.join(CACHE_DIR, f"{DESIGN_VERSION}_{card_id}.png")


def _read_cache(path):
    """Lit le PNG en cache, ou None. Synchrone : appele via asyncio.to_thread."""
    try:
        with open(path, "rb") as f:
            return f.read()
    except OSError:
        return None


async def _fetch_portrait(session, url):
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        async with session.get(url, headers=headers) as resp:
            if resp.status == 200:
                return Image.open(io.BytesIO(await resp.read()))
    except Exception:
        pass
    return None


def _load_cutout(card_id):
    """Portrait detoure local (assets/cutouts/<id>.webp) ou None s'il manque."""
    p = os.path.join(CUTOUTS, f"{card_id}.webp")
    if os.path.exists(p):
        try:
            return Image.open(p).convert("RGBA")
        except Exception:
            return None
    return None


async def get_card_bytes(card, session=None):
    """Renvoie le PNG (bytes) de la carte composee (layout v2), cache disque par id+version.

    Utilise le portrait detoure `assets/cutouts/<id>.webp` s'il existe ; sinon retombe
    sur le portrait brut telecharge (plein cadre). `session` : aiohttp.ClientSession
    optionnelle (ouverte a la volee au besoin). Retourne None si aucune image dispo.

    Tout le travail PIL (~350 ms par carte) et les I/O disque partent dans un thread :
    sinon ils bloquent l'event loop et le bot ne repond plus a personne pendant
    l'ouverture d'un lot de packs.
    """
    path = _cache_path(card["id"])
    cached = await asyncio.to_thread(_read_cache, path)
    if cached is not None:
        return cached

    art = await asyncio.to_thread(_load_cutout, card["id"])
    is_cutout = art is not None
    if art is None:
        # Fallback : pas de cutout -> portrait brut
        own_session = session is None
        if own_session:
            import aiohttp
            session = aiohttp.ClientSession()
        try:
            art = await _fetch_portrait(session, card.get("image_url"))
        finally:
            if own_session:
                await session.close()
    if art is None:
        return None

    # Detoure -> dezoom design ; portrait brut -> plein cadre (evite l'effet "flottant")
    zoom = PLAYER_ZOOM if is_cutout else 1.0
    # Les portraits S2 sont des rendus Midjourney deja cadres serre : ils passent par
    # le profil "buste", sans quoi le bot enverrait un cadrage DIFFERENT de celui que
    # tools/finalize_s2.py fait valider (tetes plus petites, vide aux epaules).
    # Le profil mesure la silhouette dans le canal alpha : il lui faut un vrai
    # detourage, jamais un portrait brut telecharge en repli.
    cadrage = "buste" if (is_cutout and card.get("saison") == 2) else None
    return await asyncio.to_thread(_render_and_cache, art, card, zoom, path, cadrage)


def _render_and_cache(art, card, zoom, path, cadrage=None):
    """Rendu + ecriture du cache. Synchrone : appele via asyncio.to_thread."""
    img = compose_v2(art, card["nom"], card["club"], card["rarete"], card.get("poste", ""),
                     zoom=zoom, cadrage=cadrage)
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="PNG")
    data = buf.getvalue()

    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(path, "wb") as f:
            f.write(data)
    except OSError:
        pass
    return data
