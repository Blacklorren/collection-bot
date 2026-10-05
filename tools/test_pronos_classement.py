# -*- coding: utf-8 -*-
"""Test hors-ligne du classement general pagine des pronostics (!cg).

Ce qui est verifie ici, c'est la promesse du message : des fleches qui repondent
TOUJOURS, a TOUT LE MONDE. Elle tient a un couplage fragile -- l'etat de la page
(numero, portee, competition) est ecrit dans le custom_id du bouton par _cg_view,
puis relu par on_interaction, sans rien en memoire entre les deux. Changer le
format du custom_id rendrait muettes toutes les fleches deja postees, en silence.

discord.py n'est pas installe dans l'environnement de dev : on EXTRAIT donc du
source (ast) le vrai code du cog, comme tools/test_duel_publication.py, et on lui
donne un faux `discord` minimal. La base, elle, est la vraie (sur un fichier
temporaire) : l'ordre des ex aequo se joue dans le SQL.

    py -3 tools/test_pronos_classement.py
"""
import ast
import asyncio
import io
import os
import re
import sys
import tempfile
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# La console Windows est en cp1252 : sans ca, afficher une medaille fait tomber
# le test sur un UnicodeEncodeError qui n'a rien a voir avec le classement.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# --- Base temporaire : surtout NE PAS toucher a la vraie collection.db ---
os.environ.pop("DEBUT_SAISON_PRONOS", None)      # la coupure par defaut, 2026-09-01
import database
TMPDIR = tempfile.mkdtemp(prefix="pronos_")
database.DATA_DIR = TMPDIR
database.DB_NAME = os.path.join(TMPDIR, "test.db")
database.initialize_database()


# --------------------------------------------------------------------------
# Faux discord : juste de quoi construire un embed et trois boutons
# --------------------------------------------------------------------------
class FakeEmbed(object):
    def __init__(self, title=None, description=None, color=None, timestamp=None):
        self.title = title
        self.description = description
        self.color = color
        self.fields = []

    def add_field(self, name=None, value=None, inline=True):
        self.fields.append((name, value))
        return self


class FakeColor(object):
    gold = staticmethod(lambda: "gold")
    blue = staticmethod(lambda: "blue")


class FakeButton(object):
    def __init__(self, emoji=None, label=None, style=None, disabled=False, custom_id=None):
        self.emoji = emoji
        self.label = label
        self.disabled = disabled
        self.custom_id = custom_id


class FakeView(object):
    def __init__(self, timeout=180):
        self.timeout = timeout
        self.children = []

    def add_item(self, item):
        self.children.append(item)


class FakeUI(object):
    View = FakeView
    Button = FakeButton


class FakeButtonStyle(object):
    grey = "grey"


class FakeInteractionType(object):
    component = "component"
    application_command = "application_command"


class FakeDiscord(object):
    Embed = FakeEmbed
    Color = FakeColor
    ui = FakeUI
    ButtonStyle = FakeButtonStyle
    InteractionType = FakeInteractionType


class FakeMember(object):
    def __init__(self, uid):
        self.id = uid
        self.display_name = "Joueur%d" % uid


PARTI = 100013      # a quitte le serveur : get_member ne le connait plus


class FakeGuild(object):
    def get_member(self, uid):
        return None if uid == PARTI else FakeMember(uid)


class FakeResponse(object):
    def __init__(self):
        self.edits = []
        self.messages = []

    async def edit_message(self, **kw):
        self.edits.append(kw)

    async def send_message(self, content=None, **kw):
        self.messages.append((content, kw))


class FakeInteraction(object):
    def __init__(self, custom_id, type_="component"):
        self.type = type_
        self.data = {"custom_id": custom_id}
        self.guild = FakeGuild()
        self.response = FakeResponse()


# --------------------------------------------------------------------------
# Extraction du vrai code du cog
# --------------------------------------------------------------------------
def load_from_cog(funcs=(), consts=()):
    """Compile fonctions et constantes nommees, prises telles quelles dans
    pronostics_cog.py. Les methodes de PronosticsCog sont recompilees comme
    fonctions libres, a lier ensuite sur un cog factice."""
    path = os.path.join(ROOT, "cogs", "pronostics_cog.py")
    with io.open(path, encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename=path)

    ns = {"datetime": datetime, "timezone": timezone, "database": database,
          "discord": FakeDiscord}

    trouves = set()

    def compile_node(node):
        mod = ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[]))
        exec(compile(mod, "<pronostics_cog>", "exec"), ns)

    for node in tree.body:                       # niveau module
        if isinstance(node, ast.Assign):
            noms = [t.id for t in node.targets if isinstance(t, ast.Name)]
            if not any(n in consts for n in noms):
                continue
            trouves.update(n for n in noms if n in consts)
            compile_node(node)
        elif isinstance(node, ast.FunctionDef) and node.name in funcs:
            trouves.add(node.name)
            compile_node(node)
        elif isinstance(node, ast.ClassDef) and node.name == "PronosticsCog":
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)) and sub.name in funcs:
                    sub.decorator_list = []
                    trouves.add(sub.name)
                    compile_node(sub)

    manque = (set(funcs) | set(consts)) - trouves
    if manque:
        raise SystemExit("Introuvable dans pronostics_cog.py : %s" % ", ".join(sorted(manque)))
    return ns


