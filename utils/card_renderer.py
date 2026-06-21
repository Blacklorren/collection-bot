"""Rendu de carte style TCG (layout v2) : silhouette noire a coins biseautes, fond degrade
couleur de rarete + joueur detoure pose dessus, ecusson du club sur disque blanc (bas-gauche),
nom (Anton) + poste (Oswald) alignes a droite (bas-droite).

Expose :
- compose_v2(cutout, nom, club, rarete, poste) -> PIL.Image  (rendu pur, synchrone, layout actif)
- compose(portrait, nom, club, rarete) -> PIL.Image          (ancien layout v1, conserve)
- get_card_bytes(card, session=None) -> bytes | None          (cutout/portrait + rend + cache disque)
"""
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
CACHE_DIR = os.path.join(ROOT, "assets", "card_cache")

# Incrementer pour invalider le cache disque quand le design change
DESIGN_VERSION = "v3"

W, H = 992, 1240
R = 40          # rayon des coins (carte, layout v1)
BLACK = 30      # bordure noire externe (v1)
FRAME = 18      # epaisseur du cadre stylise (v1)

CHAMFER = 140   # taille du coin biseaute (bas-gauche + haut-droite, v2)
BORDER = 42     # epaisseur de la bordure noire (v2)

# Reglages design v2 (bandeau bas + cadrage joueur)
PLAYER_ZOOM = 0.87   # echelle du joueur detoure (haut cale sur le liseré superieur)
BAND_TOP = 1012      # y du haut du bandeau noir / du separateur de couleur
LOGO_SIZE = 120      # taille de l'ecusson dans le bandeau
DISC_R = 72          # rayon du disque blanc sous l'ecusson

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
    """Compose la carte (PIL RGBA) layout v2 : joueur detoure (dezoom `zoom`, centre
    en X, haut cale sur le liseré superieur) sur fond degrade de rarete, puis bandeau
    noir pleine largeur en pied englobant l'ecusson (a cheval sur le separateur de
    couleur) + le nom + le poste. `cutout` : portrait detoure (fond transparent) ;
    un portrait brut plein cadre marche en fallback (passer zoom=1.0)."""
    rgb = RARITY_RGB.get(rarete, (150, 150, 150))
    out = _outer_poly()
    inn = _inner_poly(BORDER)

    # Carte noire (silhouette a coins coupes)
    card = Image.new("RGBA", (W, H), (12, 13, 16, 255))
    card.putalpha(_poly_mask((W, H), out))

    # Fond degrade de rarete + joueur, clippe au polygone interieur
    bg = _vgradient((W, H), darken(rgb, 0.15), darken(rgb, 0.6))
    art_src = cutout.convert("RGBA")
    if zoom >= 1.0:
        art = art_src.resize((W, H), Image.Resampling.LANCZOS)
    else:
        sw, sh = max(1, int(W * zoom)), max(1, int(H * zoom))
        scaled = art_src.resize((sw, sh), Image.Resampling.LANCZOS)
        art = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        art.alpha_composite(scaled, ((W - sw) // 2, BORDER))  # centre X, haut au liseré
    bg.alpha_composite(art)
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

    # Ecusson sur disque blanc, a cheval sur le separateur (bas-gauche)
    r = DISC_R
    disc_c = (BORDER + 118, BAND_TOP)
    disc = Image.new("RGBA", (r * 2 + 8, r * 2 + 8), (0, 0, 0, 0))
    ImageDraw.Draw(disc).ellipse([4, 4, r * 2 + 3, r * 2 + 3], fill=(248, 249, 250, 255),
                                 outline=(12, 13, 16, 255), width=4)
    card.alpha_composite(disc, (disc_c[0] - r - 4, disc_c[1] - r - 4))
    logo_path = os.path.join(LOGOS, slugify(club) + ".png")
    if os.path.exists(logo_path):
        ls = LOGO_SIZE
        logo = Image.open(logo_path).convert("RGBA").resize((ls, ls), Image.Resampling.LANCZOS)
        card.alpha_composite(logo, (disc_c[0] - ls // 2, disc_c[1] - ls // 2))

    # Nom (grand) + poste (dessous), alignes a droite, bas-droite
    x_r = W - BORDER - 36
    name = nom.upper()
    size = 80
    nf = anton(size)
    max_w = x_r - (disc_c[0] + r + 28)
    while cd.textlength(name, font=nf) > max_w and size > 40:
        size -= 3
        nf = anton(size)
    pf = oswald(38, 500)
    nb = cd.textbbox((0, 0), name, font=nf, anchor="ra")
    pb = cd.textbbox((0, 0), (poste or "").upper(), font=pf, anchor="ra")
    nh, ph = nb[3] - nb[1], pb[3] - pb[1]
    gap = 8
    bottom_y = H - BORDER - 46
    if poste:
        poste_top = bottom_y - ph
        name_top = poste_top - gap - nh
        cd.text((x_r, name_top), name, font=nf, fill=(255, 255, 255), anchor="ra")
        cd.text((x_r, poste_top), poste.upper(), font=pf, fill=lighten(rgb, 0.55), anchor="ra")
    else:
        cd.text((x_r, bottom_y - nh), name, font=nf, fill=(255, 255, 255), anchor="ra")
    return card


def _cache_path(card_id):
    return os.path.join(CACHE_DIR, f"{DESIGN_VERSION}_{card_id}.png")


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
    optionnelle (ouverte a la volee au besoin). Retourne None si aucune image dispo."""
    path = _cache_path(card["id"])
    if os.path.exists(path):
        with open(path, "rb") as f:
            return f.read()

    art = _load_cutout(card["id"])
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
