"""Recupere postes + portraits officiels des joueurs sur lnh.fr et enrichit le manifest S2.
(OFFLINE, dev only.)

Remplace tools/scrape_postes.py, casse depuis la refonte du site :
  - la ligue a change de slug : liquimoly-starligue -> daikin-starligue
  - l'effectif n'est plus une suite de <div class="name">/<div class="description">
    mais des blocs <a class="players-listing-item" href=".../joueurs/<slug>"> qui
    portent AUSSI l'URL du portrait dans un style background:url(...)

Une seule passe recupere donc le poste, le numero et la photo de reference qui
servira de --oref a Midjourney.

Entree : data/roster_s2.json   (produit par tools/build_manifest_s2.py)
Sortie : data/roster_s2.json   (enrichi : poste, numero, lnh_slug, ref_url, ref_file)
         refs/<id>.png         (portrait officiel telecharge)

NOTE saison 2026-27 : au 6 aout 2026 la LNH n'a pas encore publie les portraits,
toutes les fiches renvoient small_silhouette.png. Le script le detecte, ne
telecharge rien et le signale. Relancer quand les photos sortent : les postes,
eux, sont deja exploitables.

Usage :
    python tools/fetch_lnh_s2.py                # postes + telechargement des refs
    python tools/fetch_lnh_s2.py --dry-run      # rapport seul, aucun fichier ecrit
    python tools/fetch_lnh_s2.py --force        # re-telecharge les refs deja presentes
"""
import argparse
import difflib
import html as htmllib
import io
import json
import os
import re
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request

from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MANIFEST = os.path.join(ROOT, "data", "roster_s2.json")
ALIASES = os.path.join(ROOT, "data", "lnh_aliases.json")
COMPLEMENTS = os.path.join(ROOT, "data", "roster_complements.json")
REFS_MANUELLES = os.path.join(ROOT, "data", "refs_manuelles.json")
REFS_DIR = os.path.join(ROOT, "refs")

BASE = "https://www.lnh.fr/daikin-starligue/equipes/"          # pages club (logos)
CLUBS_EFFECTIF = "https://www.lnh.fr/daikin-starligue/clubs-effectif?team="
AJAX = "https://www.lnh.fr/ajaxpost1"

# club canonique (tel que dans le manifest) -> slug de page LNH 2026-27
CLUB_PAGES = {
    "Aix": "provence-aix-universite-club",
    "Caen": "caen-handball",
    "Cesson-Rennes": "cesson-rennes-metropole-hb",
    "Chambéry": "chambery-savoie-mt-blanc-handball",
    "Chartres": "c-chartres-metropole-handball",
    "Dunkerque": "dunkerque-handball-grand-littoral",
    "Limoges": "limoges-handball",
    "Montpellier": "montpellier-handball",
    "Nantes": "hbc-nantes",
    "Nîmes": "usam-nimes-gard",
    "Paris": "paris-saint-germain-handball",
    "Saint-Raphaël": "saint-raphael-var-handball",
    "Saran": "saran-loiret-handball",
    "Sélestat": "selestat-alsace-handball",
    "Toulouse": "fenix-toulouse-handball",
    "Tremblay": "tremblay-handball",
}

SECTION = re.compile(r"<h2>(.*?)</h2>(.*?)(?=<h2>|\Z)", re.S)
ITEM = re.compile(r'<a class="players-listing-item[^"]*"\s+href="([^"]+)"\s*>(.*?)</a>', re.S)
PIC = re.compile(r"background:\s*url\(([^)]+)\)")
NAME = re.compile(r'<div class="name">(.*?)</div>', re.S)
DESC = re.compile(r'<div class="description">(.*?)</div>', re.S)
NUM = re.compile(r'<div class="number">\s*#?(\d+)\s*</div>')

UA = {"User-Agent": "Mozilla/5.0"}


def norm(name):
    """Nom normalise pour rapprochement : sans accents, sans ponctuation, majuscules."""
    name = htmllib.unescape(str(name or ""))
    n = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    n = re.sub(r"[^A-Za-z0-9 ]", " ", n)
    return re.sub(r"\s+", " ", n).strip().upper()


