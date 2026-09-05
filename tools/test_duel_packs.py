# -*- coding: utf-8 -*-
"""Test hors-ligne de la distribution quotidienne de packs de duel (Saison 2).

discord.py n'est pas installe dans l'environnement de dev : impossible d'importer
cogs/duel_cog.py. On EXTRAIT donc du source (ast) les fonctions a tester et on les
execute telles quelles -- le test porte sur le VRAI code, pas sur une copie qui
divergerait silencieusement au prochain reequilibrage.

    py -3 tools/test_duel_packs.py
"""
import ast
import asyncio
import io
import os
import sys
import tempfile
from datetime import datetime, date, timedelta

import pytz

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# La console Windows est en cp1252 : sans ca, afficher une fleche du texte de jeu
# fait tomber le test sur un UnicodeEncodeError qui n'a rien a voir avec le jeu.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# --- Base temporaire : surtout NE PAS toucher a la vraie /data/collection.db ---
import database
TMPDIR = tempfile.mkdtemp(prefix="duelpacks_")
database.DATA_DIR = TMPDIR
database.DB_NAME = os.path.join(TMPDIR, "test.db")
database.initialize_database()

import duel_engine as E

PARIS = pytz.timezone("Europe/Paris")


# --------------------------------------------------------------------------
# Extraction du vrai code du cog
# --------------------------------------------------------------------------
def load_from_cog(*names):
    """Compile les fonctions nommees, prises telles quelles dans duel_cog.py."""
    path = os.path.join(ROOT, "cogs", "duel_cog.py")
    with io.open(path, encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename=path)
    wanted = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names:
            wanted[node.name] = node
    missing = set(names) - set(wanted)
    if missing:
        raise SystemExit("Introuvable dans duel_cog.py : %s" % ", ".join(sorted(missing)))

    ns = {"datetime": datetime, "date": date, "timedelta": timedelta,
          "pytz": pytz, "PARIS": PARIS, "database": database, "E": E,
          "DAILY_MATCH_CAP": 6}
    for name in names:
        node = wanted[name]
        node.decorator_list = []          # @staticmethod n'a pas de sens hors classe
        mod = ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[]))
        exec(compile(mod, "<duel_cog:%s>" % name, "exec"), ns)
    return ns


COG = load_from_cog("_midnight_utc", "_day_bounds", "_settle_day", "_daily_progress_text",
                    "playable_count", "missing_slots")
_day_bounds = COG["_day_bounds"]
_settle_day = COG["_settle_day"]
_daily_progress_text = COG["_daily_progress_text"]


class FakeCog(object):
    """Juste ce dont _settle_day a besoin : de quoi enregistrer les MP envoyes."""

    def __init__(self):
        self.notifs = []

    async def _notify_daily_packs(self, user_id, jour, victoires, packs):
        self.notifs.append((user_id, jour, victoires, packs))


# --------------------------------------------------------------------------
# Jeu de donnees
# --------------------------------------------------------------------------
def add_duel(att, dfd, gagnant, quand_paris, classe=1):
    """Insere un duel a une heure PARIS precise (created_at est stocke en UTC)."""
    utc = PARIS.localize(quand_paris).astimezone(pytz.utc).strftime("%Y-%m-%d %H:%M:%S")
    with database._connect() as con:
        con.execute(
            "INSERT INTO duels (created_at, joueur1, joueur2, score1, score2, gagnant,"
            " classe, elo1_before, elo2_before, elo1_after, elo2_after, lineup1, lineup2)"
            " VALUES (?, ?, ?, 30, 25, ?, ?, 1000, 1000, 1000, 1000, ?, ?)",
            (utc, att, dfd, gagnant, classe, "{}", "{}"))


def packs_de(uid):
    return database.get_user_data(uid)["packs"]


HIER = datetime.now(PARIS).date() - timedelta(days=1)
AUJ = HIER + timedelta(days=1)


def H(h, m=0):
    return datetime(HIER.year, HIER.month, HIER.day, h, m)


def AU(h, m=0):
    return datetime(AUJ.year, AUJ.month, AUJ.day, h, m)