COG = load_from_cog(funcs=("_date_lisible", "_cg_custom_id", "_cg_lire_custom_id", "_cg_view",
                           "_cg_page", "on_interaction"),
                    consts=("POINTS_BON_PRONO", "CG_PAR_PAGE", "CG_CUSTOM_ID"))
_cg_custom_id = COG["_cg_custom_id"]
_cg_lire_custom_id = COG["_cg_lire_custom_id"]
POINTS = COG["POINTS_BON_PRONO"]
PAR_PAGE = COG["CG_PAR_PAGE"]


class FauxCog(object):
    _cg_page = COG["_cg_page"]
    on_interaction = COG["on_interaction"]


ECHECS = []


def verifie(nom, condition, detail=""):
    if condition:
        print("  ok   %s" % nom)
    else:
        print("  FAIL %s %s" % (nom, detail))
        ECHECS.append(nom)


# --------------------------------------------------------------------------
# Jeu de donnees
# --------------------------------------------------------------------------
# 25 pronostiqueurs en Saison 2, ex aequo deux a deux : c'est le cas qui casse une
# pagination dont le tri n'est pas total. Plus un ancien qui n'a joue qu'en S1.
JOUEURS = list(range(100001, 100026))
ANCIEN = 200001
NB_STARLIGUE = 30
BONS_S2 = {}                                    # uid -> bons pronos de la saison


def remplir():
    with database._connect() as con:
        def match(mid, competition, date, resultat):
            con.execute("INSERT INTO matchs (id, equipe1, equipe2, competition, date_match, resultat) "
                        "VALUES (?, 'A', 'B', ?, ?, ?)", (mid, competition, date, resultat))

        def prono(uid, mid, valeur):
            con.execute("INSERT INTO pronostics (user_id, match_id, pronostic) VALUES (?, ?, ?)",
                        (uid, mid, valeur))

        for i in range(1, NB_STARLIGUE + 1):                       # S2, Starligue
            match(i, "Starligue", "2026-09-%02dT20:00:00+00:00" % (i % 28 + 1), "1")
        for i in (41, 42):                                         # S2, coupe
            match(i, "Coupe de France", "2026-10-0%dT20:00:00+00:00" % (i - 40), "2")
        for i in range(51, 56):                                    # S1
            match(i, "Starligue", "2026-03-%02dT20:00:00+00:00" % (i - 40), "1")
        match(61, "Starligue", "2026-10-03T20:00:00+00:00", None)  # pas encore joue

        for k, uid in enumerate(JOUEURS, 1):
            bons = NB_STARLIGUE - k // 2                           # 30, 29, 29, 28, 28...
            for mid in range(1, bons + 1):
                prono(uid, mid, "1")
            for mid in range(bons + 1, NB_STARLIGUE + 1):          # rates : ne comptent pas
                prono(uid, mid, "2")
            prono(uid, 61, "1")                                    # sans resultat : non plus
            BONS_S2[uid] = bons
        for uid in JOUEURS[:4]:
            for mid in (41, 42):
                prono(uid, mid, "2")
            BONS_S2[uid] += 2
        for uid in (JOUEURS[0], ANCIEN):
            for mid in range(51, 56):
                prono(uid, mid, "1")


remplir()
ATTENDU_S2 = sorted(JOUEURS, key=lambda u: (-BONS_S2[u], u))


def uids_affiches(embed):
    """Les joueurs listes par une page, dans l'ordre d'affichage."""
    return [int(u) for u in re.findall(r"\d{6}", embed.fields[0][1])]


