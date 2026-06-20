"""Recupere les postes des joueurs depuis les pages clubs de la LNH et les mappe
aux cartes par nom. (OFFLINE, dev only.)

Source : https://www.lnh.fr/liquimoly-starligue/equipes/<slug-club>
HTML statique, motif : <div class="name">NOM</div><div class="description">POSTE</div>

Usage :
    python tools/scrape_postes.py

Sortie :
    data/postes.json        -> { "<card_id>": "<poste>", ... }
    + rapport console des joueurs cards.json NON matches (a corriger a la main)

Le matching se fait sur le nom normalise (sans accents, majuscules, espaces compactes).
Ne modifie PAS cards.json : la fusion est laissee a l'etape d'integration.
"""
import html
import json
import os
import re
import sys
import unicodedata
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CARDS_JSON = os.path.join(ROOT, "cards.json")
OUT = os.path.join(ROOT, "data", "postes.json")

# club (tel que dans cards.json) -> slug de page LNH
CLUB_PAGES = {
    "Aix": "provence-aix-universite-club",
    "Cesson-Rennes": "cesson-rennes-metropole-hb",
    "Chambéry": "chambery-savoie-mt-blanc-handball",
    "Chartres": "c-chartres-metropole-handball",
    "Dijon": "dijon-metropole-handball",
    "Dunkerque": "dunkerque-handball-grand-littoral",
    "Istres": "istres-provence-handball",
    "Limoges": "limoges-handball",
    "Montpellier": "montpellier-handball",
    "Nantes": "hbc-nantes",
    "Nîmes": "usam-nimes-gard",
    "Paris": "paris-saint-germain-handball",
    "Saint-Raphaël": "saint-raphael-var-handball",
    "Sélestat": "selestat-alsace-handball",
    "Toulouse": "fenix-toulouse-handball",
    "Tremblay": "tremblay-handball",
    # "Légendes Starligue" : pas de page club -> postes a remplir a la main si besoin
}

BASE = "https://www.lnh.fr/liquimoly-starligue/equipes/"
ROW_RE = re.compile(
    r'<div class="name">(.*?)</div><div class="description">(.*?)</div>', re.S)
# postes a ignorer (staff)
NON_PLAYER = ("entraineur", "entraîneur", "coach", "preparateur", "kine", "medecin", "manager")


def norm(name):
    name = html.unescape(name)  # &apos; &amp; ... -> caracteres reels
    n = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    n = re.sub(r"[^A-Za-z0-9 ]", " ", n)  # apostrophes/traits d'union -> espace
    n = re.sub(r"\s+", " ", n).strip().upper()
    return n


def subset_match(target, roster):
    """Trouve un poste quand le nom de carte et celui du roster different par des
    tokens en plus/en moins (2e prenom, nom compose). Match si l'un des deux
    ensembles de tokens est inclus dans l'autre, avec >=2 tokens communs."""
    tset = set(target.split())
    best = None
    for rname, pos in roster.items():
        rset = set(rname.split())
        common = tset & rset
        if len(common) >= 2 and (tset <= rset or rset <= tset):
            if best is None or len(common) > best[0]:
                best = (len(common), pos)
    return best[1] if best else None


def fetch_html(slug):
    req = urllib.request.Request(BASE + slug, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", "ignore")


def main():
    cards = json.load(open(CARDS_JSON, encoding="utf-8"))
    # postes scrapes par club : {club: {nom_norm: poste}}
    rosters = {}
    for club, slug in CLUB_PAGES.items():
        try:
            html = fetch_html(slug)
        except Exception as e:
            print(f"ERR fetch {club} ({slug}) -> {e}")
            rosters[club] = {}
            continue
        roster = {}
        for raw_name, raw_pos in ROW_RE.findall(html):
            pos = re.sub(r"<.*?>", "", raw_pos).strip()
            if any(k in norm(pos).lower() for k in NON_PLAYER):
                continue
            roster[norm(raw_name)] = pos
        rosters[club] = roster
        print(f"{club}: {len(roster)} joueurs")

    postes = {}
    unmatched = []
    for c in cards:
        roster = rosters.get(c["club"])
        if not roster:
            continue
        target = norm(c["nom"])
        poste = roster.get(target) or subset_match(target, roster)
        if poste:
            postes[str(c["id"])] = poste
        else:
            unmatched.append((c["id"], c["nom"], c["club"]))

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump(postes, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    print(f"\n{len(postes)} postes mappes -> {OUT}")
    if unmatched:
        print(f"\n{len(unmatched)} joueurs NON matches (a corriger a la main) :")
        for cid, nom, club in unmatched:
            print(f"  - [{cid}] {nom} ({club})")


if __name__ == "__main__":
    main()
