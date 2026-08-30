"""Genere les prompts Midjourney de la saison 2, un par joueur. (OFFLINE, dev only.)

Le bloc de style est GELE et rigoureusement identique pour tous les joueurs : c'est
ce qui fait qu'une collection a l'air d'une collection. Seule varie l'URL passee a
--oref (le portrait officiel du joueur), qui porte a elle seule la ressemblance
(--ow 550). Le nom du joueur n'apparait volontairement PAS dans le prompt : il n'y
sert a rien et decalerait un rendu deja valide.

Consequence : les 255 prompts sont textuellement identiques, donc les fichiers
telecharges depuis Midjourney ne sont pas identifiables par leur nom. D'ou la sortie
club par club et l'ordre de collage fige dans out/paste_order.json : c'est ce fichier
qui permettra de reattribuer les images generees a chaque joueur.

Les refs doivent etre accessibles par URL publique pour que --oref les lise. Deux options :
  --base-url <URL>      prefixe d'hebergement des fichiers refs/ (recommande)
  --use-source-url      passe directement l'URL lnh.fr (risque de hotlink bloque)

Entree : data/roster_s2.json  (enrichi par tools/fetch_lnh_s2.py)
Sortie : out/prompts/<NN>-<club>.txt   un prompt par ligne, dans l'ordre de collage
         out/prompts_all.txt           tout d'un bloc
         out/paste_order.json          {club: [id, ...]} -> pour le tri des rendus

Usage :
    python tools/build_prompts_s2.py --base-url https://raw.githubusercontent.com/Blacklorren/collection-bot/main/refs
    python tools/build_prompts_s2.py --base-url ... --sref https://i.imgur.com/xxxx.png
    python tools/build_prompts_s2.py --base-url ... --club Nantes
"""
import argparse
import collections
import json
import os
import re
import sys
import unicodedata

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from jersey_colors import couleurs_maillot  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MANIFEST = os.path.join(ROOT, "data", "roster_s2.json")
HAIR = os.path.join(ROOT, "data", "hair.json")
OUT_DIR = os.path.join(ROOT, "out")
PROMPTS_DIR = os.path.join(OUT_DIR, "prompts")
TEST_DIR = os.path.join(OUT_DIR, "test")

# --- Prompt valide en test, a ne pas retoucher sans refaire une passe de controle ---
# --ar 3:4 : plus proche du canvas 4:5 (992x1240) que 2:3, et _crop_to_ratio() du
# renderer recadre proprement le reste. --seed fige pour homogeneiser la lumiere
# et la pose sur toute la collection.
# Image de style de reference, uploadee sur Midjourney. Le --sw 100 present dans le
# prompt ne veut rien dire sans elle : c'est elle qui porte le rendu valide en test.
# En dur volontairement, pour qu'un rerun sans le flag ne fasse pas silencieusement
# deriver le style de la collection.
DEFAULT_SREF = "https://s.mj.run/Dq9JJbmxWzE"

TEXTE = (
    # "Semi-realistic" en tete de prompt demandait litteralement de la demi-photo, a la
    # position la plus lourde. Remplace le 2026-08-17.
    "Stylized comic book bust portrait illustration, bold graphic novel art, "
    "comic book style, chest-up framing. "
    "Athletic man with {cheveux}, wearing {jersey} handball jersey, "
    "both shoulders clearly visible, cropped at high-chest. "
    "Clean confident dark ink linework with fine cross-hatching in the shadows. "
    "Cel-shaded rendering: flat shapes of color with hard shadow edges, minimal blending, limited palette. "
    "Warm peach-orange light on the skin from the upper front, cool desaturated slate-blue shadows on the opposite side. "
    # "textured stubble" etait fige dans le prompt d'origine : il imposait de la barbe
    # meme aux joueurs glabres. Remplace par la pilosite reelle de chacun.
    # "Realistic facial structure" tirait vers la photo ; l'intention (visage juste)
    # est conservee sans le mot. "subtle forehead lines" est laisse volontairement :
    # il fait partie de la variante F mais n'a pas ete retenu.
    "Accurate likeness with stylized features, {barbe}, subtle forehead lines, defined cheekbones. "
    "Neutral confident expression, mouth closed, direct eye contact. "
    "Front-facing, eye-level camera, subject centered with clear space above the head. "
    "Flat plain light grey background, completely empty. "
    "Soft even lighting, moderate contrast, crisp and clean. "
)
NEGATIF = ("--no headshot, extreme close-up, cropped at neck, floating head, photorealistic, "
           "3D render, neon, dark background, bokeh, detailed background, smile, text, watermark")

