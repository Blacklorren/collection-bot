import os, sys, random
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import duel_engine as E

SLOTS = E.SLOTS

def card(rarete, club, poste):
    return {"rarete": rarete, "club": club, "poste": poste}

def lineup_same_rarity(rarete, same_club=False, placed=True):
    lu = {}
    for i, s in enumerate(SLOTS):
        club = "FC" if same_club else f"Club{i}"
        poste = s if placed else "GB"  # placed=False -> mauvais poste partout sauf GB
        lu[s] = card(rarete, club, poste)
    return lu

def lineup_mixed_same_club(rarities):
    lu = {}
    for s, r in zip(SLOTS, rarities):
        lu[s] = card(r, "FC", s)
    return lu

def winrate(luA, luB, n=10000):
    pA, dA = E.team_power(luA)
    pB, dB = E.team_power(luB)
    wa = wb = draw = 0
    sa_tot = sb_tot = 0
    rng = random.Random(42)
    for _ in range(n):
        s1, s2 = E.resolve_duel(pA, pB, allow_draw=False, rng=rng)
        sa_tot += s1; sb_tot += s2
        if s1 > s2: wa += 1
        elif s2 > s1: wb += 1
        else: draw += 1
    return pA, pB, wa/n*100, wb/n*100, sa_tot/n, sb_tot/n, dA, dB

def show(title, luA, luB):
    pA, pB, wa, wb, avgA, avgB, dA, dB = winrate(luA, luB)
    print(f"\n### {title}")
    print(f"  A: puissance {pA:.1f}  (syn x{dA['synergy']}, club max {dA['max_club_group']})")
    print(f"  B: puissance {pB:.1f}  (syn x{dB['synergy']}, club max {dB['max_club_group']})")
    print(f"  Score moyen ~ {avgA:.1f} - {avgB:.1f}")
    print(f"  WIN  A {wa:.1f}%   B {wb:.1f}%")
    return wa, wb

print("=== VALIDATION EQUILIBRAGE DUELS ===")

# 1) Dream team de légendaires depareillees VS 1 star + full club mixte
A = lineup_same_rarity("Légendaire", same_club=False)
B = lineup_mixed_same_club(["Légendaire", "Épique", "Épique", "Rare", "Rare", "Peu Commun", "Peu Commun"])
wa1, wb1 = show("7 Legendaires (clubs differents)  VS  1 Leg + mixte MEME CLUB", A, B)
assert 55 <= wa1 <= 75, f"A devrait etre favori mais battable: {wa1}"
assert wb1 >= 25, f"B doit avoir une vraie chance: {wb1}"

# 2) Full Epique meme club VS legendaires depareillees -> le collectif doit primer
D = lineup_same_rarity("Épique", same_club=True)
wd, wa2 = show("7 Epiques MEME CLUB  VS  7 Legendaires clubs differents", D, A)
assert wd > 55, f"Le full-club doit battre les stars eparpillees: {wd}"

# 3) Equipes identiques -> ~50/50
E1 = lineup_same_rarity("Rare", same_club=False)
E2 = lineup_same_rarity("Rare", same_club=False)
we1, we2 = show("Deux equipes identiques", E1, E2)
assert 45 <= we1 <= 55, f"Devrait etre ~50/50: {we1}"

# 4) Dream team VS equipe de communes depareillees -> rouste
C = lineup_same_rarity("Commun", same_club=False)
wa4, wc4 = show("7 Legendaires  VS  7 Communes", A, C)
assert wa4 >= 95, f"Devrait etre une rouste: {wa4}"

# 5) Bonus de poste : meme equipe bien placee VS mal placee
P_ok = lineup_same_rarity("Rare", same_club=False, placed=True)
P_ko = lineup_same_rarity("Rare", same_club=False, placed=False)
wok, wko = show("Rares BIEN places  VS  Rares MAL places (memes cartes)", P_ok, P_ko)
assert wok > 60, f"Bien placer doit avantager: {wok}"

# --- ELO ---
print("\n=== ELO ===")
n1, n2 = E.elo_apply(1000, 1000, 1.0)
print(f"  1000 bat 1000 -> {n1} / {n2}")
assert n1 == 1016 and n2 == 984
n1, n2 = E.elo_apply(1000, 1300, 1.0)  # outsider gagne
print(f"  1000 bat 1300 -> {n1} / {n2}")
assert n1 > 1025
assert E.within_band(1000, 1100) and not E.within_band(1000, 1200)
print(f"  Recompense: battre +200 Elo = {E.duel_reward(1000,1200)} pts ; ecraser -300 = {E.duel_reward(1300,1000)} pts")

print("\nTOUS LES TESTS DUEL PASSENT")
