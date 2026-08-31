"""Publie les cartes de la Saison 2 dans cards.json. (OFFLINE, dev only.)

C'est le maillon qui manquait entre la chaine de production (roster_s2.json +
assets/cutouts/) et le bot : finalize_s2.py s'arrete aux cutouts, et rien
n'amenait ensuite les joueurs dans cards.json, le seul fichier que le bot lise.

Ce que fait la publication :
  - BACKFILL : toute carte deja presente sans champ "saison" recoit "saison": 1
    (les 275 cartes S1, Noel compris). C'est ce marqueur, et lui seul, qui permet
    au bot de garder la S1 en archive : plus tirable en pack, plus alignable en
    duel, mais toujours visible dans la collection.
  - AJOUT : chaque joueur de data/roster_s2.json recoit une carte "saison": 2.

Un joueur n'est publie QUE si son cutout existe (assets/cutouts/<id>.webp).
Publier une carte sans portrait ferait planter le bot au rendu, et pas seulement
sur cette carte : l'album de club assemble ses images dans un asyncio.gather()
sans return_exceptions, donc une carte manquante emporte l'album entier.
Le script est donc RE-JOUABLE : on le relance a chaque lot de cutouts termine,
il ne publie que les nouveaux et met a jour ceux qui ont change de rarete ou de
poste.

image_url pointe sur le portrait officiel LNH heberge dans le repo (refs/), qui
sert de filet : si un cutout venait a disparaitre, le rendu retombe dessus au
lieu de lever une KeyError. Les ids S1 (entiers + "noel_N") et S2 (slugs) ne se
recouvrent pas : aucune collection existante ne peut etre cassee par l'ajout.

A LANCER AVANT de deployer, et APRES tools/migration_s2.py (qui deduit les ids
de la S1 en lisant cards.json : si la S2 y est deja, il vise le mauvais jeu).

Usage :
    python tools/publier_s2.py                 # simulation, n'ecrit rien
    python tools/publier_s2.py --go            # applique, apres sauvegarde
    python tools/publier_s2.py --go --tout     # publie meme sans cutout (deconseille)
"""
import argparse
import json
import os
import shutil
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CARDS = os.path.join(ROOT, "cards.json")
MANIFEST = os.path.join(ROOT, "data", "roster_s2.json")
CUTOUTS = os.path.join(ROOT, "assets", "cutouts")

# Le repo sert d'hebergement pour les portraits officiels (cf docs/GENERATION_S2.md).
BASE_REFS = "https://raw.githubusercontent.com/Blacklorren/collection-bot/main/refs"

# Champs d'une carte, dans l'ordre d'ecriture de cards.json.
CHAMPS = ("id", "nom", "club", "rarete", "poste", "image_url", "saison")


def charger(chemin):
    with open(chemin, encoding="utf-8") as f:
        return json.load(f)


def ecrire_cards(cartes):
    """Meme mise en forme que le fichier d'origine : indent 2, accents litteraux."""
    with open(CARDS, "w", encoding="utf-8", newline="\n") as f:
        json.dump(cartes, f, ensure_ascii=False, indent=2)
        f.write("\n")


def carte_depuis_joueur(p):
    return {
        "id": p["id"],
        "nom": p["nom"],
        "club": p["club"],
        "rarete": p["rarete"],
        "poste": p.get("poste") or "",
        "image_url": f"{BASE_REFS}/{p['id']}.png",
        "saison": 2,
    }


