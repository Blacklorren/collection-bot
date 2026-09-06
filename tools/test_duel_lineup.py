# -*- coding: utf-8 -*-
"""Tests hors-ligne de la composition automatique (`duel_engine.best_lineup`).

Sans Discord ni base : `best_lineup` est de la logique pure. Ce que ce fichier
verrouille, c'est qu'elle rend l'optimum EXACT de `team_power`. L'ancien glouton
poste par poste alignait pres de 4 joueurs sur 7 hors de leur poste et perdait
~13 % de puissance -- c'est ce qui remontait en "ma defense joue des gens au
mauvais poste alors que j'ai le titulaire".

    py -3 tools/test_duel_lineup.py
"""
import json, os, random, statistics, sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import duel_engine as E

SLOTS = E.SLOTS
FAILED = []


def check(name, cond, detail=""):
    print(("  OK   " if cond else "  ECHEC") + " " + name + ((" | " + detail) if detail else ""))
    if not cond:
        FAILED.append(name)


def card(cid, rarete, poste, club="X"):
    return {"id": cid, "nom": "C%s" % cid, "rarete": rarete, "poste": poste, "club": club}


def brute_optimum(cards):
    """Reference EXHAUSTIVE : on essaie tout, y compris laisser un poste vide.
    Exponentiel -- reserve aux toutes petites collections."""
    n = len(SLOTS)
    best = [-1.0]

    def rec(si, used, lu):
        if si == n:
            best[0] = max(best[0], E.team_power(lu)[0])
            return
        lu[SLOTS[si]] = None
        rec(si + 1, used, lu)
        for ci, c in enumerate(cards):
            if ci in used:
                continue
            lu[SLOTS[si]] = c
            rec(si + 1, used | {ci}, lu)
        lu[SLOTS[si]] = None

    rec(0, frozenset(), {s: None for s in SLOTS})
    return best[0]


def greedy_legacy(cards):
    """L'ancien algorithme, garde comme temoin de non-regression."""
    lineup, used = {s: None for s in SLOTS}, set()
    for slot in SLOTS:
        best, best_note, best_key = None, -1.0, None
        for c in cards:
            if id(c) in used:
                continue
            note = E.card_note(c, slot)
            if note > best_note:
                best, best_note, best_key = c, note, id(c)
        if best is not None:
            lineup[slot] = best
            used.add(best_key)
    return lineup


def hors_poste(lu):
    return sum(1 for s in SLOTS if lu[s] and E.normalize_poste(lu[s].get("poste")) != s)


print("\n=== 1. Le cas signale par les joueurs ===")
# Un Legendaire ailier gauche et un Rare gardien : le glouton mettait l'ailier
# dans les buts (16 > 8 x 1,4) et reversait le gardien sur l'aile.
duo = [card(1, "Légendaire", "Ailier Gauche"), card(2, "Rare", "Gardien")]
lu = E.best_lineup(duo)
check("le gardien Rare garde les buts", lu["GB"]["id"] == 2, "GB=%s" % lu["GB"]["nom"])
check("le Legendaire joue ailier gauche", lu["ALG"]["id"] == 1, "ALG=%s" % lu["ALG"]["nom"])
check("puissance > ancien glouton",
      E.team_power(lu)[0] > E.team_power(greedy_legacy(duo))[0],
      "%.1f vs %.1f" % (E.team_power(lu)[0], E.team_power(greedy_legacy(duo))[0]))

print("\n=== 2. Sept cartes, sept postes distincts : chacun chez soi ===")
rar = ["Commun", "Peu Commun", "Rare", "Épique", "Légendaire", "Rare", "Commun"]
sept = [card(i, rar[i], E.SLOT_LABELS[s]) for i, s in enumerate(SLOTS)]
lu = E.best_lineup(sept)
check("7 postes naturels occupes", hors_poste(lu) == 0,
      " ".join("%s:%s" % (s, lu[s]["nom"]) for s in SLOTS))

print("\n=== 3. Cas limites ===")
vide = {s: None for s in SLOTS}
check("collection vide", E.best_lineup([]) == vide)
check("None filtres", E.best_lineup([None, None]) == vide)
seule = [card(1, "Épique", "Pivot")]
lu = E.best_lineup(seule)
check("une seule carte -> a son poste",
      lu["PIV"] is seule[0] and sum(1 for s in SLOTS if lu[s]) == 1)
