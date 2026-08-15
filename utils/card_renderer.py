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


def compose_v2(cutout, nom, club, rarete, poste="", zoom=PLAYER_ZOOM):
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
    if zoom >= 1.0:
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
            art = await _fetch_portrait(session, card["image_url"])
        finally:
            if own_session:
                await session.close()
    if art is None:
        return None

    # Detoure -> dezoom design ; portrait brut -> plein cadre (evite l'effet "flottant")
    zoom = PLAYER_ZOOM if is_cutout else 1.0
    return await asyncio.to_thread(_render_and_cache, art, card, zoom, path)


def _render_and_cache(art, card, zoom, path):
    """Rendu + ecriture du cache. Synchrone : appele via asyncio.to_thread."""
    img = compose_v2(art, card["nom"], card["club"], card["rarete"], card.get("poste", ""), zoom=zoom)
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