def ordonner(carte):
    """Reordonne les cles pour que le diff de cards.json reste lisible."""
    return {k: carte[k] for k in CHAMPS if k in carte}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--go", action="store_true", help="applique reellement (sinon simulation)")
    ap.add_argument("--tout", action="store_true",
                    help="publie aussi les joueurs sans cutout (le bot ne saura pas les rendre)")
    args = ap.parse_args()

    cartes = charger(CARDS)
    roster = charger(MANIFEST)
    cutouts = {f[:-5] for f in os.listdir(CUTOUTS) if f.endswith(".webp")}

    # --- 1. backfill saison 1 ---
    a_marquer = [c for c in cartes if "saison" not in c]

    # --- 2. tri des joueurs S2 ---
    par_id = {str(c["id"]): c for c in cartes}
    nouveaux, maj, sans_cutout, sortis = [], [], [], []
    for p in roster:
        if p.get("sorti"):
            sortis.append(p)
            continue
        if p["id"] not in cutouts and not args.tout:
            sans_cutout.append(p)
            continue
        neuve = carte_depuis_joueur(p)
        ancienne = par_id.get(str(p["id"]))
        if ancienne is None:
            nouveaux.append(neuve)
        elif any(ancienne.get(k) != v for k, v in neuve.items()):
            maj.append((ancienne, neuve))

    # --- 3. rapport ---
    print(f"cards.json          : {len(cartes)} cartes")
    print(f"  a marquer saison 1: {len(a_marquer)}")
    print(f"roster_s2.json      : {len(roster)} joueurs")
    print(f"  cutout present    : {len([p for p in roster if p['id'] in cutouts])}")
    print(f"  NOUVELLES cartes  : {len(nouveaux)}")
    print(f"  cartes mises a jour: {len(maj)}")
    print(f"  sans cutout (ignores) : {len(sans_cutout)}")
    print(f"  marques sortis    : {len(sortis)}")

    if sans_cutout:
        from collections import Counter
        detail = Counter(p["club"] for p in sans_cutout)
        print("\n  Il manque encore le portrait de :")
        for club, n in detail.most_common():
            print(f"    {club:<18}{n:>3}")
        print("  -> relancer tools/finalize_s2.py, puis ce script (il est re-jouable).")

    if maj:
        print("\n  Changements sur des cartes deja publiees :")
        for ancienne, neuve in maj[:10]:
            ecarts = [f"{k}: {ancienne.get(k)!r} -> {v!r}"
                      for k, v in neuve.items() if ancienne.get(k) != v]
            print(f"    {neuve['nom']}: " + " ; ".join(ecarts))
        if len(maj) > 10:
            print(f"    ... et {len(maj) - 10} autre(s)")

    deja_s2 = len([c for c in cartes if c.get("saison") == 2])
    s1_final = len(a_marquer) + len(cartes) - len(a_marquer) - deja_s2
    print("")
    print(f"Apres publication   : {len(cartes) + len(nouveaux)} cartes "
          f"({s1_final} en saison 1, {deja_s2 + len(nouveaux)} en saison 2)")

    if not args.go:
        print("\nSIMULATION - rien n'a ete ecrit. Relance avec --go pour appliquer.")
        return

    # --- 4. ecriture ---
    horo = datetime.now().strftime("%Y%m%d-%H%M%S")
    bak = f"{CARDS}.avant-s2-{horo}.bak"
    shutil.copy2(CARDS, bak)
    print(f"\nSauvegarde          : {os.path.basename(bak)}")

    for c in a_marquer:
        c["saison"] = 1
    for ancienne, neuve in maj:
        ancienne.update(neuve)
    cartes.extend(nouveaux)
    ecrire_cards([ordonner(c) for c in cartes])

    # --- 5. controle de relecture ---
    relu = charger(CARDS)
    s1 = [c for c in relu if c.get("saison") == 1]
    s2 = [c for c in relu if c.get("saison") == 2]
    orphelines = [c for c in relu if "saison" not in c]
    ids = [str(c["id"]) for c in relu]
    doublons = {i for i in ids if ids.count(i) > 1}
    sans_art = [c["id"] for c in s2 if str(c["id"]) not in cutouts]

    print(f"\nControle de relecture :")
    print(f"  saison 1          : {len(s1)}")
    print(f"  saison 2          : {len(s2)}")
    print(f"  sans saison       : {len(orphelines)}  (doit valoir 0)")
    print(f"  ids en double     : {len(doublons)}  (doit valoir 0) {sorted(doublons)[:5]}")
    print(f"  S2 sans cutout    : {len(sans_art)}  (doit valoir 0 hors --tout)")
    print(f"\n{len(nouveaux)} carte(s) publiee(s). REDEMARRER LE BOT : cards.json est lu"
          f" une seule fois, a l'initialisation des cogs.")


if __name__ == "__main__":
    main()
