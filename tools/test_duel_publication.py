# -*- coding: utf-8 -*-
"""Test hors-ligne de la PUBLICATION des resultats de duel (Saison 2).

Ce qui est verifie ici, c'est le couplage FRAGILE du dispositif : le resume public
inscrit le numero de match dans le pied de son embed, et le bouton « Feuille de
match » le RELIT dans ce pied pour retrouver le duel en base. Les deux bouts sont
a cinquante lignes d'ecart et rien ne les relie a la compilation -- changer le
libelle du pied casserait tous les boutons deja publies, en silence. Ce test est
la pour que ca ne passe pas.

discord.py n'est pas installe dans l'environnement de dev : on EXTRAIT donc du
source (ast) le vrai code du cog, comme tools/test_duel_packs.py, et on lui donne
un faux `discord` minimal.

    py -3 tools/test_duel_publication.py
"""
import ast
import io
import json
import os
import re
import sys
from datetime import datetime

import pytz

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# La console Windows est en cp1252 : sans ca, afficher une fleche du texte de jeu
# fait tomber le test sur un UnicodeEncodeError qui n'a rien a voir avec le jeu.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import duel_engine as E

PARIS = pytz.timezone("Europe/Paris")


# --------------------------------------------------------------------------
# Faux discord : juste de quoi faire tourner les constructeurs d'embed
# --------------------------------------------------------------------------
class FakeEmbed(object):
    def __init__(self, title=None, description=None, color=None):
        self.title = title
        self.description = description
        self.color = color
        self.footer_text = None

    def set_footer(self, text=None):
        self.footer_text = text
        return self


class FakeColor(object):
    greyple = staticmethod(lambda: "greyple")
    gold = staticmethod(lambda: "gold")
    blue = staticmethod(lambda: "blue")


class FakeDiscord(object):
    Embed = FakeEmbed
    Color = FakeColor


class FakeMember(object):
    def __init__(self, uid, nom):
        self.id = uid
        self.display_name = nom


# --------------------------------------------------------------------------
# Extraction du vrai code du cog
# --------------------------------------------------------------------------
def load_from_cog(funcs=(), consts=()):
    """Compile fonctions et constantes nommees, prises telles quelles dans duel_cog.py.

    Les constantes comptent autant que les fonctions ici : _MATCH_RE est la moitie
    du contrat teste."""
    path = os.path.join(ROOT, "cogs", "duel_cog.py")
    with io.open(path, encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename=path)

    ns = {"datetime": datetime, "pytz": pytz, "PARIS": PARIS, "E": E,
          "json": json, "re": re, "os": os, "discord": FakeDiscord,
          "RARITY_EMOJI": {"Rare": "R", "Legendaire": "L"}}

    trouves = set()
    for node in tree.body:                       # niveau module uniquement
        if isinstance(node, ast.Assign):
            noms = [t.id for t in node.targets if isinstance(t, ast.Name)]
            if not any(n in consts for n in noms):
                continue
            trouves.update(n for n in noms if n in consts)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name not in funcs:
                continue
            node.decorator_list = []
            trouves.add(node.name)
        else:
            continue
        mod = ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[]))
        exec(compile(mod, "<duel_cog>", "exec"), ns)

    # Les methodes de DuelCog sont imbriquees dans la classe : second passage.
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "DuelCog":
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)) and sub.name in funcs:
                    sub.decorator_list = []
                    trouves.add(sub.name)
                    mod = ast.fix_missing_locations(ast.Module(body=[sub], type_ignores=[]))
                    exec(compile(mod, "<duel_cog>", "exec"), ns)

    manque = (set(funcs) | set(consts)) - trouves
    if manque:
        raise SystemExit("Introuvable dans duel_cog.py : %s" % ", ".join(sorted(manque)))
    return ns


COG = load_from_cog(funcs=("_thread_name_for", "_compact_result_embed", "_lineup_from_ids",
                           "_env_channel_id"),
                    consts=("_JOURS", "_MOIS", "_MATCH_RE"))
_env_channel_id = COG["_env_channel_id"]
_thread_name_for = COG["_thread_name_for"]
_compact_result_embed = COG["_compact_result_embed"]
_lineup_from_ids = COG["_lineup_from_ids"]
_MATCH_RE = COG["_MATCH_RE"]
_JOURS = COG["_JOURS"]