P1, P2, P3, P4, P5, P6, P7, P8, P9 = 101, 102, 103, 104, 105, 106, 107, 108, 109
# Six sacs de frappe distincts : le bareme compte des ADVERSAIRES, plus des matchs.
C = [901, 902, 903, 904, 905, 906]
JOUEURS = (P1, P2, P3, P4, P5, P6, P7, P8, P9) + tuple(C)

# P1 : 6 matchs, 4 adversaires distincts battus, 2 perdus -> 1 pack
for i in range(4):
    add_duel(P1, C[i], P1, H(10, i))
for i in (4, 5):
    add_duel(P1, C[i], C[i], H(11, i))

# P2 : 6 adversaires distincts battus -> 2 packs
for i in range(6):
    add_duel(P2, C[i], P2, H(12, i))

# P3 : 2 adversaires en classe -> 0 pack. Le 3e est battu en AMICAL (classe=0) :
# il ne doit pas faire basculer le palier.
add_duel(P3, C[0], P3, H(13, 0))
add_duel(P3, C[1], P3, H(13, 1))
add_duel(P3, C[2], P3, H(13, 2), classe=0)

# P4 : ne fait que DEFENDRE, et gagne toutes ses defenses -> 0 pack.
for i in range(5):
    add_duel(C[0], P4, P4, H(14, i))

# P5 : 3 adversaires battus a 23 h 45, le meme jour -> 1 pack (borne haute)
for i in range(3):
    add_duel(P5, C[i], P5, H(23, 45 + i))

# P6 : 3 adversaires battus a 00 h 15 AUJOURD'HUI -> rien pour hier
for i in range(3):
    add_duel(P6, C[i], P6, AU(0, 15 + i))

# P7 : LE FARMEUR. 6 victoires brutes, mais sur 2 cibles seulement (3 fois
# chacune) -> 2 adversaires distincts -> 0 pack. C'est tout l'objet de la regle :
# sous l'ancien decompte il aurait empoche 2 packs.
for i in range(3):
    add_duel(P7, C[0], P7, H(15, i))
    add_duel(P7, C[1], P7, H(16, i))

# P8 : la REVANCHE. Il perd contre C[0], revient et gagne, puis bat C[1] et C[2].
# 3 adversaires distincts -> 1 pack : reperdre une premiere manche ne doit pas
# condamner la journee.
add_duel(P8, C[0], C[0], H(17, 0))
add_duel(P8, C[0], P8, H(17, 1))
add_duel(P8, C[1], P8, H(17, 2))
add_duel(P8, C[2], P8, H(17, 3))

# P9 : LE MATCH DE MARGE. 6 matchs, une defaite, 5 adversaires distincts battus
# -> 2 packs. C'est la raison d'etre du palier a 5 et non 6 : une seule mauvaise
# rencontre ne doit pas condamner la journee des le premier match.
add_duel(P9, C[5], C[5], H(18, 0))
for i in range(5):
    add_duel(P9, C[i], P9, H(18, i + 1))

for uid in JOUEURS:
    database.check_user(uid)
AVANT = {uid: packs_de(uid) for uid in JOUEURS}


# --------------------------------------------------------------------------
# 1. Distribution
# --------------------------------------------------------------------------
print("=== DISTRIBUTION DU %s ===" % HIER)
cog = FakeCog()
asyncio.run(_settle_day(cog, HIER))
gagne = {uid: packs_de(uid) - AVANT[uid] for uid in AVANT}
for uid in sorted(gagne):
    print("  joueur %s : +%d pack(s)" % (uid, gagne[uid]))

assert gagne[P1] == 1, "4 adversaires battus sur 6 = 1 pack, obtenu %s" % gagne[P1]
assert gagne[P2] == 2, "6 adversaires battus = 2 packs, obtenu %s" % gagne[P2]
assert gagne[P9] == 2, \
    "5 adversaires distincts malgre une defaite = 2 packs, obtenu %s" % gagne[P9]
assert gagne[P3] == 0, "2 adversaires en classe = 0 pack (l'amical ne compte pas)"
assert gagne[P4] == 0, "les defenses gagnees ne rapportent aucun pack"
assert gagne[P5] == 1, "23 h 45 est encore dans la journee"
assert gagne[P6] == 0, "00 h 15 appartient a la journee SUIVANTE"
assert gagne[P7] == 0, \
    "ANTI-FARM : 6 victoires sur 2 cibles = 2 adversaires = 0 pack, obtenu %s" % gagne[P7]
