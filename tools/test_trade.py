# -*- coding: utf-8 -*-
"""Test hors-ligne de l'echange de cartes (Saison 2, refonte ergonomique).

discord.py n'est pas installe dans l'environnement de dev : impossible d'importer
cogs/trade_cog.py. On EXTRAIT donc du source (ast) ce qui est testable sans
Discord -- fonctions du module, classe Deal, methodes de tri/description du
composeur et resolution des exemplaires -- et on l'execute tel quel. Le test
porte donc sur le VRAI code, pas sur une copie qui divergerait au prochain
ajustement de l'ergonomie.

    py -3 tools/test_trade.py
"""
import ast
import io
import json
import os
import sys
import tempfile
import unicodedata
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# La console Windows est en cp1252 : sans ca, afficher un embed de jeu fait
# tomber le test sur un UnicodeEncodeError qui n'a rien a voir avec l'echange.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# --- Base temporaire : surtout NE PAS toucher a la vraie collection.db ---
import database
TMPDIR = tempfile.mkdtemp(prefix="trade_")
database.DATA_DIR = TMPDIR
database.DB_NAME = os.path.join(TMPDIR, "test.db")
database.initialize_database()

SRC = os.path.join(ROOT, "cogs", "trade_cog.py")

# Les regles de saison sont definies une seule fois, dans collection_cog : ce
# module-la n'importe pas discord, on peut le lire tel quel.
import importlib.util
_spec = importlib.util.spec_from_file_location(
    "_saisons", os.path.join(ROOT, "cogs", "collection_cog.py"))
_src = io.open(os.path.join(ROOT, "cogs", "collection_cog.py"), encoding="utf-8").read()
_ns = {}
_tree = ast.parse(_src)
_keep = [n for n in _tree.body
         if (isinstance(n, ast.FunctionDef) and n.name in
             ("saison_de", "cartes_de_la_saison", "saison_en_cours"))
         or (isinstance(n, ast.Assign)
             and getattr(n.targets[0], "id", "") == "SAISON_COURANTE")]
exec(compile(ast.Module(_keep, []), "collection_cog.py", "exec"), _ns)
saison_de = _ns["saison_de"]
saison_en_cours = _ns["saison_en_cours"]


# --------------------------------------------------------------------------
# Extraction du vrai code du cog
# --------------------------------------------------------------------------
def load_from_cog(funcs=(), classes=(), methods=()):
    """Compile les morceaux nommes, pris tels quels dans trade_cog.py.

    `methods` = [("TradePicker", "_candidates"), ...] : les methodes sont
    recompilees comme fonctions libres, a lier ensuite sur un objet factice.
    """
    with io.open(SRC, encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename=SRC)

    # discord n'est pas installe : on ne rejoue PAS les imports du cog, on fournit
    # a la main les seuls modules dont le code extrait a besoin.
    ns = {"database": database, "Counter": Counter, "unicodedata": unicodedata,
          "saison_de": saison_de, "saison_en_cours": saison_en_cours}
    # Les constantes du module (RARITY_EMOJI, MAX_PER_SIDE, ...) : indispensables
    # aux fonctions extraites, et c'est justement leur valeur reelle qu'on teste.
    for node in tree.body:
        if isinstance(node, ast.Assign):
            exec(compile(ast.Module([node], []), SRC, "exec"), ns)

    wanted = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in funcs:
            wanted.append(node)
        elif isinstance(node, ast.ClassDef) and node.name in classes:
            wanted.append(node)
        elif isinstance(node, ast.ClassDef):
            for cls, meth in methods:
                if node.name != cls:
                    continue
                for sub in node.body:
                    if isinstance(sub, ast.FunctionDef) and sub.name == meth:
                        wanted.append(sub)

    exec(compile(ast.Module(wanted, []), SRC, "exec"), ns)

    manquant = ([n for n in funcs if n not in ns]
                + [n for n in classes if n not in ns]
                + [m for _, m in methods if m not in ns])
    if manquant:
        raise SystemExit("Introuvable dans trade_cog.py : %s" % ", ".join(manquant))
    return ns