ECHECS = []


def verifie(nom, condition, detail=""):
    if condition:
        print("  ok   %s" % nom)
    else:
        print("  FAIL %s %s" % (nom, detail))
        ECHECS.append(nom)


# --------------------------------------------------------------------------
# 1. Le nom du fil du jour
# --------------------------------------------------------------------------
def test_nom_du_fil():
    print("\n=== NOM DU FIL QUOTIDIEN ===")
    matin = _thread_name_for(datetime(2026, 9, 5, 8, 30))
    soir = _thread_name_for(datetime(2026, 9, 5, 23, 59))
    demain = _thread_name_for(datetime(2026, 9, 6, 0, 1))
    print("  %s" % matin)

    # Le nom est la CLE du cache : instable, il ferait un fil par match.
    verifie("stable sur la journee", matin == soir, "%r != %r" % (matin, soir))
    verifie("change de jour", matin != demain)
    verifie("jour en francais (samedi)", "samedi" in matin, matin)
    verifie("mois en francais (septembre)", "septembre" in matin, matin)
    verifie("1er janvier = jeudi", "jeudi 1 janvier" in _thread_name_for(datetime(2026, 1, 1)))
    verifie("3 aout = lundi", "lundi 3 août" in _thread_name_for(datetime(2026, 8, 3)))
    verifie("aucun jour anglais", not any(en in matin for en in
                                          ("Monday", "Friday", "Saturday", "Sunday")))
    # Discord refuse les noms de fil au-dela de 100 caracteres.
    plus_long = max(len(_thread_name_for(datetime(2026, m, 22))) for m in range(1, 13))
    verifie("<= 100 caracteres", plus_long <= 100, "max %d" % plus_long)


# --------------------------------------------------------------------------
# 2. Le contrat pied-de-page <-> bouton
# --------------------------------------------------------------------------
def compact(**kw):
    args = dict(duel_id=42, a=FakeMember(1, "Omeyer"), d=FakeMember(2, "Flof"),
                s_a=34, s_d=30, winner=1, overtime=False, ranked=True,
                elo_a0=1000, elo_a1=1016, mvp={"nom": "Luc STEINS"}, soft=False)
    args.update(kw)
    return _compact_result_embed(None, **args)


def test_numero_de_match():
    print("\n=== N° DE MATCH : ECRIT PAR L'EMBED, RELU PAR LE BOUTON ===")
    e = compact(duel_id=1234)
    print("  pied : %r" % e.footer_text)
    trouve = _MATCH_RE.search(e.footer_text or "")
    verifie("le pied porte le n°", trouve is not None, repr(e.footer_text))
    verifie("relu a l'identique", trouve and int(trouve.group(1)) == 1234)

    # Un entrainement n'est pas enregistre : pas de n°, donc pas de bouton.
    verifie("pas de n° sans duel_id", compact(duel_id=None).footer_text is None)
    verifie("un pied sans n° ne matche pas", _MATCH_RE.search("Match amical") is None)


