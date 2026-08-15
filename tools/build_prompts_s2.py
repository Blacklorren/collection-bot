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
import json
import os
import re
import unicodedata

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MANIFEST = os.path.join(ROOT, "data", "roster_s2.json")
OUT_DIR = os.path.join(ROOT, "out")
PROMPTS_DIR = os.path.join(OUT_DIR, "prompts")

# --- Prompt valide en test, a ne pas retoucher sans refaire une passe de controle ---
# --ar 3:4 : plus proche du canvas 4:5 (992x1240) que 2:3, et _crop_to_ratio() du
# renderer recadre proprement le reste. --seed fige pour homogeneiser la lumiere
# et la pose sur toute la collection.
# Image de style de reference, uploadee sur Midjourney. Le --sw 100 present dans le
# prompt ne veut rien dire sans elle : c'est elle qui porte le rendu valide en test.
# En dur volontairement, pour qu'un rerun sans le flag ne fasse pas silencieusement
# deriver le style de la collection.
DEFAULT_SREF = "https://s.mj.run/Dq9JJbmxWzE"

PROMPT = (
    "Semi-realistic anime bust portrait illustration, comic book style, chest-up framing. "
    "Athletic man wearing a handball jersey, both shoulders clearly visible, cropped at high-chest. "
    "Clean confident dark ink linework with fine cross-hatching in the shadows. "
    "Cel-shaded rendering: flat shapes of color with hard shadow edges, minimal blending, limited palette. "
    "Warm peach-orange light on the skin from the upper front, cool desaturated slate-blue shadows on the opposite side. "
    "Realistic facial structure and proportions, textured stubble, subtle forehead lines, defined cheekbones. "
    "Neutral confident expression, mouth closed, direct eye contact. "
    "Front-facing, eye-level camera, subject centered with clear space above the head. "
    "Flat plain light grey background, completely empty. "
    "Soft even lighting, moderate contrast, crisp and clean. "
    "--no headshot, extreme close-up, cropped at neck, floating head, photorealistic, 3D render, "
    "neon, dark background, bokeh, detailed background, smile, text, watermark "
    "--ar 3:4 --seed 1234 --sw 100 --ow 550 --v 7 --stylize 50"
)


def slugify(s):
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Za-z0-9]+", "-", s).strip("-").lower()


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
    ap.add_argument("--club", default="", help="ne generer que ce club")
    ap.add_argument("--skip-done", action="store_true",
                    help="ecarte les joueurs dont le rendu est deja choisi (image_file)")
    args = ap.parse_args()

    if not args.base_url and not args.use_source_url:
        raise SystemExit("Precise --base-url <url_publique_du_dossier_refs> ou --use-source-url")
    sref = "" if args.no_sref else args.sref

    if not os.path.exists(MANIFEST):
        raise SystemExit("data/roster_s2.json absent : lance d'abord tools/build_manifest_s2.py")
    players = [p for p in json.load(open(MANIFEST, encoding="utf-8")) if not p.get("sorti")]
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
    order, total = {}, 0
    all_lines = []
    for club in sorted(par_club):
        entries = par_club[club]
        lines = []
        for p, url in entries:
            prompt = f"{PROMPT} --oref {url}"
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