T = load_from_cog(
    funcs=("release", "_fold", "owned_counts", "_group", "_fmt_basket"),
    classes=("Deal",),
    methods=[("TradePicker", "_owner_side"), ("TradePicker", "_partner"),
             ("TradePicker", "_changed"), ("TradePicker", "_ready"),
             ("TradePicker", "_candidates"), ("TradePicker", "_describe"),
             ("TradeCog", "echangeable"), ("TradeCog", "echangeable_id"),
             ("TradeCog", "tradables"), ("TradeCog", "resolve_rowids")],
)


# --------------------------------------------------------------------------
# Doublures : juste ce que le code extrait touche reellement
# --------------------------------------------------------------------------
with io.open(os.path.join(ROOT, "cards.json"), encoding="utf-8") as f:
    CARDS = json.load(f)
BY_ID = {c["id"]: c for c in CARDS}
BY_ID.update({str(c["id"]): c for c in CARDS})
SAISON_JOUEE = saison_en_cours(CARDS)


class FakeCog:
    saison = SAISON_JOUEE

    def get_card(self, cid):
        return BY_ID.get(cid) or BY_ID.get(str(cid))

    echangeable = T["echangeable"]
    echangeable_id = T["echangeable_id"]
    tradables = T["tradables"]
    resolve_rowids = T["resolve_rowids"]


class FakeMember:
    def __init__(self, uid, name):
        self.id = uid
        self.display_name = name
        self.mention = "<@%d>" % uid


class FakePicker:
    """Le composeur reduit a son etat : les methodes extraites y sont liees."""

    _owner_side = T["_owner_side"]
    _partner = T["_partner"]
    _changed = T["_changed"]
    _ready = T["_ready"]
    _candidates = T["_candidates"]
    _describe = T["_describe"]

    def __init__(self, cog, deal, side, target="mine", query=""):
        self.cog = cog
        self.deal = deal
        self.side = side
        self.target = target
        self.query = query
        self.counts = {s: T["owned_counts"](deal.member(s).id, cog.echangeable_id)
                       for s in ("a", "b")}
        self.archive = (len(database.get_user_collection(deal.member(side).id))
                        - sum(self.counts[side].values()))
        self.draft = {"mine": list(deal.give[side]),
                      "theirs": list(deal.give[deal.other(side)])}


# --------------------------------------------------------------------------
# Harnais
# --------------------------------------------------------------------------
ECHECS = []


def ok(condition, titre, detail=""):
    if condition:
        print("  [ok] %s" % titre)
    else:
        ECHECS.append(titre)
        print("  [KO] %s %s" % (titre, detail))


def donner(user_id, *card_ids):
    for cid in card_ids:
        database.add_card_to_collection(user_id, cid)


def cid_par_nom(nom):
    for c in CARDS:
        if c["nom"] == nom:
            return c["id"]
    raise SystemExit("carte introuvable : %s" % nom)


def par_rarete(rarete, n, club=None, saison=None):
    """n cartes distinctes d'une rarete donnee, de la saison JOUEE par defaut."""
    saison = SAISON_JOUEE if saison is None else saison
    out = [c["id"] for c in CARDS
           if c["rarete"] == rarete and saison_de(c) == saison
           and (club is None or c["club"] == club)]
    if len(out) < n:
        raise SystemExit("pas assez de cartes %s / saison %s / %s" % (rarete, saison, club))
    return out[:n]


def archive(n):
    """n cartes d'une saison passee : l'archive, celle qui ne doit pas s'echanger."""
    out = [c["id"] for c in CARDS if saison_de(c) != SAISON_JOUEE]
    if len(out) < n:
        raise SystemExit("pas assez de cartes d'archive")
    return out[:n]


# ==========================================================================
print("\n=== 1. Briques du module ===")

ok(T["_fold"]("Rémili") == "remili", "la recherche ignore les accents")
ok(T["_fold"]("PSG") == "psg", "la recherche ignore la casse")
ok(T["_fold"](None) == "", "recherche vide sur une valeur absente")

ok(T["_group"]([7, 7, 3]) == [(7, 2), (3, 1)], "les exemplaires identiques font une ligne")
ok(T["_group"]([3, 7, 3]) == [(3, 2), (7, 1)], "le regroupement garde l'ordre d'ajout")
ok(T["_group"]([]) == [], "panier vide")

A, B = 1001, 1002
COM = par_rarete("Commun", 4)
RAR = par_rarete("Rare", 3)
LEG = par_rarete("Légendaire", 2)

