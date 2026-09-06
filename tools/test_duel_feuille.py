# -*- coding: utf-8 -*-
"""Test hors-ligne de la FEUILLE DE MATCH (colonne d'une equipe).

Ce qui est verifie : la colonne se lit comme un CALCUL descendant -- valeur des
cartes, puis chaque bonus en POURCENTAGE, puis la puissance en resultat. Plus
aucun multiplicateur a l'ecran (un « x1.12 » se lit comme une ligne de bareme
interne, un « +12 % » comme un bonus gagne), et la ligne de forme du jour
disparait proprement quand il n'y a pas de match derriere la compo.

discord.py n'est pas installe en dev : on EXTRAIT le vrai `_team_summary` du
source par `ast`, comme tools/test_duel_publication.py.

    py -3 tools/test_duel_feuille.py
"""
import ast
import io
import os
import re
import sqlite3
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import duel_engine as E

RARITY_EMOJI = {"Commun": "⬜", "Peu Commun": "🟩", "Rare": "🟦",
                "Épique": "🟪", "Légendaire": "🟨"}
ECHECS = []


def verifie(nom, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + nom + ((" | " + str(detail)) if detail else ""))
    if not cond:
        ECHECS.append(nom)


def charge_methode(nom):
    """Sort une methode de DuelCog du source, sans importer discord.py."""
    path = os.path.join(ROOT, "cogs", "duel_cog.py")
    with io.open(path, encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename=path)
    ns = {"E": E, "RARITY_EMOJI": RARITY_EMOJI}
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "DuelCog":
            for sub in node.body:
                if isinstance(sub, ast.FunctionDef) and sub.name == nom:
                    sub.decorator_list = []
                    mod = ast.fix_missing_locations(ast.Module(body=[sub], type_ignores=[]))
                    exec(compile(mod, "<duel_cog>", "exec"), ns)
                    return ns[nom]
    raise SystemExit("%s introuvable dans duel_cog.py" % nom)


_team_summary = charge_methode("_team_summary")


def compo(specs):
    """specs : (rarete, poste_naturel ou None, club) dans l'ordre des SLOTS."""
    lu = {}
    for slot, (rarete, poste, club) in zip(E.SLOTS, specs):
        lu[slot] = {"id": slot, "nom": "%s-%s" % (slot, rarete[:3]), "rarete": rarete,
                    "poste": E.SLOT_LABELS[poste] if poste else "Gardien", "club": club}
    return lu


def rendu(lineup, score=None, forme=None):
    _, det = E.team_power(lineup)
    return _team_summary(None, lineup, det, score, forme)


def entetes(col, n=5):
    return [l.split(" :")[0].lstrip("*") for l in col.split("\n")[:n]]


print("\n=== 1. Rendu complet : le match 71 vs 49 de la capture ===")
# Attaquant : 7 Rares a leur poste, 2 du meme club. Defenseur : 5 hors poste.
att = compo([("Rare", s, "Nantes" if i < 2 else "Club%d" % i)
             for i, s in enumerate(E.SLOTS)])
dfs = compo([("Rare", s if i < 2 else None, "Club%d" % i)
             for i, s in enumerate(E.SLOTS)])
col_a = rendu(att, 28, 1.016)
col_d = rendu(dfs, 34, 1.201)
for titre, col in (("ATTAQUANT", col_a), ("DEFENSEUR", col_d)):
    print("\n  " + titre)
    for ligne in col.split("\n"):
        print("    " + ligne)

print("\n=== 2. Ce que la colonne doit contenir (et ne plus contenir) ===")
verifie("aucun multiplicateur a l'ecran", "×" not in col_a and "×" not in col_d)
verifie("le mot d'implementation « max » a disparu", "max " not in col_a)
entete_a = col_a.split("\n")[:col_a.split("\n").index("")]
verifie("la puissance est la DERNIERE ligne avant la compo",
        entete_a[-1].startswith("**Puissance sur le terrain"), entete_a[-1])
verifie("ordre : valeur -> postes -> synergie -> forme -> puissance",
        entetes(col_a) == ["Score", "Valeur des cartes", "Postes respectés",
                           "Synergie club", "Forme du jour"],
        " / ".join(entetes(col_a)))
verifie("la forme du jour est nommee ET chiffree", "🔥 Grand jour (+20 %)" in col_d)
verifie("les 7 postes sont toujours listes",
        all(("`%s`" % s) in col_a for s in E.SLOTS))

