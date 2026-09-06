"""
Moteur de duel (Saison 2) — logique PURE, sans Discord ni base de données.

Le duel est ASYMÉTRIQUE : un seul joueur est connecté, l'attaquant. Le défenseur
est représenté par sa compo automatique : il ne met rien en jeu, et ne gagne
rien non plus — voir `elo_apply_attacker` et `packs_for_wins`.

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

# La forme du jour est le SEUL facteur invisible du match : elle est tirée une fois
# par équipe, avant les 50 possessions, et donne le ton de la rencontre entière.
# Sans elle à l'écran, un joueur lit « puissance 71 contre 49 », perd 28-34, et
# conclut que le bot est cassé. Les paliers ci-dessous existent pour lui donner un
# MOT, pas seulement un nombre — un « grand jour » tombe une fois sur dix environ
# (mesuré : 10,6 / 23,3 / 32,2 / 23,3 / 10,6 %), donc le mot garde son poids.
# Seuils décroissants, comme DAILY_PACK_LADDER : on renvoie le premier atteint.
FORM_BANDS = (
    (1.15, "🔥", "Grand jour"),
    (1.05, "😃", "En jambes"),
    (0.95, "😐", "Normale"),
    (0.85, "😕", "Jour moyen"),
    (0.00, "🥶", "Jour sans"),
)

# --- ELO ---
ELO_START = 1000
ELO_K = 32
ELO_BAND = 150            # au-delà : duel classé « hors bande » (configurable côté cog)
ELO_K_SOFT = 8            # K réduit pour un classé hors bande (bande douce)

# --- RÉCOMPENSES : DES PACKS, EN FIN DE JOURNÉE ---
# Un match ne rapporte QUE de l'Elo. Aucun point n'est crédité pendant le duel :
# les packs sont distribués une fois par jour, par PALIERS, selon le nombre de
# VICTOIRES EN ATTAQUE de la journée (cf packs_for_wins).
#
# ⚠️ « Une victoire » = UN ADVERSAIRE DISTINCT battu. Rebattre la même personne
# dans la journée ne compte qu'une fois (`database.count_beaten_opponents_for`,
# COUNT DISTINCT). C'est l'anti-farm : le palier haut exige cinq adversaires
# différents, ce que deux collections faibles ne peuvent pas fournir. Sans ça, le
# chemin le plus sûr vers les 2 packs était de matraquer les trois débutants du
# serveur — à puissance moitié moindre, l'attaquant gagne 99,7 % du temps.
#
# Pourquoi des paliers plutôt qu'un gain par match : un gain par match se cumulait
# aux points de messages et laissait entrevoir ~10 packs/jour. Un palier borne le
# revenu quotidien par construction — 2 packs, quoi qu'il arrive — et rend la
# journée lisible : « il me manque une victoire », pas « il me manque 47 points ».
#
# Calibré sur le plafond de 6 attaques classées/jour (DUEL_DAILY_MATCH_CAP, côté
# cog) : 3 adversaires = la moitié de sa journée, 5 = le palier haut.
#
# 5 sur 6 et non 6 sur 6 : ça laisse exactement UN match de marge. On peut perdre
# une fois et décrocher quand même les deux packs, ou dépenser ce match en
# revanche sur la cible qui nous a battu (auquel cas elle finit dans les cinq).
# Exiger le sans-faute rendait le palier haut hostile — une seule mauvaise
# rencontre condamnait la journée entière dès le premier match.
DAILY_PACK_LADDER = ((5, 2), (3, 1))   # (adversaires distincts battus, packs) — décroissant

# --- DUEL ASYMÉTRIQUE (le défenseur n'est pas connecté) ---
# Le défenseur ne met RIEN en jeu et ne gagne RIEN : son Elo ne bouge pas, sa
# défense ne lui rapporte aucun pack. Elle PROTÈGE son classement, elle ne le fait
# pas monter. Rester hors ligne n'est donc pas une source de revenus — c'était le
# risque de l'ancien DEFENSE_HOLD_POINTS, qui payait le sommeil.


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
    raw_total = 0.0
    poste_ok = 0
    club_counts = {}
    for slot in SLOTS:
        card = lineup.get(slot)
        if not card:
            total += EMPTY_SLOT_NOTE
            raw_total += EMPTY_SLOT_NOTE
            continue
        total += card_note(card, slot)
        # `raw_total` = la même somme SANS le bonus de poste. Seul intérêt : pouvoir
        # afficher ce que les postes bien tenus ont rapporté. `base_total` le contient
        # déjà, donc une feuille de match qui partirait de `base_total` présenterait
        # un premier terme dans lequel un facteur est déjà caché.
        raw_total += BASE_NOTE.get(card.get("rarete"), EMPTY_SLOT_NOTE)
        if normalize_poste(card.get("poste")) == slot:
            poste_ok += 1
        club = card.get("club")
        if club:
            club_counts[club] = club_counts.get(club, 0) + 1
    max_group = max(club_counts.values()) if club_counts else 1
    synergy = SYNERGY.get(min(max_group, 7), 1.0)
    power = total * synergy
    return power, {"base_total": round(total, 1), "synergy": synergy,
                   "max_club_group": max_group, "raw_total": round(raw_total, 1),
                   "poste_ok": poste_ok}


def _convs(power1, power2, rng):
    """Taux de réussite des deux équipes, après application de la forme du jour."""
    f1 = _clamp(rng.gauss(FORM_MEAN, FORM_STD), FORM_MIN, FORM_MAX)
    f2 = _clamp(rng.gauss(FORM_MEAN, FORM_STD), FORM_MIN, FORM_MAX)
    p1, p2 = power1 * f1, power2 * f2
    total = p1 + p2 or 1.0
    c1 = _clamp(BASE_CONV * 2 * p1 / total, CONV_MIN, CONV_MAX)
    c2 = _clamp(BASE_CONV * 2 * p2 / total, CONV_MIN, CONV_MAX)
    return c1, c2, f1, f2


def simulate_match(power1, power2, allow_draw=False, rng=None):
    """Simule un match possession par possession.
    Retourne (score1, score2, half, overtime, formes) : `half` = (s1, s2) à la
    mi-temps, `overtime` = True si départagé en mort subite, `formes` = (f1, f2),
    les deux formes du jour tirées pour ce match.

    Les formes SORTENT du moteur parce qu'elles s'affichent sur la feuille de
    match : c'est le seul facteur que le joueur ne pouvait pas voir, et donc le
    seul qui faisait passer un résultat légitime pour un bug."""
    rng = rng or random
    c1, c2, f1, f2 = _convs(power1, power2, rng)
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
    return s1, s2, half, overtime, (f1, f2)


def resolve_duel(power1, power2, allow_draw=False, rng=None):
    """Simule un match. Retourne (score1, score2).
    Si `allow_draw` est False, départage en mort subite (style 7 mètres)."""
    s1, s2 = simulate_match(power1, power2, allow_draw=allow_draw, rng=rng)[:2]
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


def elo_apply_attacker(elo_att, elo_def, result_att, k=ELO_K):
    """Duel ASYMÉTRIQUE : seul l'attaquant met son Elo en jeu.

    Le défenseur n'a pas choisi ce match et n'était pas là pour le jouer : son Elo
    ne bouge pas. L'espérance reste calculée contre son Elo, donc écraser un faible
    ne rapporte quasiment rien et se faire sortir par lui coûte cher — le garde-fou
    anti-farm est dans le barème lui-même, pas seulement dans les plafonds.

    result_att : 1.0 victoire, 0.5 nul, 0.0 défaite. Retourne le nouvel Elo attaquant.
    """
    e = elo_expected(elo_att, elo_def)
    return round(elo_att + k * (result_att - e))


def within_band(elo_a, elo_b, band=ELO_BAND):
    """True si les deux joueurs sont assez proches pour un duel classé."""
    return abs(elo_a - elo_b) <= band


def packs_for_wins(wins):
    """Packs mérités pour `wins` ADVERSAIRES DISTINCTS battus dans la journée.

    Paliers, pas cumul : 5 adversaires valent 2 packs, pas 2 + 1. Le tableau est
    trié par exigence décroissante, on renvoie le premier palier atteint.
    """
    for seuil, packs in DAILY_PACK_LADDER:
        if wins >= seuil:
            return packs
    return 0


def ladder_text():
    """Le barème en une ligne : « 3 adversaires = 1 pack · 5 adversaires = 2 packs ».

    Rendu depuis DAILY_PACK_LADDER, jamais réécrit à la main : un rééquilibrage ne
    doit pas laisser derrière lui des textes d'aide qui annoncent l'ancien barème.
    """
    return " · ".join(
        "%d adversaires = %d pack%s" % (seuil, packs, "s" if packs > 1 else "")
        for seuil, packs in sorted(DAILY_PACK_LADDER))


def next_pack_tier(wins):
    """(adversaires_manquants, packs_du_palier) pour le prochain palier, ou None si
    le joueur est déjà au sommet. Sert à afficher « encore 1 adversaire → 2 packs »."""
    for seuil, packs in sorted(DAILY_PACK_LADDER):
        if wins < seuil:
            return seuil - wins, packs
    return None



# --- MISE EN MOTS POUR LA FEUILLE DE MATCH ---
# Le moteur raisonne en multiplicateurs (×1.12), la feuille de match parle en
# pourcentages (+12 %) : un coefficient se lit comme une ligne de barème, un
# pourcentage comme un bonus gagné. La conversion vit ICI et pas dans le cog,
# pour que le vocabulaire reste testable sans Discord.


def pct_text(ratio):
    """Un multiplicateur interne en pourcentage lisible : 1.12 → « +12 % ».

    Renvoie '' quand l'arrondi tombe sur zéro : « +0 % » occupe une ligne pour ne
    rien dire. Le signe moins est le vrai (U+2212) et pas un trait d'union — en
    police proportionnelle, « -11 % » se lit comme un tiret d'énumération."""
    pct = round((ratio - 1.0) * 100)
    if pct == 0:
        return ""
    return "%s%d %%" % ("+" if pct > 0 else "−", abs(pct))