def subset_match(target, roster):
    """Rattrape les ecarts de tokens (2e prenom, particule, nom compose) : match si
    l'un des deux ensembles de tokens contient l'autre, avec >= 2 tokens communs."""
    tset = set(target.split())
    best = None
    for rname, entry in roster.items():
        rset = set(rname.split())
        common = tset & rset
        if len(common) >= 2 and (tset <= rset or rset <= tset):
            if best is None or len(common) > best[0]:
                best = (len(common), entry)
    return best[1] if best else None


def fuzzy_match(target, roster, cutoff=0.86):
    """Rattrape les coquilles du roster xlsx (VALERO/VALERA, ODRIORZOLA/ODRIOZOLA).
    Volontairement strict, et chaque rapprochement flou est affiche pour controle."""
    close = difflib.get_close_matches(target, list(roster), n=1, cutoff=cutoff)
    return (close[0], roster[close[0]]) if close else (None, None)


def fetch(url, timeout=30, data=None):
    req = urllib.request.Request(url, data=data, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def slugify(s):
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Za-z0-9]+", "-", s).strip("-").lower()


def parse_club(club):
    """-> {nom_normalise: {poste, numero, photo, lnh_slug, equipe}} pour un club.

    On passe par clubs-effectif?team=<clef> et non par /equipes/<slug> : cette page
    liste AUSSI le centre de formation, ou se trouvent les joueurs que le xlsx
    contient mais que la page club ignore. Son contenu arrive par un POST ajax dont
    tous les parametres sont lisibles dans la page (rien n'est code en dur, la
    saison courante suit donc automatiquement l'option selectionnee)."""
    key = slugify(club)
    page = fetch(CLUBS_EFFECTIF + key).decode("utf-8", "ignore")
    tid = re.search(r'name="teams_id"[^>]*value="(\d+)"', page)
    sid = re.search(r'<option[^>]*value="(\d+)"[^>]*selected', page)
    uni = re.search(r'name="univers"[^>]*value="([^"]+)"', page)
    if not (tid and sid and uni):
        raise RuntimeError(f"parametres ajax introuvables sur clubs-effectif?team={key}")

    body = urllib.parse.urlencode({
        "seasons_id": sid.group(1),
        "teams_id": tid.group(1),
        "univers": uni.group(1),
        "contents_controller": "sportsPlayers",
        "contents_action": "team_index_ajax",
        "cache": "yes",
        "cacheKeys": "univers,contents_controller,contents_action,seasons_id,teams_id",
    }).encode()
    html = fetch(AJAX, data=body).decode("utf-8", "ignore")

    roster = {}
    for titre, bloc in SECTION.findall(html):
        titre = re.sub(r"<.*?>", "", titre).strip()
        equipe = "formation" if "formation" in titre.lower() else "pro"
        for href, block in ITEM.findall(bloc):
            if "/joueurs/" not in href:
                continue  # staff (coach, prepa, kine) : /staffs/
            m = NAME.search(block)
            if not m:
                continue
            nom = htmllib.unescape(re.sub(r"<.*?>", "", m.group(1)).strip())
            poste = DESC.search(block)
            poste = htmllib.unescape(re.sub(r"<.*?>", "", poste.group(1)).strip()) if poste else None
            numero = NUM.search(block)
            photo = PIC.search(block)
            photo = photo.group(1).strip("\"' ") if photo else None
            roster[norm(nom)] = {
                "poste": poste,
                "numero": int(numero.group(1)) if numero else None,
                "photo": photo,
                "lnh_slug": href.rstrip("/").rsplit("/", 1)[-1],
                "nom_lnh": nom,
                "equipe": equipe,
            }
    return roster


def variants(url):
    """Le site expose deux tailles pour un meme portrait : <x>.png et small_<x>.png.
    Contre-intuitif mais verifie : la variante 'small_' est la PLUS grande
    (350x525 contre 288x432). On ne se fie donc pas au nom, on compare les pixels."""
    d, f = url.rsplit("/", 1)
    base = f[6:] if f.startswith("small_") else f
    return [f"{d}/{base}", f"{d}/small_{base}"]


