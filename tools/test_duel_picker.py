# -*- coding: utf-8 -*-
"""Test hors-ligne du PICKER de composition (Saison 2).

Ce qui est verifie : le menu « qui joue ici ? » classe les cartes par ce qu'elles
RAPPORTENT vraiment a l'equipe, synergie comprise, et le montant affiche tient
compte du poste que la carte laisse vide en partant. C'est tout l'interet du
picker depuis qu'il n'y a plus d'etape « club » : le meilleur choix doit etre en
premiere ligne, sinon on a juste deplace le probleme.

Verifie aussi le repere « compo optimale » : depuis que best_lineup() rend
l'optimum exact, aucun reglage manuel ne peut faire mieux -- l'ecart affiche doit
donc etre nul sur la compo optimale, et positif des qu'on la degrade.

discord.py n'est pas installe en dev : on EXTRAIT le vrai code du cog par `ast`,
comme tools/test_duel_publication.py.

    py -3 tools/test_duel_picker.py
"""
import ast
import io
import json
import os
import sys

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


# --------------------------------------------------------------------------
# Faux discord : de quoi faire tourner les constructeurs d'options et d'embed
# --------------------------------------------------------------------------
class FakeOption(object):
    def __init__(self, label=None, value=None, description=None, emoji=None, default=False):
        self.label = label
        self.value = value
        self.description = description
        self.emoji = emoji
        self.default = default


class FakeEmbed(object):
    def __init__(self, title=None, description=None, color=None):
        self.title = title
        self.description = description
        self.fields = []
        self.footer_text = None

    def add_field(self, name=None, value=None, inline=False):
        self.fields.append((name, value))
        return self

    def set_footer(self, text=None):
        self.footer_text = text
        return self


class FakeColor(object):
    gold = staticmethod(lambda: "gold")
    blurple = staticmethod(lambda: "blurple")
    green = staticmethod(lambda: "green")
    red = staticmethod(lambda: "red")
    greyple = staticmethod(lambda: "greyple")
    blue = staticmethod(lambda: "blue")


class FakeDiscord(object):
    Embed = FakeEmbed
    Color = FakeColor
    SelectOption = FakeOption


class FauxSelect(object):
    """Un menu deroulant reduit a ce que le picker ecrit dedans."""

    def __init__(self):
        self.options = []
        self.disabled = False
        self.placeholder = ""


# --------------------------------------------------------------------------
# Extraction du vrai code du cog
# --------------------------------------------------------------------------
def charge(classe, methodes=(), classes_entieres=()):
    path = os.path.join(ROOT, "cogs", "duel_cog.py")
    with io.open(path, encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename=path)
    ns = {"E": E, "discord": FakeDiscord, "RARITY_EMOJI": RARITY_EMOJI}
    trouves = set()
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name in classes_entieres:
            node.bases = []
            for sub in list(node.body):
                # on ne garde que ce qui ne depend pas de discord.ui
                if isinstance(sub, ast.FunctionDef):
                    sub.decorator_list = []
            mod = ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[]))
            exec(compile(mod, "<duel_cog>", "exec"), ns)
            trouves.add(node.name)
        elif isinstance(node, ast.ClassDef) and node.name == classe:
            for sub in node.body:
                if isinstance(sub, ast.FunctionDef) and sub.name in methodes:
                    sub.decorator_list = []
                    mod = ast.fix_missing_locations(ast.Module(body=[sub], type_ignores=[]))
                    exec(compile(mod, "<duel_cog>", "exec"), ns)
                    trouves.add(sub.name)
    manque = (set(methodes) | set(classes_entieres)) - trouves
    if manque:
        raise SystemExit("Introuvable dans duel_cog.py : %s" % ", ".join(sorted(manque)))
    return ns


COG = charge("LineupPicker",
             methodes=("lineup", "_placed_slots", "_with_card", "_candidates",
                       "_refresh_components", "_embed", "_owned_cards"),
             classes_entieres=("DuelSession",))
DuelSession = COG["DuelSession"]