print("\n=== 3. La chaine de calcul se verifie a la main ===")
_, det = E.team_power(dfs)
raw = det["raw_total"]
attendu = raw * (det["base_total"] / raw) * det["synergy"] * 1.201
affiche = int(re.search(r"Puissance sur le terrain : (\d+)", col_d).group(1))
verifie("la puissance affichee est le produit de la chaine",
        abs(affiche - round(attendu)) <= 1,
        "affiche %d, calcul %.1f" % (affiche, attendu))
verifie("les postes tenus sont comptes juste", "Postes respectés : 2 sur 7" in col_d,
        [l for l in col_d.split("\n") if "Postes" in l])

print("\n=== 4. Sans match derriere : /ma_defense et vieux duels ===")
col = rendu(att, None, None)
verifie("pas de ligne de forme", "Forme du jour" not in col)
verifie("pas de ligne de score", "Score" not in col)
entete = col.split("\n")[:col.split("\n").index("")]
verifie("la puissance redevient celle de la compo au repos",
        entete[-1].startswith("**Puissance :"), entete[-1])
vieux = rendu(att, 28, None)   # duel joue avant le stockage de la forme
verifie("un vieux duel garde son score sans inventer de forme",
        "Score" in vieux and "Forme du jour" not in vieux)

print("\n=== 5. Cas degeneres ===")
vide = rendu({s: None for s in E.SLOTS}, 12, 0.82)
verifie("compo entierement vide : pas de division par zero", "Puissance" in vide)
verifie("0 poste tenu s'affiche sans « +0 % »",
        "Postes respectés : 0 sur 7" in vide and "+0 %" not in vide,
        [l for l in vide.split("\n") if "Postes" in l])
sans_syn = compo([("Rare", s, "Club%d" % i) for i, s in enumerate(E.SLOTS)])
neutre = rendu(sans_syn, 20, 1.0)
verifie("aucune synergie -> « aucune », pas « 1 joueurs »",
        "Synergie club : aucune" in neutre,
        [l for l in neutre.split("\n") if "Synergie" in l])
verifie("forme pile a 1.00 : le mot reste, le pourcentage saute",
        "Forme du jour : 😐 Normale" in neutre and "Normale (" not in neutre)

print("\n=== 6. Migration de la base : colonnes forme1/forme2 ===")
import database

tmp = os.path.join(tempfile.mkdtemp(), "collection.db")
# Base a l'ANCIEN schema, puis on laisse init_db la rattraper.
con = sqlite3.connect(tmp)
con.execute("""CREATE TABLE duels (id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, joueur1 INTEGER NOT NULL,
    joueur2 INTEGER NOT NULL, score1 INTEGER NOT NULL, score2 INTEGER NOT NULL,
    gagnant INTEGER, classe INTEGER NOT NULL DEFAULT 1, elo1_before INTEGER,
    elo2_before INTEGER, elo1_after INTEGER, elo2_after INTEGER,
    lineup1 TEXT, lineup2 TEXT)""")
con.execute("INSERT INTO duels (joueur1, joueur2, score1, score2) VALUES (1, 2, 30, 25)")
con.commit()
con.close()

database.DB_NAME = tmp
database.initialize_database()
con = sqlite3.connect(tmp)
con.row_factory = sqlite3.Row
cols = {r[1] for r in con.execute("PRAGMA table_info(duels)")}
verifie("colonnes ajoutees sur une base existante", {"forme1", "forme2"} <= cols)
ancien = dict(con.execute("SELECT * FROM duels WHERE id = 1").fetchone())
verifie("le duel deja en base survit, forme a NULL",
        ancien["score1"] == 30 and ancien["forme1"] is None)
con.close()

database.record_duel(1, 2, 31, 24, 1, True, 1000, 1000, 1016, 1000,
                     {"GB": 5}, {"GB": 6}, 1.016, 1.201)
d = database.get_duel(2)
verifie("un nouveau duel stocke bien les deux formes",
        abs(d["forme1"] - 1.016) < 1e-9 and abs(d["forme2"] - 1.201) < 1e-9,
        "%s / %s" % (d["forme1"], d["forme2"]))
verifie("la feuille rejouee retrouve le meme mot",
        E.form_text(d["forme2"]) == "🔥 Grand jour (+20 %)", E.form_text(d["forme2"]))

print("\n" + ("TOUS LES TESTS FEUILLE PASSENT" if not ECHECS else "ECHECS : %s" % ECHECS))
sys.exit(1 if ECHECS else 0)
