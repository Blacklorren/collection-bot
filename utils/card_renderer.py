"""Rendu de carte style TCG : bordure noire -> cadre metallique (couleur rarete) -> art du joueur,
plaque de nom noire (nom Anton + club Oswald + pastille de rarete), ecusson du club.

Expose :
- compose(portrait, nom, club, rarete) -> PIL.Image       (rendu pur, synchrone)
- get_card_bytes(card, session=None) -> bytes | None       (telecharge + rend + cache disque)
"""
import io
import os
import unicodedata

from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(__file__)
ROOT = os.path.dirname(HERE)
FONTS = os.path.join(ROOT, "assets", "fonts")
LOGOS = os.path.join(ROOT, "assets", "logos")
CACHE_DIR = os.path.join(ROOT, "assets", "card_cache")

# Incrementer pour invalider le cache disque quand le design change
DESIGN_VERSION = "v1"

W, H = 992, 1240
R = 40          # rayon des coins (carte)
BLACK = 30      # bordure noire externe
FRAME = 18      # epaisseur du cadre stylise

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


async def get_card_bytes(card, session=None):
    """Renvoie le PNG (bytes) de la carte composee, avec cache disque par id+version.

    `session` : aiohttp.ClientSession optionnelle. Si absente et qu'un telechargement
    est necessaire, une session ephemere est ouverte. Retourne None si le portrait
    est introuvable."""
    path = _cache_path(card["id"])
    if os.path.exists(path):
        with open(path, "rb") as f:
            return f.read()

    own_session = session is None
    if own_session:
        import aiohttp
        session = aiohttp.ClientSession()
    try:
        portrait = await _fetch_portrait(session, card["image_url"])
    finally:
        if own_session:
            await session.close()
    if portrait is None:
        return None

    img = compose(portrait, card["nom"], card["club"], card["rarete"])
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