class Picker(object):
    """Le vrai code du picker, pose sur un objet minimal."""

    def __init__(self, cartes, lineup, defense, best=None, slot=E.SLOTS[0]):
        self.s = DuelSession(attacker=None, defender=None, ranked=True,
                             lineup_a=lineup, lineup_d=defense, lineup_best=best)
        self.current_slot = slot
        self._cards = list(cartes)
        self.slot_select = FauxSelect()
        self.card_select = FauxSelect()

    for _nom in ("lineup", "_placed_slots", "_with_card", "_candidates",
                 "_refresh_components", "_embed", "_owned_cards"):
        locals()[_nom] = COG[_nom]
    del _nom


def carte(cid, rarete, poste, club="Club%d"):
    return {"id": cid, "nom": "J%s" % cid, "rarete": rarete,
            "poste": E.SLOT_LABELS[poste] if poste else None,
            "club": club if "%" not in club else club % cid}


def vide():
    return {s: None for s in E.SLOTS}


print("\n=== 1. Le meilleur choix est en premiere ligne ===")
# Trois gardiens de raretes differentes + du remplissage ailleurs.
cartes = [carte(1, "Peu Commun", "GB"), carte(2, "Légendaire", "GB"),
          carte(3, "Rare", "GB"), carte(4, "Épique", "ALG")]
p = Picker(cartes, vide(), vide(), slot="GB")
cands = p._candidates("GB")
ordre = [c["nom"] for c, _ in cands]
gains = {c["nom"]: round(g) for c, g in cands}
verifie("le meilleur choix est en premiere ligne", ordre[0] == "J2", ordre)
verifie("les gains sont strictement decroissants",
        all(a[1] >= b[1] for a, b in zip(cands, cands[1:])), gains)
verifie("les trois gardiens sont ordonnes par rarete",
        gains["J2"] > gains["J3"] > gains["J1"] > 0, gains)
# Le cas interessant, et c'est bien le classement voulu : un Epique HORS poste
# (note 12) rapporte plus qu'un Rare a SON poste (8 x 1,4 = 11,2). L'ancien menu
# rangeait par club et ne montrait aucun chiffre : ce compromis etait invisible.
verifie("un Epique hors poste passe devant un Rare a son poste",
        ordre.index("J4") < ordre.index("J3") and gains["J4"] > gains["J3"], gains)

print("\n=== 2. Le gain tient compte du poste laisse vide ===")
# J5 est ailier gauche ET deja aligne en ALG : le mettre dans les buts vide ALG.
lu = vide()
j5 = carte(5, "Légendaire", "ALG")
lu["ALG"] = j5
p = Picker([j5, carte(6, "Rare", "GB")], lu, vide(), slot="GB")
essai = p._with_card(j5, "GB")
verifie("la carte deplacee libere son ancien poste",
        essai["ALG"] is None and essai["GB"] is j5)
gain_j5 = dict((c["nom"], g) for c, g in p._candidates("GB"))["J5"]
attendu = E.team_power(essai)[0] - E.team_power(lu)[0]
verifie("le gain annonce est la vraie difference de puissance",
        abs(gain_j5 - attendu) < 1e-9, "%.2f vs %.2f" % (gain_j5, attendu))
verifie("deplacer le Legendaire hors de son poste est perdant", round(gain_j5) < 0,
        round(gain_j5))

print("\n=== 3. Ce que le menu ECRIT ===")
p._refresh_components()
desc = {o.label: o.description for o in p.card_select.options}
verifie("la carte deja alignee ailleurs annonce le poste qu'elle quitte",
        "quitte ALG" in desc["J5"], desc["J5"])
# J5 est ailier gauche : propose dans les buts, il est « hors poste ».
# J6 est gardien : lui y est chez lui.
verifie("un ailier propose dans les buts est dit hors poste",
        "hors poste" in desc["J5"], desc["J5"])
verifie("le gardien est dit a son poste", "à son poste" in desc["J6"], desc["J6"])
verifie("le signe moins est le meme que sur la feuille de match (U+2212)",
        "−6" in desc["J5"] and "-6" not in desc["J5"], desc["J5"])