def fetch_retry(url, tries=3):
    """lnh.fr coupe la connexion quand on enchaine trop vite : on reessaie en douceur."""
    for attempt in range(tries):
        try:
            return fetch(url, timeout=60)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None       # variante inexistante, inutile d'insister
            if attempt == tries - 1:
                return None
        except Exception:
            if attempt == tries - 1:
                return None
        time.sleep(1.5 * (attempt + 1))
    return None


def download_ref(url, dest, crop=None):
    """Telecharge la variante la plus definie disponible. -> URL retenue, ou None.

    crop=[gauche, haut, droite, bas] decoupe apres telechargement : utile pour les
    refs de secours prises dans une photo de presse ou le joueur n'est pas seul."""
    best = None  # (surface, data, url)
    for candidate in dict.fromkeys(variants(url) + [url]):
        data = fetch_retry(candidate)
        time.sleep(0.3)
        if not data or len(data) < 2000:  # placeholder / 1x1 : on n'en veut pas
            continue
        try:
            w, h = Image.open(io.BytesIO(data)).size
        except Exception:
            continue
        if best is None or w * h > best[0]:
            best = (w * h, data, candidate)
    if not best:
        return None
    if crop:
        Image.open(io.BytesIO(best[1])).convert("RGB").crop(tuple(crop)).save(dest, "PNG")
    else:
        with open(dest, "wb") as fh:
            fh.write(best[1])
    return best[2]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="rapport seul, rien n'est ecrit")
    ap.add_argument("--force", action="store_true", help="re-telecharge les refs deja presentes")
    args = ap.parse_args()

    if not os.path.exists(MANIFEST):
        raise SystemExit("data/roster_s2.json absent : lance d'abord tools/build_manifest_s2.py")
    players = json.load(open(MANIFEST, encoding="utf-8"))
    actifs = [p for p in players if not p.get("sorti")]

    if not args.dry_run:
        os.makedirs(REFS_DIR, exist_ok=True)

    rosters = {}
    for club in CLUB_PAGES:
        try:
            rosters[club] = parse_club(club)
            cf = sum(1 for e in rosters[club].values() if e.get("equipe") == "formation")
            print(f"{club:16s} {len(rosters[club]):3d} joueurs  (dont {cf:2d} en centre de formation)")
        except Exception as e:
            rosters[club] = {}
            print(f"{club:16s} ERR -> {e}")
        time.sleep(0.4)  # on ne martele pas le site

    # Surcharges manuelles : {id_joueur: "NOM TEL QUE SUR LNH"}
    aliases = json.load(open(ALIASES, encoding="utf-8")) if os.path.exists(ALIASES) else {}

    unmatched, silhouettes, telecharges, flous = [], [], 0, []
    postes = 0
    for p in actifs:
        roster = rosters.get(p["club"])
        if not roster:
            continue
        cible = norm(aliases.get(p["id"]) or p["nom"])
        entry = roster.get(cible) or subset_match(cible, roster)
        if not entry:
            trouve, entry = fuzzy_match(cible, roster)
            if entry:
                flous.append((p, trouve))
        if not entry:
            unmatched.append(p)
            continue

        if entry["poste"]:
            p["poste"] = entry["poste"]
            postes += 1
        p["numero"] = entry["numero"]
        p["lnh_slug"] = entry["lnh_slug"]
        p["equipe"] = entry.get("equipe")

        photo = entry["photo"] or ""
        if not photo or "silhouette" in photo:
            silhouettes.append(p)
            continue

        dest = os.path.join(REFS_DIR, f"{p['id']}.png")
        if os.path.exists(dest) and not args.force:
            p["ref_file"] = os.path.relpath(dest, ROOT).replace("\\", "/")
            continue
        if args.dry_run:
            p["ref_url"] = photo
            telecharges += 1
            continue
        used = download_ref(photo, dest)
        if used:
            p["ref_url"] = used
            p["ref_file"] = os.path.relpath(dest, ROOT).replace("\\", "/")
            telecharges += 1
        else:
            silhouettes.append(p)

    # Refs de secours pour les joueurs que la LNH laisse en silhouette : URL trouvee
    # a la main sur le site du club. Stockees a part pour survivre a un rerun.
    manuelles = json.load(open(REFS_MANUELLES, encoding="utf-8")) if os.path.exists(REFS_MANUELLES) else {}
    par_id = {p["id"]: p for p in actifs}
    sans_photo_lnh = {s["id"] for s in silhouettes}
    for pid, spec in manuelles.items():
        p = par_id.get(pid)
        if not p:
            print(f"  ref manuelle ignoree, id inconnu : {pid}")
            continue
        # Vrai repli : des que la LNH publie le portrait officiel, il reprend la main.
        if pid not in sans_photo_lnh:
            continue
        url = spec if isinstance(spec, str) else spec.get("url", "")
        crop = None if isinstance(spec, str) else spec.get("crop")
        dest = os.path.join(REFS_DIR, f"{pid}.png")
        if os.path.exists(dest) and not args.force:
            p["ref_file"] = os.path.relpath(dest, ROOT).replace("\\", "/")
            silhouettes = [s for s in silhouettes if s["id"] != pid]
            continue
        if args.dry_run:
            continue
        used = download_ref(url, dest, crop=crop)
        if used:
            p["ref_url"] = used
            p["ref_file"] = os.path.relpath(dest, ROOT).replace("\\", "/")
            telecharges += 1
            silhouettes = [s for s in silhouettes if s["id"] != pid]
            print(f"  ref manuelle OK : {p['nom']}")
        else:
            print(f"  ref manuelle EN ECHEC : {p['nom']} -> {url}")

    # Postes corriges a la main : la LNH en donne parfois un faux (Antonsen liste
    # ailier droit alors qu'il est pivot). Applique APRES le scrape, sinon la valeur
    # scrapee reprendrait la main a chaque run.
    if os.path.exists(COMPLEMENTS):
        corr = json.load(open(COMPLEMENTS, encoding="utf-8")).get("postes", {})
        par_id2 = {p["id"]: p for p in actifs}
        for pid, poste in corr.items():
            p = par_id2.get(pid)
            if p and p.get("poste") != poste:
                print(f"  poste corrige : {p['nom']} '{p.get('poste')}' -> '{poste}'")
                p["poste"] = poste

    if not args.dry_run:
        json.dump(players, open(MANIFEST, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    avec_ref = [p for p in actifs if p.get("ref_file")]
    print(f"\n--- {len(actifs)} joueurs actifs ---")
    print(f"  postes renseignes  : {postes}")
    print(f"  portraits recuperes: {telecharges}{' (dry-run)' if args.dry_run else ''}")
    print(f"  refs disponibles   : {len(avec_ref)}")
    if silhouettes:
        print(f"  sans photo LNH     : {len(silhouettes)}  (fiche en silhouette)")
    if flous:
        print(f"\n{len(flous)} rapprochements approximatifs (verifie que c'est bien le meme joueur) :")
        for p, trouve in flous:
            print(f"    - {p['nom']} ({p['club']})  ->  {trouve}")
    if unmatched:
        print(f"\n{len(unmatched)} joueurs absents de l'effectif LNH :")
        for p in unmatched:
            print(f"    - {p['id']:32s} {p['nom']} ({p['club']})")
        print("  Soit le joueur n'est pas encore enregistre, soit le nom differe :")
        print(f"  dans ce cas ajoute {{\"<id>\": \"NOM LNH\"}} dans {os.path.relpath(ALIASES, ROOT)}")
    if silhouettes and len(silhouettes) == len(actifs) - len(unmatched):
        print("\nLa LNH n'a publie AUCUN portrait pour l'instant : relance ce script")
        print("quand les photos officielles sortent (en general au coup d'envoi de la saison).")


if __name__ == "__main__":
    main()
