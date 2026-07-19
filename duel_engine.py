"""
Moteur de duel (Saison 2) — logique PURE, sans Discord ni base de données.

Concept : chaque joueur aligne 7 cartes sur les 7 postes du handball.
La puissance d'équipe dépend de la rareté des cartes, d'un bonus si chaque
carte est à SON poste, et d'une synergie si plusieurs cartes partagent le
même club. Le match est simulé possession par possession avec une variance
« forme du jour » pour laisser sa chance à l'outsider.

Toutes les constantes d'équilibrage sont en haut : ré-équilibrage facile
après les premiers vrais matchs, sans toucher à la logique.

Validé par simulation Monte-Carlo (voir tools/_test_duel.py).
"""
import math
import random

# --- POSTES (= les 7 slots d'une feuille de match) ---
SLOTS = ["GB", "ALG", "ARG", "DC", "PIV", "ARD", "ALD"]
SLOT_LABELS = {
    "GB": "Gardien", "ALG": "Ailier gauche", "ARG": "Arrière gauche",
    "DC": "Demi-centre", "PIV": "Pivot", "ARD": "Arrière droit", "ALD": "Ailier droit",
}

# cards.json stocke les postes en toutes lettres ("Gardien", "Demi Centre"…).
# On normalise vers les codes de slot pour appliquer le bonus de poste.
POSTE_ALIASES = {
    "gardien": "GB", "gb": "GB",
    "ailier gauche": "ALG", "alg": "ALG",
    "arriere gauche": "ARG", "arg": "ARG",
    "demi centre": "DC", "dc": "DC",
    "pivot": "PIV", "piv": "PIV",
    "arriere droit": "ARD", "ard": "ARD",
    "ailier droit": "ALD", "ald": "ALD",
}


def _norm(text):
    if not text:
        return ""
    text = text.strip().lower()
    for a, b in (("è", "e"), ("é", "e"), ("ê", "e"), ("ë", "e"), ("à", "a"), ("-", " ")):
        text = text.replace(a, b)
    return " ".join(text.split())


def normalize_poste(poste):
    """Convertit un poste (toutes lettres ou code) en code de slot, '' si inconnu/vide."""
    return POSTE_ALIASES.get(_norm(poste), "")

# --- NOTES PAR RARETÉ (échelle compressée : Lég ≈ 5× Commun) ---
BASE_NOTE = {
    "Commun": 3, "Peu Commun": 5, "Rare": 8, "Épique": 12, "Légendaire": 16,
    "Noël": 7,  # normalement non jouable, valeur de secours
}
EMPTY_SLOT_NOTE = BASE_NOTE["Commun"]   # slot vide = niveau Commune, sans club ni bonus
POSTE_BONUS = 1.4                        # carte alignée à SON poste

# --- SYNERGIE DE CLUB (sur le plus gros groupe de même club aligné) ---
SYNERGY = {1: 1.00, 2: 1.05, 3: 1.12, 4: 1.20, 5: 1.30, 6: 1.42, 7: 1.55}

# --- SIMULATION DU MATCH ---
POSSESSIONS = 50
BASE_CONV = 0.55          # taux de réussite à puissances égales
CONV_MIN, CONV_MAX = 0.30, 0.85
FORM_MEAN, FORM_STD = 1.0, 0.12
FORM_MIN, FORM_MAX = 0.7, 1.3
OVERTIME_CAP = 200        # garde-fou de la mort subite

# --- ELO ---
ELO_START = 1000
ELO_K = 32
ELO_BAND = 150            # au-delà : duel classé « hors bande » (configurable côté cog)
ELO_K_SOFT = 8            # K réduit pour un classé hors bande (bande douce)
SOFT_REWARD_FACTOR = 0.5  # récompenses réduites hors bande

# --- RÉCOMPENSES (classé uniquement) ---
DUEL_WIN_POINTS = 100     # base ; scalé par l'écart d'Elo
DUEL_LOSS_POINTS = 20     # lot de consolation


def _clamp(x, lo, hi):
    return lo if x < lo else hi if x > hi else x