# Reste de la variante "texte allege" NON retenu dans le prompt de base : les deux
# autres remplacements y ont ete integres en dur le 2026-08-17.
ALLEGEMENTS = [
    ("subtle forehead lines, ", ""),
]
NEGATIF_RENFORCE = NEGATIF.replace(
    "--no ", "--no photograph, photography, realistic skin texture, film grain, DSLR, ")

# Valeurs par defaut, surchargeables en ligne de commande.
# Historique de --ow, le seul reglage qui a bouge :
#   550  d'origine, au-dessus du plafond conseille par Midjourney (400) : combine a
#        --stylize 50 il faisait primer la PHOTO de reference sur le style demande,
#        d'ou les rendus photorealistes.
#   100  le 2026-08-17 (defaut MJ) : le style comic s'impose enfin, mais la
#        ressemblance devient trop lache. Clubs Saran et Paris generes ainsi.
#   300  le 2026-08-25, option C de GRILLE_TEST : compromis retenu apres la passe de
#        test. Saint-Raphael a ete genere et valide avec cette valeur -- c'est donc
#        elle qui fait foi pour les clubs suivants. La constante etait restee a 100
#        (300 passe en ligne de commande ce jour-la) : corrige ici pour qu'un rerun
#        ne reparte pas en silence sur l'ancien reglage.
OW, SW, STYLIZE, SEED, AR = 300, 100, 50, 1234, "3:4"


def assembler(texte, ow, sw, stylize, seed=SEED, negatif=NEGATIF):
    graine = f" --seed {seed}" if seed is not None else ""
    return f"{texte}{negatif} --ar {AR}{graine} --sw {sw} --ow {ow} --v 7 --stylize {stylize}"


def alleger(texte):
    for avant, apres in ALLEGEMENTS:
        texte = texte.replace(avant, apres)
    return texte


# Grille du mode --test : (libelle, ow, sw, stylize, texte allege ?)
# Chaque ligne ne doit changer QU'UNE valeur par rapport au temoin A, sinon on ne sait
# pas ce qui a produit l'ecart. Les lignes sont donc derivees des constantes et non
# codees en dur : quand OW est passe de 100 a 300, l'ancienne grille se retrouvait avec
# A et C identiques et un D/E restes sur l'ancien ow.
GRILLE_TEST = [
    ("A  temoin, reglages actuels", OW, SW, STYLIZE, False),
    ("B  ressemblance relachee", 100, SW, STYLIZE, False),
    ("C  ressemblance au plafond conseille MJ", 400, SW, STYLIZE, False),
    ("D  style de reference renforce", OW, 400, STYLIZE, False),
    ("E  stylize releve", OW, SW, 250, False),
    ("F  actuel sans les rides du front", OW, SW, STYLIZE, True),
]


def slugify(s):
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Za-z0-9]+", "-", s).strip("-").lower()


def corps_pour(p, cheveux, allege=False):
    """Partie descriptive du prompt pour un joueur, hors parametres."""
    coul = p.get("jersey")
    # "an orange", "a black" : seul orange commence par une voyelle
    jersey = f"{'an' if coul and coul[0] in 'aeiou' else 'a'} {coul}" if coul else "a"
    h = cheveux.get(p["id"], {})
    texte = alleger(TEXTE) if allege else TEXTE
    return (texte
            .replace("{cheveux}", h.get("cheveux") or "short hair")
            .replace("{jersey}", jersey)
            .replace("{barbe}", h.get("barbe") or "light stubble"))


def mode_test(ids, players, cheveux, sref, args):
    """Meme joueur decline sur toute la grille de reglages : une seule session de
    collage suffit a trancher, au lieu de relancer des seeds au hasard."""
    par_id = {p["id"]: p for p in players}
    manquants = [i for i in ids if i not in par_id or not par_id[i].get("ref_file")]
    if manquants:
        raise SystemExit(f"Inconnus ou sans ref : {', '.join(manquants)}")

    lignes, legende = [], []
    for pid in ids:
        p = par_id[pid]
        url = ref_url_for(p, args)
        for libelle, ow, sw, st, allege in GRILLE_TEST:
            prompt = assembler(corps_pour(p, cheveux, allege), ow, sw, st,
                               seed=None,  # graine libre : on compare des reglages, pas des tirages
                               negatif=NEGATIF_RENFORCE if allege else NEGATIF)
            prompt += f" --oref {url}"
            if sref:
                prompt += f" --sref {sref}"
            lignes.append(prompt)
            legende.append(f"{len(lignes):2d}. {p['nom']:24s} {libelle}"
                           f"   (ow {ow}, sw {sw}, stylize {st}"
                           f"{', texte allege' if allege else ''})")

    # Dossier a part : le mode normal vide out/prompts/ a chaque run, il effacerait
    # le fichier de test.
    os.makedirs(TEST_DIR, exist_ok=True)
    chemin = os.path.join(TEST_DIR, "prompts_test.txt")
    open(chemin, "w", encoding="utf-8").write("\n".join(lignes) + "\n")
    leg = os.path.join(TEST_DIR, "legende.txt")
    open(leg, "w", encoding="utf-8").write("\n".join(legende) + "\n")

    print(f"{len(lignes)} prompts de test -> {os.path.relpath(chemin, ROOT)}")
    print(f"Legende -> {os.path.relpath(leg, ROOT)}\n")
    print("\n".join(legende))
    print("\nColle les lignes DANS L'ORDRE : la legende s'y refere.")
    print("La graine est libre ici, pour juger le reglage et non un tirage particulier.")