# --------------------------------------------------------------------------
# 3. Le resume tient en deux lignes et dit le necessaire
# --------------------------------------------------------------------------
def test_resume_compact():
    print("\n=== RESUME PUBLIC ===")
    victoire = compact()
    print("  %s\n  %s" % (victoire.title, victoire.description))

    verifie("titre sur une ligne", "\n" not in victoire.title)
    verifie("description sur une ligne", "\n" not in victoire.description)
    # L'attaquant est TOUJOURS a gauche : c'est ce qui rend la colonne lisible.
    verifie("attaquant a gauche (victoire)", victoire.title.index("Omeyer") < victoire.title.index("Flof"))
    defaite = compact(winner=2, s_a=30, s_d=34, elo_a1=984)
    print("  %s\n  %s" % (defaite.title, defaite.description))
    verifie("attaquant a gauche (defaite)", defaite.title.index("Omeyer") < defaite.title.index("Flof"))
    verifie("defense qui tient = bouclier", defaite.title.startswith("🛡️"))
    verifie("victoire = epees", victoire.title.startswith("⚔️"))
    # 5 lignes plus bas, le 🏆 veut dire « classe ». Deux sens pour une icone,
    # empiles dans le meme resume, c'est illisible.
    verifie("pas de trophee dans le titre", "🏆" not in victoire.title)
    verifie("nul = poignee de main", compact(winner=None, s_d=34).title.startswith("🤝"))

    verifie("delta Elo signe +", "(+16)" in victoire.description, victoire.description)
    verifie("delta Elo signe -", "(-16)" in defaite.description, defaite.description)
    verifie("homme du match", "Luc STEINS" in victoire.description)

    amical = compact(ranked=False)
    verifie("amical sans Elo", "Elo" not in amical.description and "📊" not in amical.description,
            amical.description)
    verifie("amical annonce", "Amical" in amical.description)
    verifie("hors bande signale", "hors bande" in compact(soft=True).description)
    verifie("mort subite signalee", "mort subite" in compact(overtime=True).description)

    # 40 de ces resumes doivent tenir dans un fil sans le noyer.
    verifie("moins de 200 caracteres", len(victoire.title) + len(victoire.description) < 200,
            "%d" % (len(victoire.title) + len(victoire.description)))


# --------------------------------------------------------------------------
# 4. Relecture des compos stockees en base
# --------------------------------------------------------------------------
class FauxCog(object):
    CARTES = {"7": {"id": 7, "nom": "Luc STEINS", "rarete": "Rare", "club": "PSG", "poste": "Demi Centre"}}

    def get_card(self, cid):
        return self.CARTES.get(str(cid)) if cid is not None else None


def test_relecture_compo():
    print("\n=== COMPOS RELUES DEPUIS LA BASE ===")
    faux = FauxCog()
    depuis_json = _lineup_from_ids(faux, json.dumps({"GB": 7, "ALG": None}))
    verifie("tous les slots presents", set(depuis_json) == set(E.SLOTS))
    verifie("carte retrouvee", depuis_json["GB"] and depuis_json["GB"]["nom"] == "Luc STEINS")
    verifie("slot vide reste vide", depuis_json["ALG"] is None)
    verifie("slot absent = vide", depuis_json["PIV"] is None)

    verifie("dict accepte tel quel", _lineup_from_ids(faux, {"GB": 7})["GB"] is not None)
    verifie("None tolere", all(v is None for v in _lineup_from_ids(faux, None).values()))
    verifie("JSON casse tolere", all(v is None for v in _lineup_from_ids(faux, "{pas du json").values()))
    # Une carte retiree de cards.json ne doit pas faire exploser une vieille feuille.
    verifie("carte inconnue toleree", _lineup_from_ids(faux, {"GB": 99999})["GB"] is None)


# --------------------------------------------------------------------------
# 5. Les reglages d'environnement ne doivent JAMAIS tuer le demarrage
# --------------------------------------------------------------------------
def test_reglages_environnement():
    print("\n=== DUEL_CHANNEL_ID LU DEPUIS L'ENVIRONNEMENT ===")
    # Le cog est charge dans setup_hook sans try/except : une ValueError ici
    # empecherait le bot ENTIER de demarrer, pour un simple reglage de rangement.
    cas = [("", None), ("   ", None), ("123456789", 123456789),
           (" 123456789 ", 123456789), ('"123456789"', 123456789),
           ("#duels", None), ("abc", None), ("12.5", None)]
    for brut, attendu in cas:
        os.environ["TEST_DUEL_CHANNEL"] = brut
        obtenu = _env_channel_id("TEST_DUEL_CHANNEL")
        verifie("%-13r -> %s" % (brut, attendu), obtenu == attendu, "obtenu %r" % obtenu)
    os.environ.pop("TEST_DUEL_CHANNEL", None)
    verifie("variable absente  -> None", _env_channel_id("TEST_DUEL_ABSENTE") is None)


test_reglages_environnement()
test_nom_du_fil()
test_numero_de_match()
test_resume_compact()
test_relecture_compo()

print("")
if ECHECS:
    raise SystemExit("ECHECS : %s" % ", ".join(ECHECS))
print("TOUS LES TESTS PUBLICATION PASSENT")