# --------------------------------------------------------------------------
# 1. La base : tout le monde, dans un ordre total
# --------------------------------------------------------------------------
def test_base():
    print("\n=== CLASSEMENT EN BASE ===")
    tous = database.get_general_leaderboard(POINTS, limit=None)
    verifie("limit=None rend tout le monde", len(tous) == len(JOUEURS), "%d" % len(tous))
    verifie("ordre : points, puis user_id entre ex aequo",
            [r["user_id"] for r in tous] == ATTENDU_S2)
    verifie("points = bons pronos x bareme",
            all(r["total_points"] == BONS_S2[r["user_id"]] * POINTS for r in tous))
    # Le recap hebdomadaire passe toujours limit=10 : il ne doit pas avoir bouge.
    dix = database.get_general_leaderboard(POINTS, limit=10)
    verifie("limit=10 rend toujours le top 10", [r["user_id"] for r in dix] == ATTENDU_S2[:10])
    archive = database.get_general_leaderboard(POINTS, limit=None, depuis=None)
    verifie("l'archive ajoute l'ancien de la S1",
            len(archive) == len(JOUEURS) + 1 and ANCIEN in [r["user_id"] for r in archive])


# --------------------------------------------------------------------------
# 2. Le contrat custom_id : ecrit par le bouton, relu par on_interaction
# --------------------------------------------------------------------------
def test_custom_id():
    print("\n=== CUSTOM_ID : ECRIT PAR LE BOUTON, RELU AU CLIC ===")
    competitions = (None, "Starligue", "Coupe de France", "Trophée des Champions",
                    "Europe : phase de groupes")
    for competition in competitions:
        for archive in (False, True):
            for page in (0, 3, 12):
                cid = _cg_custom_id(page, archive, competition)
                relu = _cg_lire_custom_id(cid)
                if relu != (page, archive, competition) or len(cid) > 100:
                    verifie("aller-retour %r" % cid, False, "relu %r" % (relu,))
    verifie("aller-retour sur %d competitions, 2 portees, 3 pages" % len(competitions),
            not any(e.startswith("aller-retour '") for e in ECHECS))
    print("  ex.  %r" % _cg_custom_id(2, True, "Coupe de France"))

    # on_interaction voit passer TOUS les boutons du bot : il ne doit prendre que les siens.
    for etranger in ("duel:sheet", "pronos:cg", "pronos:cgautre:1:0:", "pronos:cg:x:0:",
                     "pronos:cg:1", ""):
        verifie("%-22r n'est pas une fleche" % etranger, _cg_lire_custom_id(etranger) is None)


# --------------------------------------------------------------------------
# 3. Les pages
# --------------------------------------------------------------------------
def test_pages():
    print("\n=== PAGES ===")
    cog, guild = FauxCog(), FakeGuild()

    embed, view = cog._cg_page(guild, 0, False, None)
    print("  %s | %s" % (embed.title, embed.fields[0][0]))
    print("  " + embed.fields[0][1].split("\n")[0])
    prec, compteur, suiv = view.children
    verifie("vue sans expiration", view.timeout is None, repr(view.timeout))
    verifie("page 1 : dix joueurs, les dix premiers", uids_affiches(embed) == ATTENDU_S2[:PAR_PAGE])
    verifie("page 1 : places annoncees", embed.fields[0][0] == "Places 1 à 10 sur 25", embed.fields[0][0])
    verifie("page 1 : podium", all(m in embed.fields[0][1] for m in ("🥇", "🥈", "🥉")))
    verifie("page 1 : fleche gauche eteinte, droite allumee", prec.disabled and not suiv.disabled)
    verifie("compteur 1/3, non cliquable", compteur.label == "1/3" and compteur.disabled)
    verifie("deux fleches, deux custom_id", prec.custom_id != suiv.custom_id)
    verifie("saison en cours annoncee", "Saison en cours" in embed.description, embed.description)

    embed, view = cog._cg_page(guild, 1, False, None)
    verifie("page 2 : rangs 11 a 20", "**#11**" in embed.fields[0][1] and "**#20**" in embed.fields[0][1])
    verifie("page 2 : pas de medaille", "🥇" not in embed.fields[0][1])
    verifie("page 2 : joueur parti, nomme par son id",
            ("Utilisateur (%d)" % PARTI) in embed.fields[0][1], embed.fields[0][1])
    verifie("page 2 : les deux fleches allumees",
            not view.children[0].disabled and not view.children[2].disabled)

    embed, view = cog._cg_page(guild, 2, False, None)
    verifie("page 3 : les cinq derniers", uids_affiches(embed) == ATTENDU_S2[20:])
    verifie("page 3 : places annoncees", embed.fields[0][0] == "Places 21 à 25 sur 25", embed.fields[0][0])
    verifie("page 3 : fleche droite eteinte", view.children[2].disabled and not view.children[0].disabled)

    # Un vieux bouton peut viser une page qui n'existe plus.
    embed, view = cog._cg_page(guild, 99, False, None)
    verifie("page trop grande ramenee a la derniere", view.children[1].label == "3/3")
    embed, view = cog._cg_page(guild, -1, False, None)
    verifie("page negative ramenee a la premiere", view.children[1].label == "1/3")

    embed, view = cog._cg_page(guild, 0, False, "Coupe de France")
    verifie("une seule page : pas de fleches", view is None)
    verifie("une seule page : 4 joueurs de la coupe", uids_affiches(embed) == JOUEURS[:4])
    verifie("une seule page : « Top Joueurs »", embed.fields[0][0] == "Top Joueurs", embed.fields[0][0])

    embed, view = cog._cg_page(guild, 2, True, None)
    verifie("archive : 26 classes", embed.fields[0][0] == "Places 21 à 26 sur 26", embed.fields[0][0])
    verifie("archive : annoncee dans le titre", "archive" in embed.title, embed.title)
    verifie("archive : la fleche garde la portee",
            _cg_lire_custom_id(view.children[0].custom_id) == (1, True, None))

    verifie("competition inconnue : rien a afficher", cog._cg_page(guild, 0, False, "Inconnue") is None)

    # Discord refuse un champ d'embed au-dela de 1024 caracteres. Pire cas : dix
    # pseudos de 32 caracteres, des rangs et des points a quatre chiffres.
    class GuildBavarde(object):
        def get_member(self, uid):
            m = FakeMember(uid)
            m.display_name = "W" * 32
            return m
    embed, _ = cog._cg_page(GuildBavarde(), 2, True, None)
    pire = len(embed.fields[0][1]) + PAR_PAGE * 4
    verifie("champ <= 1024 caracteres", pire <= 1024, "%d" % pire)