def form_text(forme):
    """La forme du jour en mots + pourcentage : « 🔥 Grand jour (+20 %) ».

    Le mot porte l'information rare/banal, le pourcentage porte l'ampleur. Seul,
    un « +20 % » ne dit pas si c'est un coup de chance ou l'ordinaire."""
    for seuil, emoji, label in FORM_BANDS:
        if forme >= seuil:
            pct = pct_text(forme)
            return "%s %s (%s)" % (emoji, label, pct) if pct else "%s %s" % (emoji, label)
    return ""


# --- COMPOSITION AUTOMATIQUE ---
#
# ⚠️ Remplir « la meilleure carte restante, poste par poste » est un piège, et
# c'est ce que faisait la v1. L'écart de rareté (3 → 16) est plus grand que le
# bonus de poste (×1,4) : un Légendaire ailier pèse 16 dans les buts, contre
# 11,2 pour un Rare gardien à SON poste. Le premier slot servi emportait donc la
# meilleure carte quel que soit son poste, le titulaire était évincé, et de
# proche en proche toute la compo glissait. Mesuré sur 600 collections tirées de
# cards.json : 3,8 postes sur 7 mal occupés et ~13 % de puissance perdue — d'où
# les remontées « ma défense joue des gens hors de leur poste ».
#
# `best_lineup` maximise donc directement ce qui décide du match — `team_power`,
# SYNERGIE DE CLUB COMPRISE — au lieu d'une approximation poste par poste.
# Optimiser la seule somme des notes ne suffisait pas : elle pouvait défaire un
# groupe de club que le glouton avait formé par accident, et rendre une défense
# plus faible qu'avant le correctif (mesuré jusqu'à −6 %).
#
# Sept slots : l'optimum EXACT est calculable. Décomposition, pour un club C
# donné et un ensemble de postes tenus par des joueurs de C :
#   - placer au mieux les cartes de C sur ces postes,
#   - placer au mieux les AUTRES cartes sur les postes restants,
# les deux moitiés étant indépendantes une fois le partage fixé. On balaie les
# 2^7 partages pour chaque club, et la synergie se lit sur le nombre de postes
# tenus par C. Le vrai optimum a forcément un club majoritaire : il est donc
# atteint par l'un de ces balayages.