def card_note(card, slot):
    """Note d'une carte alignée sur `slot` (bonus si c'est son poste naturel)."""
    base = BASE_NOTE.get(card.get("rarete"), EMPTY_SLOT_NOTE)
    bonus = POSTE_BONUS if normalize_poste(card.get("poste")) == slot else 1.0
    return base * bonus


def team_power(lineup):
    """Puissance d'une équipe.
    `lineup` : dict {slot: card_dict | None}. Une carte = dict avec
    au moins 'rarete', 'poste', 'club'. Slot absent ou None = slot vide.
    Retourne (puissance, details)."""
    total = 0.0
    club_counts = {}
    for slot in SLOTS:
        card = lineup.get(slot)
        if not card:
            total += EMPTY_SLOT_NOTE
            continue
        total += card_note(card, slot)
        club = card.get("club")
        if club:
            club_counts[club] = club_counts.get(club, 0) + 1
    max_group = max(club_counts.values()) if club_counts else 1
    synergy = SYNERGY.get(min(max_group, 7), 1.0)
    power = total * synergy
    return power, {"base_total": round(total, 1), "synergy": synergy, "max_club_group": max_group}


def _convs(power1, power2, rng):
    """Taux de réussite des deux équipes, après application de la forme du jour."""
    f1 = _clamp(rng.gauss(FORM_MEAN, FORM_STD), FORM_MIN, FORM_MAX)
    f2 = _clamp(rng.gauss(FORM_MEAN, FORM_STD), FORM_MIN, FORM_MAX)
    p1, p2 = power1 * f1, power2 * f2
    total = p1 + p2 or 1.0
    c1 = _clamp(BASE_CONV * 2 * p1 / total, CONV_MIN, CONV_MAX)
    c2 = _clamp(BASE_CONV * 2 * p2 / total, CONV_MIN, CONV_MAX)
    return c1, c2


def simulate_match(power1, power2, allow_draw=False, rng=None):
    """Simule un match possession par possession.
    Retourne (score1, score2, half, overtime) : `half` = (s1, s2) à la mi-temps,
    `overtime` = True si le match a été départagé en mort subite."""
    rng = rng or random
    c1, c2 = _convs(power1, power2, rng)
    s1 = s2 = 0
    half = (0, 0)
    for i in range(POSSESSIONS):
        if rng.random() < c1:
            s1 += 1
        if rng.random() < c2:
            s2 += 1
        if i + 1 == POSSESSIONS // 2:
            half = (s1, s2)

    overtime = False
    if s1 == s2 and not allow_draw:
        overtime = True
        for _ in range(OVERTIME_CAP):
            h = rng.random() < c1
            a = rng.random() < c2
            if h and not a:
                s1 += 1
                break
            if a and not h:
                s2 += 1
                break
        else:
            # extrêmement rare : on tranche par la puissance brute
            if power1 >= power2:
                s1 += 1
            else:
                s2 += 1
    return s1, s2, half, overtime


def resolve_duel(power1, power2, allow_draw=False, rng=None):
    """Simule un match. Retourne (score1, score2).
    Si `allow_draw` est False, départage en mort subite (style 7 mètres)."""
    s1, s2, _, _ = simulate_match(power1, power2, allow_draw=allow_draw, rng=rng)
    return s1, s2


# --- ELO ---

def elo_expected(elo_a, elo_b):
    return 1.0 / (1.0 + 10 ** ((elo_b - elo_a) / 400.0))


def elo_apply(elo1, elo2, result1, k=ELO_K):
    """result1 : 1.0 si j1 gagne, 0.5 nul, 0.0 défaite. Retourne (new1, new2)."""
    e1 = elo_expected(elo1, elo2)
    new1 = round(elo1 + k * (result1 - e1))
    new2 = round(elo2 + k * ((1.0 - result1) - (1.0 - e1)))
    return new1, new2


def within_band(elo_a, elo_b, band=ELO_BAND):
    """True si les deux joueurs sont assez proches pour un duel classé."""
    return abs(elo_a - elo_b) <= band


def duel_reward(winner_elo, loser_elo, base=DUEL_WIN_POINTS):
    """Récompense scalée : battre plus fort rapporte plus, écraser un faible rapporte peu."""
    factor = _clamp(1 + (loser_elo - winner_elo) / 400.0, 0.25, 2.0)
    return round(base * factor)
