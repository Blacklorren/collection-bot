# Refonte visuelle des cartes — Layout v2 (handoff)

Point d'entrée pour reprendre la refonte des cartes dans une nouvelle session.
Tout le contexte nécessaire est ici (ne dépend d'aucune mémoire locale).

## État actuel (déjà en prod sur `main`)
- `utils/card_renderer.py` rend une carte **layout v1** (bordure noire → cadre métallique
  couleur rareté → portrait **plein cadre** → plaque de nom noire). Branché sur la grille
  `/collection` (`utils/album_generator.py`) et le reveal `/ouvrir` (`cogs/collection_cog.py`),
  avec cache disque `assets/card_cache/` (clé = `DESIGN_VERSION` + id), fallback image brute.
- Assets présents : `assets/fonts/` (Anton + Oswald, OFL), `assets/logos/<slug>.png` (16 logos LNH).

## Objectif : passer au layout v2 (validé avec l'utilisateur)
Référence d'implémentation **déjà écrite et validée visuellement** :
`tools/prototype_layout_v2.py` → fonction `compose_v2(cutout, nom, club, rarete, poste)`.

Spéc du layout v2 :
- **Silhouette à coins biseautés** : coins coupés à 45° en **bas-gauche** et **haut-droite**
  (les deux autres restent droits). Bordure noire **épaisse (BORDER=42)** d'épaisseur
  **uniforme y compris sur la diagonale** (offset perpendiculaire — voir `_inner_poly`).
  Taille du biseau `CHAMFER=140`.
- **Fond intérieur = dégradé de la couleur de rareté**, avec le **joueur détouré** posé dessus
  (fond du portrait supprimé), + ombre dégradée en bas pour lisibilité du texte.
- **Bas-gauche** : écusson du club sur **disque blanc** (contraste constant, même logos foncés).
- **Bas-droite** : **nom** en grand (Anton, auto-réduit si trop long) + **poste** dessous
  (Oswald, plus petit), alignés à droite.
- Géométrie/couleurs/polices réutilisent `utils/card_renderer.py` (`W,H,RARITY_RGB`, `anton`,
  `oswald`, `_vgradient`, `lighten`, `darken`, `slugify`, `LOGOS`).

Réglage resté ouvert : **cadrage du joueur** (plan serré actuel vs léger dézoom pour voir plus
de fond). À confirmer avec l'utilisateur au moment d'intégrer.

## Les 3 chantiers à réaliser

### A. Détourage des portraits (offline, one-shot)
- Script : `tools/build_cutouts.py` (`pip install rembg onnxruntime pillow`).
- Produit `assets/cutouts/<id>.webp` (RGBA transparent, ~240 Ko/carte, **~63 Mo** au total).
- Qualité validée sur 6 portraits variés (rembg u2net + alpha matting) : contours propres,
  pas de halo, OK même cheveux clairs sur fond clair.
- **Ne jamais mettre rembg dans les deps du bot / sur Railway** (lourd). C'est du dev-only.
- **Décision de stockage à trancher** (recommandation : commit WebP, ou git-LFS si clone léger
  souhaité, ou hébergement externe + `cutout_url`). Par défaut `assets/cutouts/` est **gitignoré**
  pour éviter un commit accidentel : retirer la ligne du `.gitignore` quand on décide de committer.

### B. Postes des joueurs (offline, one-shot)
- Script : `tools/scrape_postes.py` (stdlib only).
- Scrape les effectifs LNH (`/liquimoly-starligue/equipes/<slug>`, HTML statique,
  motif `<div class="name">…</div><div class="description">…</div>`), filtre le staff,
  mappe aux cartes **par nom normalisé** (sans accents, majuscules).
- Produit `data/postes.json` (`{id: poste}`) + **liste console des non-matchés** à corriger
  à la main (différences de nom/accents). `CLUB_PAGES` contient déjà les 16 slugs clubs.
- « Légendes Starligue » n'a pas de page club → postes à remplir à la main si on en veut.
- Étape suivante : fusionner les postes retenus dans `cards.json` (champ `poste`) — non fait
  automatiquement par le script (volontaire).

### C. Intégration dans le renderer + bot
1. Porter `compose_v2` dans `utils/card_renderer.py` (remplace/complète `compose`).
2. `get_card_bytes` doit charger le **cutout** `assets/cutouts/<id>.webp` (au lieu de télécharger
   le portrait brut). **Fallback** : si le cutout manque, retomber sur le portrait brut plein cadre.
3. Lire le **poste** depuis `cards.json` (`card.get('poste', '')`).
4. **Bumper `DESIGN_VERSION`** dans `card_renderer.py` pour invalider `assets/card_cache/`.
5. Rien à changer côté `album_generator.py` / reveal : ils consomment `get_card_bytes`.

## Ordre conseillé
1. `python tools/build_cutouts.py` (long, one-shot) → vérifier quelques `.webp`.
2. `python tools/scrape_postes.py` → relire `data/postes.json` + corriger les non-matchés,
   puis fusionner les `poste` dans `cards.json`.
3. Faire valider le rendu v2 sur 2-3 cartes via `tools/prototype_layout_v2.py`
   (le cadrage du joueur notamment), ajuster si besoin.
4. Intégrer (chantier C), bumper `DESIGN_VERSION`, tester `/collection` et `/ouvrir` en live.
5. Décider du stockage des cutouts et committer en conséquence.

## Pièges / notes d'environnement
- Repo git = `E:\Bot Handnews\collection-bot` (PAS le parent). Commits directs sur `main`,
  push seulement si demandé. Remote : https://github.com/Blacklorren/collection-bot
- Le `python`/`py` système n'a **pas `aiohttp`** : les tests purs (compose, scraping stdlib)
  tournent en local, mais tout ce qui importe aiohttp doit tourner dans l'env réel du bot.
  Pour le détourage, installer rembg/onnxruntime en local.
- Portraits `image_url` parfois en `.gif` animé (légendaires) → `.convert("RGBA")` prend la 1re frame.
- `cards.json` : champs `id, nom, club, rarete, image_url` (+ `poste` à ajouter).
- Données de cartes : ~270, 16 clubs réels + set « Légendes Starligue ».
- Le prototype jetable de mise au point était dans `proto_out/` (gitignoré, peut être absent).