# A : 2 exemplaires de COM[0], 1 de COM[1], 1 Rare, 1 Legendaire
donner(A, COM[0], COM[0], COM[1], RAR[0], LEG[0])
# B : 1 exemplaire de COM[1] (donc A et B ont COM[1] en commun), 2 de RAR[1]
donner(B, COM[1], RAR[1], RAR[1])

cA = T["owned_counts"](A)
cB = T["owned_counts"](B)
ok(cA[COM[0]] == 2 and cA[COM[1]] == 1, "owned_counts compte les exemplaires")
ok(cA.get(RAR[1], 0) == 0, "owned_counts ignore ce qu'on ne possede pas")

# --------------------------------------------------------------------------
print("\n=== 2. Marqueurs de l'echange (les deux infos qui font decider) ===")

txt = T["_fmt_basket"](FakeCog(), [COM[0]], cA, cB)
ok("⚠️" not in txt, "donner un doublon n'alarme pas", txt)
ok("🆕" in txt, "carte absente chez l'autre : marquee neuve", txt)

txt = T["_fmt_basket"](FakeCog(), [COM[0], COM[0]], cA, cB)
ok("×2" in txt and "⚠️" in txt,
   "donner SES DEUX exemplaires alarme bien", txt)

txt = T["_fmt_basket"](FakeCog(), [COM[1]], cA, cB)
ok("⚠️" in txt, "donner son seul exemplaire alarme", txt)
ok("🆕" not in txt, "carte deja possedee par l'autre : pas de 🆕", txt)

ok(T["_fmt_basket"](FakeCog(), [], cA, cB).startswith("_("), "cote vide lisible")

# --------------------------------------------------------------------------
print("\n=== 3. Tri du composeur ===")

deal = T["Deal"](FakeMember(A, "Alice"), FakeMember(B, "Bob"))
p = FakePicker(FakeCog(), deal, "a", target="mine")
noms = [(c["id"], owned, dispo, neuf) for c, owned, dispo, neuf in p._candidates()]

ok(noms[0][0] == COM[0], "ce qu'on donne : le doublon passe en tete",
   str(noms[:2]))
ok(all(t[1] > 1 for t in noms[:1]) and all(t[1] == 1 for t in noms[1:]),
   "les doublons sont groupes en haut")
prem_uniques = [t for t in noms if t[1] == 1]
ok(prem_uniques[0][3] is True,
   "parmi les uniques, ce qui manque a l'autre d'abord", str(prem_uniques))

p.target = "theirs"
p.draft = {"mine": [], "theirs": []}
cand = p._candidates()
ok(cand[0][0]["id"] == RAR[1],
   "ce qu'on demande : ce qui manque a SA collection d'abord",
   str([c["id"] for c, *_ in cand]))
ok(cand[0][1] == 2, "et de preference un doublon de l'autre")
ok(all(c["id"] in (RAR[1], COM[1]) for c, *_ in cand),
   "on ne pioche que dans la collection de l'autre")

# Filtre : nom ET club
club_cible = BY_ID[RAR[1]]["club"]
p.query = T["_fold"](BY_ID[RAR[1]]["nom"])
ok([c["id"] for c, *_ in p._candidates()] == [RAR[1]], "filtre par nom de joueur")
p.query = T["_fold"](club_cible)
trouve = [c["id"] for c, *_ in p._candidates()]
ok(RAR[1] in trouve and all(BY_ID[i]["club"] == club_cible for i in trouve),
   "filtre par club (ce qui remplace l'ancienne etape club)", str(trouve))
p.query = T["_fold"]("zzzzz")
ok(p._candidates() == [], "filtre sans resultat")

# --------------------------------------------------------------------------
print("\n=== 4. Descriptions ===")

p = FakePicker(FakeCog(), deal, "a", target="mine")
d_dbl = p._describe(BY_ID[COM[0]], 2, 2, True)
ok("tu en as 2" in d_dbl and "🆕" in d_dbl, "doublon a donner : quantite + interet", d_dbl)
d_uni = p._describe(BY_ID[LEG[0]], 1, 1, True)
ok("⚠️" in d_uni and "seul exemplaire" in d_uni, "unique a donner : alerte", d_uni)
d_pris = p._describe(BY_ID[COM[0]], 2, 0, True)
ok("déjà dans l'offre" in d_pris, "plus rien de disponible : dit", d_pris)
ok(len(d_dbl) <= 100 and len(d_uni) <= 100, "descriptions dans la limite Discord")