verifie("le menu annonce le poste en clair",
        p.card_select.placeholder == "2️⃣ Qui joue gardien ?", p.card_select.placeholder)
verifie("le selecteur de poste montre qui occupe chaque poste",
        {o.value: o.description for o in p.slot_select.options}["ALG"] == "J5",
        {o.value: o.description for o in p.slot_select.options})

lu2 = vide()
lu2["GB"] = carte(7, "Rare", "GB")
p2 = Picker([lu2["GB"], carte(8, "Commun", "GB")], lu2, vide(), slot="GB")
p2._refresh_components()
opts = {o.label: o for o in p2.card_select.options}
verifie("la carte en place est marquee « en place », pas « +0 »",
        "en place" in opts["J7"].description and opts["J7"].default, opts["J7"].description)

print("\n=== 4. Le plafond des 25 options de Discord ===")
beaucoup = [carte(100 + i, "Commun", "GB") for i in range(40)]
lu3 = vide()
lu3["GB"] = beaucoup[-1]          # la carte en place est la PIRE du tri
p3 = Picker(beaucoup, lu3, vide(), slot="GB")
cands = p3._candidates("GB")
verifie("jamais plus de 25 entrees", len(cands) <= 25, len(cands))
verifie("la carte en place survit a la coupe",
        any(c["id"] == beaucoup[-1]["id"] for c, _ in cands))
p3._refresh_components()
verifie("le menu rendu respecte aussi la limite", len(p3.card_select.options) <= 25)

print("\n=== 5. Collection vide ===")
p4 = Picker([], vide(), vide(), slot="GB")
p4._refresh_components()
verifie("menu desactive et non vide", p4.card_select.disabled
        and len(p4.card_select.options) == 1, p4.card_select.options[0].label)

print("\n=== 6. Le repere « compo optimale » ===")
CARDS = json.load(open(os.path.join(ROOT, "cards.json"), encoding="utf-8"))
SAISON = max(c.get("saison", 1) for c in CARDS)
POOL = [c for c in CARDS if c.get("saison") == SAISON and c.get("rarete") != "Noël"]
import random

rng = random.Random(11)
sub = rng.sample(POOL, 20)
best = E.best_lineup(sub)
s_ok = DuelSession(None, None, True, lineup_a=dict(best), lineup_d=vide(), lineup_best=best)
verifie("aucun ecart quand l'optimale est deja en place", s_ok.best_gain() == 0,
        s_ok.best_gain())

degrade = dict(best)
degrade["GB"] = None
s_ko = DuelSession(None, None, True, lineup_a=degrade, lineup_d=vide(), lineup_best=best)
verifie("un poste vide creuse un ecart positif", s_ko.best_gain() > 0, s_ko.best_gain())
verifie("l'ecart est la difference des valeurs ARRONDIES",
        s_ko.best_gain() == round(E.team_power(best)[0]) - round(E.team_power(degrade)[0]),
        s_ko.best_gain())
s_sans = DuelSession(None, None, True, lineup_a=dict(best), lineup_d=vide())
verifie("pas d'optimale calculee -> pas d'ecart, pas de crash",
        s_sans.best_power() is None and s_sans.best_gain() == 0)

print("\n=== 7. L'embed du picker ===")
p5 = Picker(sub, degrade, vide(), best=best, slot="GB")
e = p5._embed()
champs = dict(e.fields)
verifie("l'embed chiffre la compo optimale",
        any("optimale" in n.lower() for n in champs), list(champs))
verifie("le pied de page parle en pourcentage, pas en multiplicateur",
        "+40 %" in e.footer_text and "×" not in e.footer_text, e.footer_text)
p6 = Picker(sub, dict(best), vide(), best=best, slot="GB")
verifie("pas de champ « optimale » quand il n'y a rien a gagner",
        not any("optimale" in n.lower() for n in dict(p6._embed().fields)))

print("\n" + ("TOUS LES TESTS PICKER PASSENT" if not ECHECS else "ECHECS : %s" % ECHECS))
sys.exit(1 if ECHECS else 0)
