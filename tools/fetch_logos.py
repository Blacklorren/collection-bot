"""Recupere les ecussons manquants des clubs sur lnh.fr. (OFFLINE, dev only.)

compose_v2() cherche assets/logos/<slug>.png, ou <slug> est le club passe dans
slugify() (repli ascii + minuscules). Un club sans fichier est rendu SANS ecusson,
d'ou ce script a relancer a chaque changement d'effectif (montees/descentes).

Heureusement la LNH nomme ses medias avec exactement la meme clef :
    https://www.lnh.fr/medias/sports_teams/<slug>__logo__<saison>.png
On scrape donc la page du club et on retient l'URL dont le nom commence par
"<slug>__logo__", en prenant la saison la plus recente si plusieurs sont servies.

Les fichiers sont normalises en 200x200 RGBA, comme les 16 ecussons existants.

Entree : data/roster_s2.json  (pour la liste des clubs) + CLUB_PAGES de fetch_lnh_s2
Sortie : assets/logos/<slug>.png

Usage :
    python tools/fetch_logos.py              # seulement les manquants
    python tools/fetch_logos.py --force      # re-telecharge tout
    python tools/fetch_logos.py --club Caen  # un club precis
"""
import argparse
import io
import json
import os
import re
import sys
import unicodedata
import urllib.request

from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fetch_lnh_s2 import CLUB_PAGES  # noqa: E402  (meme table de slugs LNH)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MANIFEST = os.path.join(ROOT, "data", "roster_s2.json")
LOGOS = os.path.join(ROOT, "assets", "logos")

BASE = "https://www.lnh.fr/daikin-starligue/equipes/"
SIZE = 200  # format des ecussons deja en place
UA = {"User-Agent": "Mozilla/5.0"}


def slugify(s):
    """Identique a utils/card_renderer.slugify : c'est elle qui decide du nom de fichier."""
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Za-z0-9]+", "-", s).strip("-").lower()


def fetch(url, timeout=30):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
        return r.read()


def find_logo_url(club_slug, page_slug):
    """L'URL d'ecusson du club sur sa propre page (elle contient aussi celles des
    adversaires du calendrier, d'ou le filtre sur le prefixe)."""
    page = fetch(BASE + page_slug).decode("utf-8", "ignore")
    urls = set(re.findall(r'https://www\.lnh\.fr/medias/sports_teams/[^\s"\')<>]+', page))
    mine = [u for u in urls if os.path.basename(u).startswith(f"{club_slug}__logo__")]
    return sorted(mine)[-1] if mine else None  # saison la plus recente


def normalize(data):
    """Ajuste dans un carre SIZE x SIZE transparent, sans deformer."""
    im = Image.open(io.BytesIO(data)).convert("RGBA")
    bbox = im.getbbox()  # retire les marges transparentes eventuelles
    if bbox:
        im = im.crop(bbox)
    im.thumbnail((SIZE, SIZE), Image.LANCZOS)
    canvas = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    canvas.paste(im, ((SIZE - im.width) // 2, (SIZE - im.height) // 2), im)
    return canvas


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--club", default="")
    args = ap.parse_args()

    if args.club:
        clubs = [args.club]
    elif os.path.exists(MANIFEST):
        players = json.load(open(MANIFEST, encoding="utf-8"))
        clubs = sorted({p["club"] for p in players if not p.get("sorti")})
    else:
        clubs = sorted(CLUB_PAGES)

    os.makedirs(LOGOS, exist_ok=True)
    faits = ignores = 0
    for club in clubs:
        slug = slugify(club)
        dest = os.path.join(LOGOS, f"{slug}.png")
        if os.path.exists(dest) and not args.force:
            ignores += 1
            continue
        page_slug = CLUB_PAGES.get(club)
        if not page_slug:
            print(f"  {club:16s} SKIP (pas de page LNH connue)")
            continue
        try:
            url = find_logo_url(slug, page_slug)
            if not url:
                print(f"  {club:16s} ERR aucun '{slug}__logo__*' sur la page")
                continue
            normalize(fetch(url)).save(dest, "PNG")
            faits += 1
            print(f"  {club:16s} OK  {os.path.basename(url)} -> assets/logos/{slug}.png")
        except Exception as e:
            print(f"  {club:16s} ERR {e}")

    print(f"\n{faits} ecussons recuperes, {ignores} deja presents.")

    # Controle final : un club sans fichier = des cartes sans ecusson
    manquants = [c for c in clubs if not os.path.exists(os.path.join(LOGOS, slugify(c) + ".png"))]
    if manquants:
        print(f"ATTENTION toujours manquants : {', '.join(manquants)}")


if __name__ == "__main__":
    main()