p.target = "theirs"
d_dem = p._describe(BY_ID[RAR[1]], 2, 2, True)
ok("2 exemplaires" in d_dem and "tu ne l'as pas" in d_dem, "a demander : dit ce qu'on gagne", d_dem)
d_deja = p._describe(BY_ID[COM[1]], 1, 1, False)
ok("tu l'as déjà" in d_deja, "a demander : previent du doublon inutile", d_deja)

# --------------------------------------------------------------------------
print("\n=== 5. Etat de l'accord (anti-arnaque) ===")

deal = T["Deal"](FakeMember(A, "Alice"), FakeMember(B, "Bob"))
ok(deal.side_of(A) == "a" and deal.side_of(B) == "b", "chacun de son cote")
ok(deal.other("a") == "b", "cote oppose")
ok(not deal.ready(), "un echange vide n'est pas pret")

deal.give["a"] = [COM[0]]
ok(not deal.ready(), "pas de cadeau : un seul cote ne suffit pas")
deal.give["b"] = [RAR[1]]
ok(deal.ready(), "les deux cotes remplis : pret")

deal.sign("a")
ok(deal.accepted == {"a": True, "b": False}, "proposer vaut acceptation de son auteur")
deal.accepted["b"] = True
deal.sign("b")
ok(deal.accepted == {"a": False, "b": True},
   "toute modification efface l'acceptation de l'AUTRE")

# --------------------------------------------------------------------------
print("\n=== 6. Verrous ===")

T["ACTIVE_TRADERS"].update({A, B})
T["release"](deal)
ok(deal.closed and not (T["ACTIVE_TRADERS"] & {A, B}),
   "fermer un echange libere les deux joueurs")

# --------------------------------------------------------------------------
print("\n=== 7. Resolution des exemplaires au dernier moment ===")

cog = FakeCog()
rows = cog.resolve_rowids(A, [COM[0], COM[0]])
ok(rows is not None and len(set(rows)) == 2,
   "deux exemplaires demandes = deux lignes DISTINCTES", str(rows))
ok(cog.resolve_rowids(A, [COM[0], COM[0], COM[0]]) is None,
   "trois exemplaires alors qu'on en a deux : refus")
ok(cog.resolve_rowids(A, [RAR[1]]) is None, "carte non possedee : refus")
ok(cog.resolve_rowids(A, [COM[1], RAR[0]]) is not None, "cartes distinctes possedees : ok")

# --------------------------------------------------------------------------
print("\n=== 8. Echange complet, de bout en bout ===")

avant_a = sorted(database.get_user_collection(A))
avant_b = sorted(database.get_user_collection(B))
total_avant = Counter(avant_a) + Counter(avant_b)

give_a = [COM[0], LEG[0]]          # un doublon + sa legendaire unique
give_b = [RAR[1]]                  # un de ses deux exemplaires

ra = cog.resolve_rowids(A, give_a)
rb = cog.resolve_rowids(B, give_b)
ok(database.execute_trade(A, ra, B, rb), "l'echange s'execute")

apres_a = Counter(database.get_user_collection(A))
apres_b = Counter(database.get_user_collection(B))
ok(apres_a[LEG[0]] == 0 and apres_b[LEG[0]] == 1, "la legendaire a bien change de main")
ok(apres_a[COM[0]] == 1 and apres_b[COM[0]] == 1, "un seul des deux doublons est parti")
ok(apres_a[RAR[1]] == 1 and apres_b[RAR[1]] == 1, "le rare recu, l'autre exemplaire garde")
ok(apres_a + apres_b == total_avant, "aucune carte creee ni perdue")

# Proposition perimee : la carte promise n'est plus la
perime = cog.resolve_rowids(A, [LEG[0]])
ok(perime is None, "une carte deja echangee ne se resout plus (refus propre)")

# Rowid vole entre-temps : execute_trade doit refuser sans rien casser
avant = Counter(database.get_user_collection(A)) + Counter(database.get_user_collection(B))
faux = cog.resolve_rowids(B, [RAR[1]])
ok(not database.execute_trade(A, faux, B, cog.resolve_rowids(B, [COM[1]])),
   "un exemplaire qui n'appartient pas au donneur fait echouer l'echange")