check("aucune carte sur deux postes",
      len({id(c) for c in lu.values() if c}) == sum(1 for c in lu.values() if c))
lu = E.best_lineup([card(i, "Rare", None) for i in range(10)])
check("cartes sans poste : 7 slots remplis quand meme",
      sum(1 for s in SLOTS if lu[s]) == 7)
lu = E.best_lineup([card(i, "Commun", "Gardien") for i in range(30)])
check("30 gardiens : 7 slots remplis, 7 cartes distinctes",
      sum(1 for s in SLOTS if lu[s]) == 7 and len({id(c) for c in lu.values() if c}) == 7)
lu = E.best_lineup([card(i, "Commun", None, club=None) for i in range(9)])
check("collection sans club renseigne", sum(1 for s in SLOTS if lu[s]) == 7)

print("\n=== 4. La synergie de club est bien prise en compte ===")
# 7 Rares du meme club (x1,55) contre les memes cartes eparpillees : le moteur
# doit preferer le bloc, meme si la somme des notes est identique.
bloc = [card(i, "Rare", E.SLOT_LABELS[s], club="FC") for i, s in enumerate(SLOTS)]
extra = [card(100 + i, "Épique", E.SLOT_LABELS[s], club="Club%d" % i) for i, s in enumerate(SLOTS[:2])]
lu = E.best_lineup(bloc + extra)
det = E.team_power(lu)[1]
check("un groupe de club est privilegie quand il paie", det["max_club_group"] >= 5,
      "groupe max %d, synergie x%.2f" % (det["max_club_group"], det["synergy"]))

print("\n=== 5. Optimum exact, verifie par recherche exhaustive ===")
CARDS = json.load(open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                    "cards.json"), encoding="utf-8"))
SAISON = max(c.get("saison", 1) for c in CARDS)
POOL = [c for c in CARDS if c.get("saison") == SAISON and c.get("rarete") != "Noël"]
rng = random.Random(2026)
manque = 0.0
for _ in range(40):
    sub = rng.sample(POOL, rng.choice([2, 4, 6, 7, 8]))
    manque = max(manque, brute_optimum(sub) - E.team_power(E.best_lineup(sub))[0])
check("optimum exact (synergie comprise)", manque < 1e-9, "manque max %.6f" % manque)

print("\n=== 6. Non-regression vs l'ancien glouton (600 collections) ===")
gains, mis_new, mis_old, troues = [], [], [], 0
for _ in range(600):
    sub = rng.sample(POOL, rng.choice([3, 7, 8, 12, 20, 40, 80]))
    lu, old = E.best_lineup(sub), greedy_legacy(sub)
    p_new, p_old = E.team_power(lu)[0], E.team_power(old)[0]
    gains.append((p_new - p_old) / p_old * 100)
    mis_new.append(hors_poste(lu))
    mis_old.append(hors_poste(old))
    if len(sub) >= 7 and any(lu[s] is None for s in SLOTS):
        troues += 1
check("jamais plus faible que l'ancien glouton", min(gains) >= -1e-9,
      "pire cas %+.2f %%" % min(gains))
check("aucun poste vide avec >= 7 cartes", troues == 0, "%d compos trouees" % troues)
print("       gain de puissance : moyenne %+.1f %% | mediane %+.1f %% | max %+.1f %%"
      % (statistics.mean(gains), statistics.median(gains), max(gains)))
print("       joueurs hors poste : %.2f/7 -> %.2f/7"
      % (statistics.mean(mis_old), statistics.mean(mis_new)))

print("\n=== 7. Determinisme (l'ordre de lecture de la collection ne compte pas) ===")
stable = True
for _ in range(60):
    sub = rng.sample(POOL, 15)
    ref = [(s, (E.best_lineup(sub)[s] or {}).get("id")) for s in SLOTS]
    for _ in range(5):
        melange = sub[:]
        rng.shuffle(melange)
        if [(s, (E.best_lineup(melange)[s] or {}).get("id")) for s in SLOTS] != ref:
            stable = False
check("meme collection -> meme compo", stable)

print("\n" + ("TOUS LES TESTS PASSENT" if not FAILED else "ECHECS : %s" % FAILED))
sys.exit(1 if FAILED else 0)