_LINEUP_MASKS = 1 << len(SLOTS)
_NEG = float("-inf")


def _lineup_order_key(card):
    """Ordre de parcours stable : cartes fortes d'abord, puis id.

    Uniquement pour départager deux compos de puissance IDENTIQUE. Sans lui, la
    défense d'un joueur pouvait changer d'un match à l'autre au gré de l'ordre de
    lecture de sa collection — illisible pour lui, et impossible à reproduire en
    debug."""
    return (-BASE_NOTE.get(card.get("rarete"), EMPTY_SLOT_NOTE),
            str(card.get("id", "")), str(card.get("nom", "")))


def _assign_dp(indexes, notes, allow_empty):
    """DP sur les 2^7 sous-ensembles de postes. Retourne (val, asg).

    `val[mask]` = meilleur total pour les postes de `mask`, `asg[mask]` = le
    tuple ((slot_index, card_index), …) qui l'atteint.

    allow_empty=False : `mask` est tenu EXACTEMENT par des cartes — indispensable
    pour que le nombre de postes du masque soit la taille du groupe de club.
    allow_empty=True  : un poste de `mask` peut rester vide (EMPTY_SLOT_NOTE).

    L'affectation est stockée en entier plutôt qu'en pointeur vers l'état parent :
    un parent peut être amélioré APRÈS avoir servi, et remonter la chaîne à la fin
    aligne alors une carte déjà utilisée sur deux postes.
    """
    n = len(SLOTS)
    val = [_NEG] * _LINEUP_MASKS
    asg = [None] * _LINEUP_MASKS
    val[0], asg[0] = 0.0, ()
    for ci in indexes:
        row = notes[ci]
        # Masques décroissants : on écrit toujours vers un masque plus grand, donc
        # déjà dépassé — c'est ce qui interdit de réutiliser la carte `ci` deux fois.
        for mask in range(_LINEUP_MASKS - 1, -1, -1):
            base = val[mask]
            if base == _NEG:
                continue
            for si in range(n):
                bit = 1 << si
                if mask & bit:
                    continue
                cand = base + row[si]
                if cand > val[mask | bit]:
                    val[mask | bit] = cand
                    asg[mask | bit] = asg[mask] + ((si, ci),)
    if allow_empty:
        # Un poste laissé vide vaut EMPTY_SLOT_NOTE. Comparaison STRICTE : à valeur
        # égale — une Commune hors poste vaut exactement un poste vide — on garde la
        # carte. Une feuille de match trouée se lit comme un bug, à puissance égale.
        for si in range(n):
            bit = 1 << si
            for mask in range(_LINEUP_MASKS):
                if not mask & bit or val[mask ^ bit] == _NEG:
                    continue
                cand = val[mask ^ bit] + EMPTY_SLOT_NOTE
                if cand > val[mask]:
                    val[mask] = cand
                    asg[mask] = asg[mask ^ bit]
    return val, asg