def couleurs_par_joueur(players, recompute=False):
    """Renseigne le champ 'jersey' de chaque joueur ayant une ref.

    La detection est faite image par image, puis LISSEE par (club, gardien ou non) :
    au sein d'un meme club les ecarts observes ("purple" vs "violet", "black" vs
    "black and navy blue") sont du bruit de detection, pas de vraies differences de
    maillot, puisque les portraits viennent d'une seule seance officielle. Les
    gardiens sont traites a part : leur maillot est reellement different.

    Une valeur deja presente dans le manifest est conservee : c'est ce qui permet de
    corriger une couleur a la main sans qu'un rerun l'ecrase.
    """
    brut = {}
    for p in players:
        if not p.get("ref_file") or not os.path.exists(os.path.join(ROOT, p["ref_file"])):
            continue
        if p.get("jersey") and not recompute:
            continue
        brut[p["id"]] = couleurs_maillot(os.path.join(ROOT, p["ref_file"]))

    groupes = collections.defaultdict(collections.Counter)
    for p in players:
        c = brut.get(p["id"])
        if c:
            groupes[(p["club"], p.get("poste") == "Gardien")][c] += 1

    lisses = 0
    for p in players:
        if p["id"] not in brut:
            continue
        cle = (p["club"], p.get("poste") == "Gardien")
        mode = groupes[cle].most_common(1)[0][0] if groupes[cle] else None
        # on ne lisse que si le groupe est assez fourni pour faire foi
        retenu = mode if sum(groupes[cle].values()) >= 3 else brut[p["id"]]
        if retenu != brut[p["id"]]:
            lisses += 1
        p["jersey"] = retenu
    return len(brut), lisses