apres = Counter(database.get_user_collection(A)) + Counter(database.get_user_collection(B))
ok(apres == avant, "un echec ne deplace rien du tout")

# --------------------------------------------------------------------------
print("\n=== 9. Brouillon du composeur ===")

deal = T["Deal"](FakeMember(A, "Alice"), FakeMember(B, "Bob"))
deal.give["a"] = [COM[1]]
deal.give["b"] = [RAR[1]]
p = FakePicker(cog, deal, "b")           # Bob modifie : son cote est "b"
ok(p.draft["mine"] == [RAR[1]] and p.draft["theirs"] == [COM[1]],
   "le composeur s'ouvre prerempli, vu du bon cote")
ok(not p._changed(), "a l'ouverture, rien n'a change")
ok(p._ready(), "la proposition preremplie est deja complete")
p.draft["mine"] = []
ok(p._changed() and not p._ready(), "vider son cote : modifie et incomplet")
ok(p._owner_side() == "b", "on donne depuis SA collection")
p.target = "theirs"
ok(p._owner_side() == "a", "on demande depuis celle de l'autre")
ok(p._partner().display_name == "Alice", "le partenaire est bien l'autre joueur")

# ==========================================================================
# --------------------------------------------------------------------------
print("\n=== 10. Archive des saisons passees : hors echange ===")

C, D = 1003, 1004
ARC = archive(2)
S2 = par_rarete("Rare", 2)
donner(C, ARC[0], ARC[1], S2[0])
donner(D, S2[1])

ok(cog.echangeable(BY_ID[S2[0]]), "une carte de la saison en cours s'echange")
ok(not cog.echangeable(BY_ID[ARC[0]]), "une carte d'archive ne s'echange pas")
ok(not cog.echangeable(None), "carte inconnue : refusee")

tr = cog.tradables(C)
ok([cid for _, cid in tr] == [S2[0]],
   "tradables ne rend que la saison en cours", str(tr))

cnt = T["owned_counts"](C, cog.echangeable_id)
ok(dict(cnt) == {S2[0]: 1}, "owned_counts filtre l'archive", str(dict(cnt)))
ok(sum(T["owned_counts"](C).values()) == 3, "sans garde, tout est compte")

deal = T["Deal"](FakeMember(C, "Carla"), FakeMember(D, "Dan"))
p = FakePicker(cog, deal, "a")
listes = [c["id"] for c, *_ in p._candidates()]
ok(listes == [S2[0]], "le composeur ne propose jamais l'archive", str(listes))
ok(p.archive == 2, "et il annonce combien de cartes il masque", str(p.archive))

p.target = "theirs"
ok([c["id"] for c, *_ in p._candidates()] == [S2[1]],
   "on ne peut pas non plus DEMANDER une carte d'archive")

ok(cog.resolve_rowids(C, [str(ARC[0])]) is None,
   "meme visee directement, l'archive ne se resout pas en exemplaire")
ok(cog.resolve_rowids(C, [S2[0]]) is not None, "la saison en cours, si")

# Les identifiants de cartes sont MIXTES : entiers en saison 1, slugs en saison 2.
# Tout compte passe par str(card_id) ; sans ca, deux exemplaires d'une meme carte
# comptent pour deux cartes differentes (et int(slug) plantait tout net).
ok(isinstance(S2[0], str) and not str(ARC[0]).isdigit() is False,
   "le jeu de test couvre bien les deux formes d'identifiant",
   "S2=%r ARC=%r" % (S2[0], ARC[0]))
melange = T["owned_counts"](C)
ok(set(melange) == {str(ARC[0]), str(ARC[1]), str(S2[0])},
   "owned_counts normalise entiers ET slugs en cles str", str(set(melange)))

# L'archive ne doit pas non plus fausser le « nouvelle pour lui »
donner(D, ARC[0])
p2 = FakePicker(cog, deal, "a")
ok(p2.counts["b"].get(str(ARC[0]), 0) == 0,
   "une archive possedee par l'autre reste invisible des deux cotes")


print("\n" + "=" * 60)
if ECHECS:
    print("%d test(s) en echec :" % len(ECHECS))
    for t in ECHECS:
        print("  - %s" % t)
    sys.exit(1)
print("Tous les tests passent.")