assert gagne[P8] == 1, "la revanche compte : 3 adversaires distincts = 1 pack"
for uid in C:
    assert gagne[uid] == 0, "un sac de frappe n'attaque jamais"
assert sorted(n[0] for n in cog.notifs) == [P1, P2, P5, P8, P9], \
    "un MP par joueur paye, pas plus"

# Le registre doit tracer ce qui a ete paye, et rien pour les autres.
JOUR = HIER.isoformat()
for uid, battus, packs in ((P1, 4, 1), (P2, 6, 2), (P5, 3, 1), (P8, 3, 1), (P9, 5, 2)):
    ligne = database.get_duel_daily_reward(uid, JOUR)
    assert ligne is not None, "aucune ligne de registre pour %s" % uid
    assert (ligne["victoires"], ligne["packs"]) == (battus, packs), \
        "registre faux pour %s : %s" % (uid, ligne)
for uid in (P3, P4, P6, P7) + tuple(C):
    assert database.get_duel_daily_reward(uid, JOUR) is None, \
        "%s n-a rien merite, il ne doit pas avoir de ligne" % uid
print("  registre duel_daily_rewards conforme  OK")

# Le compteur brut ne doit pas etre confondu avec le compteur qui paie.
DEBUT, FIN = _day_bounds(HIER)
assert database.count_ranked_attacks_for(P7, DEBUT) == 6, "P7 a bien joue ses 6 matchs"
assert database.count_beaten_opponents_for(P7, DEBUT, FIN) == 2, \
    "P7 a 6 victoires brutes mais 2 adversaires distincts"
assert database.count_ranked_attacks_between(P7, C[0], DEBUT, wins_only=True) == 3, \
    "wins_only doit compter les 3 victoires de P7 contre C[0]"
assert database.count_ranked_attacks_between(P8, C[0], DEBUT, wins_only=True) == 1, \
    "P8 a perdu puis gagne contre C[0] : une seule victoire"
print("  COUNT DISTINCT vs victoires brutes  OK")

# --------------------------------------------------------------------------
# 2. Idempotence : relancer la boucle ne paie pas deux fois
# --------------------------------------------------------------------------
cog2 = FakeCog()
asyncio.run(_settle_day(cog2, HIER))
asyncio.run(_settle_day(cog2, HIER))
for uid in AVANT:
    assert packs_de(uid) - AVANT[uid] == gagne[uid], "double paiement sur %s" % uid
assert cog2.notifs == [], "aucun MP ne doit repartir sur une journee deja soldee"
print("  idempotence : 2 relances -> 0 pack de plus, 0 MP  OK")


# --------------------------------------------------------------------------
# 3. Frontieres de journee, changement d'heure compris
# --------------------------------------------------------------------------
def duree_h(jour):
    debut, fin = _day_bounds(jour)
    fmt = "%Y-%m-%d %H:%M:%S"
    return (datetime.strptime(fin, fmt) - datetime.strptime(debut, fmt)).total_seconds() / 3600


print("\n=== FRONTIERES DE JOURNEE ===")
for jour, attendu, libelle in ((date(2026, 3, 29), 23, "passage a l'heure d'ete"),
                               (date(2026, 10, 25), 25, "retour a l'heure d'hiver"),
                               (date(2026, 7, 14), 24, "journee ordinaire")):
    mesure = duree_h(jour)
    print("  %s : %g h  (%s)" % (jour, mesure, libelle))
    assert mesure == attendu, "%s devrait durer %d h, mesure %g" % (jour, attendu, mesure)

_d1, f1 = _day_bounds(HIER)
d2, _f2 = _day_bounds(HIER + timedelta(days=1))
assert f1 == d2, "les journees doivent se toucher sans trou ni recouvrement"
print("  jours jointifs (fin exclue == debut suivant)  OK")

# --------------------------------------------------------------------------
# 4. Texte de progression
# --------------------------------------------------------------------------
print("\n=== PROGRESSION AFFICHEE ===")
for matchs, battus in ((1, 1), (3, 2), (4, 3), (4, 4), (5, 3), (5, 5), (6, 5), (6, 2)):
    txt = _daily_progress_text(matchs, battus).replace("\n", " | ")
    print("  %d matchs / %d battus -> %s" % (matchs, battus, txt))