# --------------------------------------------------------------------------
# 4. Les clics : sans memoire, pour tout le monde
# --------------------------------------------------------------------------
def clic(custom_id, type_="component"):
    """Un clic traite par un cog NEUF : rien ne survit d'un clic a l'autre, comme
    apres un redemarrage du bot."""
    inter = FakeInteraction(custom_id, type_)
    asyncio.run(FauxCog().on_interaction(inter))
    return inter.response


def test_clics():
    print("\n=== CLICS ===")
    embed, view = FauxCog()._cg_page(FakeGuild(), 0, False, None)

    # On feuillette jusqu'au bout en ne suivant QUE les custom_id des boutons.
    vus = uids_affiches(embed)
    pages = 1
    while not view.children[2].disabled:
        rep = clic(view.children[2].custom_id)
        embed, view = rep.edits[-1]["embed"], rep.edits[-1]["view"]
        vus += uids_affiches(embed)
        pages += 1
        if pages > 10:
            break
    verifie("3 pages en suivant la fleche droite", pages == 3, "%d" % pages)
    verifie("chaque joueur vu une fois, dans l'ordre", vus == ATTENDU_S2)

    rep = clic(view.children[0].custom_id)
    verifie("la fleche gauche revient page 2", rep.edits[-1]["view"].children[1].label == "2/3")
    verifie("le clic edite le message, sans rien envoyer", len(rep.edits) == 1 and not rep.messages)

    rep = clic("duel:sheet")
    verifie("bouton d'un autre cog : ignore", not rep.edits and not rep.messages)
    rep = clic(_cg_custom_id(1, False, None), type_="application_command")
    verifie("autre type d'interaction : ignore", not rep.edits and not rep.messages)

    # Le classement s'est vide depuis (nouvelle saison) : on le dit, en prive.
    saison = database.DEBUT_SAISON_PRONOS
    database.DEBUT_SAISON_PRONOS = "2027-09-01"
    try:
        rep = clic(_cg_custom_id(1, False, None))
        verifie("saison repartie de zero : message prive",
                not rep.edits and len(rep.messages) == 1 and rep.messages[0][1].get("ephemeral") is True)
        rep = clic(_cg_custom_id(1, True, None))
        verifie("l'archive, elle, repond toujours", len(rep.edits) == 1)
    finally:
        database.DEBUT_SAISON_PRONOS = saison

    # Une page raccourcie a une seule page perd ses fleches (view=None les retire).
    rep = clic(_cg_custom_id(1, False, "Coupe de France"))
    verifie("plus qu'une page : les fleches partent",
            len(rep.edits) == 1 and rep.edits[0]["view"] is None)


test_base()
test_custom_id()
test_pages()
test_clics()

print("")
if ECHECS:
    raise SystemExit("ECHECS : %s" % ", ".join(ECHECS))
print("TOUS LES TESTS CLASSEMENT PRONOS PASSENT")