def best_lineup(cards):
    """Meilleure feuille de match possible avec `cards` : {slot: card | None}.

    Maximise `team_power` exactement (cf. le commentaire de section ci-dessus).
    Les cartes sont supposées déjà filtrées (saison en cours, dédoublonnées) : ce
    module ne sait rien des collections."""
    n = len(SLOTS)
    cards = sorted((c for c in cards if c), key=_lineup_order_key)
    lineup = {s: None for s in SLOTS}
    if not cards:
        return lineup

    notes = [[card_note(c, s) for s in SLOTS] for c in cards]

    def prune(indexes):
        """Les 7 meilleures cartes par poste suffisent à contenir un optimum : au
        plus 7 cartes sont alignées, donc pour n'importe quel poste l'une de ses
        sept meilleures est forcément libre, et vaut au moins autant que celle
        qu'on y aurait mise. Sans cet élagage, une collection complète ferait
        tourner la DP sur 250 cartes au lieu d'une cinquantaine."""
        keep = set()
        for si in range(n):
            keep.update(sorted(indexes, key=lambda ci: (-notes[ci][si], ci))[:n])
        return sorted(keep)

    by_club = {}
    for ci, card in enumerate(cards):
        club = card.get("club") or None
        by_club.setdefault(club, []).append(ci)

    everyone = list(range(len(cards)))
    full_mask = _LINEUP_MASKS - 1
    best_power, best = -1.0, None
    # `None` en tête = aucune contrainte de club. Ce passage garantit qu'on ne
    # rend jamais pire que l'optimum de somme, y compris pour une collection sans
    # club renseigné, où aucun passage par club n'existerait.
    for club in [None] + sorted(c for c in by_club if c):
        club_idx = by_club[club] if club else []
        # `set(...)` hors de la comprehension : dans le test, il serait reconstruit
        # a chaque carte (quadratique sur une grosse collection, x16 clubs).
        in_club = set(club_idx)
        other_idx = [ci for ci in everyone if ci not in in_club] if club else everyone
        a_val, a_asg = _assign_dp(prune(club_idx), notes, allow_empty=False)
        b_val, b_asg = _assign_dp(prune(other_idx), notes, allow_empty=True)
        for mask in range(_LINEUP_MASKS):
            if a_val[mask] == _NEG:
                continue
            rest = full_mask ^ mask
            if b_val[rest] == _NEG:
                continue
            group = bin(mask).count("1")
            # SYNERGY[group] minore la vraie synergie quand un AUTRE club est plus
            # nombreux du côté `b` — ce cas-là est couvert par le passage de ce
            # club, donc le maximum global reste l'optimum exact.
            power = (a_val[mask] + b_val[rest]) * SYNERGY.get(min(max(group, 1), 7), 1.0)
            if power > best_power:
                best_power, best = power, a_asg[mask] + b_asg[rest]

    for si, ci in best:
        lineup[SLOTS[si]] = cards[ci]
    return lineup