assert "0 pack" in _daily_progress_text(1, 1)
assert "1 pack" in _daily_progress_text(4, 3)
assert "2 packs" in _daily_progress_text(5, 5)
# 4 adversaires en 4 matchs : il reste 2 matchs pour en trouver 1 de plus.
assert "Encore **1 adversaire" in _daily_progress_text(4, 4)
# 3 adversaires en 5 matchs : le dernier match ne peut pas en donner les 2 qui
# manquent -> on annonce le palier hors d'atteinte plutot que d'entretenir l'espoir.
assert "plus atteignable" in _daily_progress_text(5, 3)
# Palier haut atteint alors qu'il reste un match : celui-ci ne joue plus que l'Elo.
assert "Palier maximum" in _daily_progress_text(5, 5)
# Journee pleine : plus de "matchs restants" dans le texte, le bilan est fige.
assert "definitif" in _daily_progress_text(6, 5).replace("é", "e")
assert "definitif" in _daily_progress_text(6, 2).replace("é", "e")
# Le farmeur : 6 matchs joues, 2 adversaires, rien.
assert "**2 adversaires battus** en 6/6 matchs" in _daily_progress_text(6, 2)
assert "**0 pack**" in _daily_progress_text(6, 2)


# --------------------------------------------------------------------------
# 5. Ticket d'entree : 7 cartes DISTINCTES pour etre defiable (et pour defier)
# --------------------------------------------------------------------------
class FakeRoster(object):
    """Juste ce dont playable_count / missing_slots ont besoin.

    `injouables` simule les cartes hors saison (ou Noel) : possedees, mais pas
    alignables. Le compte doit les ignorer comme le fait cog.jouable().
    """

    def __init__(self, injouables=()):
        self.injouables = set(injouables)

    def get_card(self, cid):
        return {"id": cid}

    def jouable(self, card):
        return bool(card) and card["id"] not in self.injouables


# missing_slots appelle self.playable_count : on greffe la vraie methode du cog.
FakeRoster.playable_count = COG["playable_count"]


def set_collection(user_id, card_ids):
    database.check_user(user_id)
    with database._connect() as con:
        con.execute("DELETE FROM user_cards WHERE user_id = ?", (user_id,))
        con.executemany("INSERT INTO user_cards (user_id, card_id) VALUES (?, ?)",
                        [(user_id, cid) for cid in card_ids])


print("\n=== TICKET D'ENTREE (7 postes) ===")
roster = FakeRoster()
N = len(E.SLOTS)

CAS = [
    ("collection vide",              201, [],                        (),        0, N),
    ("3 cartes",                     202, [1, 2, 3],                 (),        3, N - 3),
    ("6 cartes : il en manque une",  203, list(range(1, 7)),         (),        6, 1),
    ("7 cartes pile",                204, list(range(1, 8)),         (),        7, 0),
    ("12 cartes",                    205, list(range(1, 13)),        (),       12, 0),
    # Le piege : 21 lignes en base, mais seulement 3 cartes differentes.
    ("3 cartes en 7 exemplaires",    206, [1, 2, 3] * 7,             (),        3, N - 3),
    # 8 possedees dont 2 hors saison -> 6 alignables, equipe incomplete.
    ("8 dont 2 hors saison",         207, list(range(1, 9)),     (7, 8),        6, 1),
]
for libelle, uid, cartes, injouables, attendu_count, attendu_manque in CAS:
    set_collection(uid, cartes)
    r = FakeRoster(injouables)
    count = r.playable_count(uid)
    manque = COG["missing_slots"](r, uid)
    etat = "defiable" if manque == 0 else "protege (manque %d)" % manque
    print("  %-28s %2d lignes -> %2d cartes -> %s" % (libelle, len(cartes), count, etat))
    assert count == attendu_count, "%s : %d cartes attendues, %d obtenues" % (
        libelle, attendu_count, count)
    assert manque == attendu_manque, "%s : manque %d attendu, %d obtenu" % (
        libelle, attendu_manque, manque)

print("\nTOUS LES TESTS PACKS PASSENT")