def ref_url_for(p, args):
    if args.use_source_url:
        return p.get("ref_url")
    ref = p.get("ref_file")
    if not ref:
        return None
    return f"{args.base_url.rstrip('/')}/{os.path.basename(ref)}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="", help="prefixe public du dossier refs/")
    ap.add_argument("--use-source-url", action="store_true", help="utilise l'URL lnh.fr telle quelle")
    ap.add_argument("--sref", default=DEFAULT_SREF,
                    help="URL de l'image de style (defaut : celle du rendu valide)")
    ap.add_argument("--no-sref", action="store_true", help="genere sans reference de style")
    ap.add_argument("--recompute-jersey", action="store_true",
                    help="recalcule les couleurs de maillot, y compris celles corrigees a la main")
    ap.add_argument("--ow", type=int, default=OW, help=f"poids de la ref photo (defaut {OW}, plafond conseille 400)")
    ap.add_argument("--sw", type=int, default=SW, help=f"poids de la ref de style (defaut {SW})")
    ap.add_argument("--stylize", type=int, default=STYLIZE, help=f"esthetique propre a MJ (defaut {STYLIZE})")
    ap.add_argument("--texte-allege", action="store_true",
                    help="retire les mots du prompt qui reclament du realisme")
    ap.add_argument("--test", default="",
                    help="ids de joueurs separes par des virgules : balaie la grille de reglages")
    ap.add_argument("--club", default="", help="ne generer que ce club")
    ap.add_argument("--skip-done", action="store_true",
                    help="ecarte les joueurs dont le rendu est deja choisi (image_file)")
    args = ap.parse_args()

    if not args.base_url and not args.use_source_url:
        raise SystemExit("Precise --base-url <url_publique_du_dossier_refs> ou --use-source-url")
    sref = "" if args.no_sref else args.sref

    if not os.path.exists(MANIFEST):
        raise SystemExit("data/roster_s2.json absent : lance d'abord tools/build_manifest_s2.py")
    tous = json.load(open(MANIFEST, encoding="utf-8"))
    players = [p for p in tous if not p.get("sorti")]

    # Couleur du maillot lue sur la ref, puis ecrite dans le manifest pour rester
    # inspectable et corrigeable a la main.
    detectees, lisses = couleurs_par_joueur(players, recompute=args.recompute_jersey)
    if detectees:
        json.dump(tous, open(MANIFEST, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        print(f"maillots : {detectees} detectes, {lisses} alignes sur leur club\n")

    if args.test:
        cheveux = json.load(open(HAIR, encoding="utf-8")) if os.path.exists(HAIR) else {}
        return mode_test([i.strip() for i in args.test.split(",") if i.strip()],
                         players, cheveux, sref, args)

    if args.club:
        players = [p for p in players if slugify(p["club"]) == slugify(args.club)]
    deja = 0
    if args.skip_done:
        avant = len(players)
        players = [p for p in players if not p.get("image_file")]
        deja = avant - len(players)

    # Les refs arrivent club par club au fil des publications LNH : on repart d'un
    # dossier propre a chaque run, sinon les fichiers d'un run precedent trainent et
    # on risque de recoller un club deja genere.
    os.makedirs(PROMPTS_DIR, exist_ok=True)
    for f in os.listdir(PROMPTS_DIR):
        if f.endswith(".txt"):
            os.remove(os.path.join(PROMPTS_DIR, f))

    par_club, sans_ref = {}, []
    for p in sorted(players, key=lambda x: (x["club"], x["nom_famille"], x["prenom"])):
        url = ref_url_for(p, args)
        if not url:
            sans_ref.append(p)
            continue
        par_club.setdefault(p["club"], []).append((p, url))

    # Pas de prefixe numerique : l'index changerait d'un run a l'autre quand un
    # nouveau club recoit ses photos, et on collerait deux fois le meme club.
    # Coiffure et pilosite : decrites a l'oeil depuis les planches contact
    # (tools/build_head_sheets.py), pas mesurables en pixels. Corrigeables a la main.
    cheveux = json.load(open(HAIR, encoding="utf-8")) if os.path.exists(HAIR) else {}
    manquants = [p["id"] for club in par_club for p, _ in par_club[club]
                 if p["id"] not in cheveux]
    if manquants:
        print(f"{len(manquants)} joueurs sans description de coiffure "
              f"(valeur par defaut) : {', '.join(manquants[:6])}"
              + (" ..." if len(manquants) > 6 else ""))
        print("  -> relance tools/build_head_sheets.py --seulement-sans-description\n")

    order, total = {}, 0
    all_lines = []
    for club in sorted(par_club):
        entries = par_club[club]
        lines = []
        for p, url in entries:
            corps = corps_pour(p, cheveux, allege=args.texte_allege)
            prompt = assembler(corps, args.ow, args.sw, args.stylize,
                               negatif=NEGATIF_RENFORCE if args.texte_allege else NEGATIF)
            prompt += f" --oref {url}"
            if sref:
                prompt += f" --sref {sref}"
            lines.append(prompt)
        path = os.path.join(PROMPTS_DIR, f"{slugify(club)}.txt")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
        order[club] = [p["id"] for p, _ in entries]
        all_lines += lines
        total += len(lines)
        print(f"{club:16s} {len(lines):3d} prompts -> {os.path.relpath(path, ROOT)}")

    with open(os.path.join(OUT_DIR, "prompts_all.txt"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(all_lines) + "\n")
    with open(os.path.join(OUT_DIR, "paste_order.json"), "w", encoding="utf-8") as fh:
        json.dump(order, fh, ensure_ascii=False, indent=2)

    print(f"\n{total} prompts generes.")
    print(f"style de reference : {sref or 'AUCUN (--no-sref)'}")
    if deja:
        print(f"{deja} joueurs deja pourvus d'un rendu choisi, ecartes (--skip-done).")
    if sans_ref:
        print(f"{len(sans_ref)} joueurs sans photo de reference, ecartes :")
        for p in sans_ref[:15]:
            print(f"    - {p['id']:32s} {p['nom']} ({p['club']})")
        if len(sans_ref) > 15:
            print(f"    ... et {len(sans_ref) - 15} autres")
    print("\nColle un fichier club a la fois, dans l'ordre des lignes, en mode Relax.")
    print("Ne reordonne pas et ne saute pas de ligne : out/paste_order.json en depend.")


if __name__ == "__main__":
    main()
