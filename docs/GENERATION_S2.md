# Génération des portraits Saison 2 — mode d'emploi

État au 15 août 2026 : **115 refs disponibles sur 259 joueurs**, dont 7 clubs complets.

| Club | Refs | Prompts |
|---|---|---|
| Aix | 16/16 | `out/prompts/aix.txt` |
| Limoges | 16/16 | `out/prompts/limoges.txt` |
| Nantes | 17/17 | `out/prompts/nantes.txt` |
| Paris | 18/18 | `out/prompts/paris.txt` |
| Saint-Raphaël | 15/15 | `out/prompts/saint-raphael.txt` |
| Saran | 16/16 | `out/prompts/saran.txt` |
| Tremblay | 15/15 | `out/prompts/tremblay.txt` |
| Cesson-Rennes | 1/14 | `out/prompts/cesson-rennes.txt` |
| Chambéry | 1/18 | `out/prompts/chambery.txt` |
| **Total** | **115** | **115 prompts** |

Caen, Chartres, Dunkerque, Montpellier, Nîmes, Sélestat et Toulouse : 0 photo publiée.
Relancer `fetch_lnh_s2.py` toutes les semaines.

**Source des données.** Le scraper interroge `clubs-effectif?team=<clef>` et non les
pages `/equipes/<slug>` : seule la première expose le **centre de formation**, où se
trouvent les jeunes que ton xlsx retient mais que la page club ignore. Le contenu
arrive par un POST sur `/ajaxpost1` dont tous les paramètres (`teams_id`,
`seasons_id`, `univers`) sont lus dans la page — rien n'est codé en dur, le script
suivra donc le changement de saison tout seul. C'est ce qui fait passer les postes de
252 à **255/255** et supprime les joueurs non rapprochés.

---

## Étape 0 — publier les refs (obligatoire, sinon rien ne marche)

Midjourney lit les `--oref` par URL publique. Les prompts pointent sur le raw GitHub
du repo, donc les fichiers doivent y être **poussés** avant le premier collage.

```bash
git add refs data/roster_s2.json data/lnh_aliases.json assets/logos tools docs .gitignore && git commit -m "Saison 2 : refs LNH, manifest joueurs et outillage de generation" && git push
```

Vérifie qu'une URL répond avant d'aller plus loin — ouvre celle-ci dans un navigateur,
tu dois voir le portrait de Gustaf Banke :

```
https://raw.githubusercontent.com/Blacklorren/collection-bot/main/refs/banke-gustaf.png
```

Si tu obtiens un 404, Midjourney renverra une erreur sur les 111 prompts. `refs/` pèse
24 Mo aujourd'hui, ~55 Mo une fois les 255 joueurs couverts : c'est acceptable pour
GitHub, mais si tu veux alléger, passer les refs en JPEG q85 diviserait par cinq.

---

## Étape 1 — le pilote sur Aix, avant tout collage de masse

**Ne colle pas les 111 d'un coup.** Fais d'abord les 14 lignes de `out/prompts/aix.txt`
et regarde le résultat. Ça valide trois choses d'un coup, et ça coûte 15 minutes contre
plusieurs heures de rendus à jeter.

Les 111 prompts embarquent la référence de style `https://s.mj.run/Dq9JJbmxWzE`, celle
du rendu validé — c'est elle qui donne son sens au `--sw 100`. Elle est codée en dur
dans `DEFAULT_SREF` en tête de `tools/build_prompts_s2.py` : une regénération sans le
flag la conserve, et il faut un `--no-sref` explicite pour s'en passer. Si tu changes
de style en cours de saison, change cette constante, pas la ligne de commande, sinon la
collection se retrouvera à cheval sur deux esthétiques.

Vérifie sur ces 14 :
- **cadrage** — buste avec les deux épaules, pas de tête flottante ni de coupe au cou
- **ressemblance** — `--ow 550` est agressif, les visages doivent être reconnaissables
- **fond** — gris clair uni et vide, c'est ce qui rendra le détourage rembg propre

**Et note comment Midjourney nomme les fichiers téléchargés.** Les 111 prompts sont
textuellement identiques (le nom du joueur n'y figure pas, seule l'URL du `--oref`
change), donc c'est l'ordre qui fait le lien avec les joueurs. Envoie-moi la liste des
noms de fichiers du lot Aix : si l'URL de la ref y apparaît, je remplace le tri par
ordre par un rapprochement exact et le risque de décalage disparaît.

---

## Étape 2 — la session de collage

Réglages Midjourney : **mode Relax** (illimité sur Pro — `--oref` coûte 2× le GPU,
autant ne pas taper dans tes 30 h de Fast). Version 7, déjà forcée par `--v 7`.

Un fichier club à la fois, ligne par ligne dans la barre imagine, **dans l'ordre du
fichier**. Trois règles qui conditionnent tout le rapprochement en aval :

1. ne réordonne pas les lignes
2. ne saute aucune ligne
3. termine un club avant d'en commencer un autre

Si un job échoue ou si tu relances une ligne, note-le : ça décale le club, et il faudra
le recaler d'un cran dans le picker (touche `[` ou `]`).

Compte 111 prompts → **444 images**. La file Relax tourne en tâche de fond, tu peux
partir et revenir.

---

## Étape 3 — récupération des rendus

Dans la galerie web (`midjourney.com/imagine`, pas la beta) : sélection multiple par
clic-glissé ou shift-clic, puis téléchargement. Ça sort des zips de 50 images.

Range les images décompressées **un sous-dossier par club**, nommé avec le même slug
que les fichiers de prompts :

```
C:/Users/quent/Downloads/mj/
    aix/          (56 images = 14 joueurs x 4)
    limoges/      (64 images)
    nantes/       (68 images)
    ...
```

C'est ce découpage qui permet au picker de savoir quel joueur correspond à quelle image.

---

## Étape 4 — le picker

```bash
py tools/pick_renders.py --downloads "C:/Users/quent/Downloads/mj"
```

Ça ouvre `http://localhost:8765` : une ligne par joueur, le portrait officiel LNH à
gauche, les 4 rendus à droite. Le manifest est écrit à chaque choix, il n'y a rien à
enregistrer.

| Touche | Effet |
|---|---|
| `1`–`9` | choisir ce rendu et passer au joueur suivant |
| `←` `→` | naviguer |
| `0` | effacer le choix |
| `[` `]` | recaler tout le club d'un cran (si les visages ne correspondent pas) |
| `Entrée` | sauter au prochain joueur non traité |

**Le recalage est le filet de sécurité.** Si la photo de gauche et les rendus de droite
ne montrent pas le même joueur, c'est que le club a glissé : appuie sur `[` ou `]`
jusqu'à ce que ça colle, le décalage est mémorisé pour ce club.

Si tu télécharges les grilles 2×2 au lieu des upscales séparés, ajoute
`--per-player 1`.

Compte 3 secondes par joueur, soit une quinzaine de minutes pour les 111.

---

## Étape 5 — quand les autres clubs sortent

```bash
py tools/fetch_lnh_s2.py
py tools/build_prompts_s2.py --base-url https://raw.githubusercontent.com/Blacklorren/collection-bot/main/refs --skip-done
git add refs data/roster_s2.json && git commit -m "Refs LNH : nouveaux portraits" && git push
```

`--skip-done` écarte les joueurs dont le rendu est déjà choisi : tu ne regénères que
les nouveaux. Le dossier `out/prompts/` est vidé à chaque run, il ne contient donc que
ce qu'il te reste à coller.

---

## Points de vigilance

**Les trois joueurs introuvables sont réglés.** Alexiou STAVROS, Alexandre BARADAT et
Dimitri CLAUDE sont en centre de formation ; ils apparaissent depuis le passage à
`clubs-effectif`. Stavros a son portrait officiel LNH. Les deux autres sont encore en
silhouette côté LNH, donc leur ref vient de `data/refs_manuelles.json` :

- **Baradat** — portrait studio du site de Cesson, équivalent à un portrait LNH.
- **Claude** — recadré dans une photo de signature du site de Chambéry, où il n'est pas
  seul. Le visage est net et de face, mais **il porte un t-shirt d'entraînement gris et
  non le maillot du club** : avec `--ow 550` le rendu risque de reprendre cette tenue.
  Regarde-le en premier dans le picker.

Ce mécanisme est un vrai repli : dès que la LNH publie le portrait officiel d'un de ces
joueurs, il reprend automatiquement la main sur la ref manuelle.

**Quatre joueurs ont été ajoutés hors xlsx**, via `data/roster_complements.json` — sans
ce fichier, un `build_manifest_s2.py` les effacerait et perdrait leur rareté :

| Joueur | Club | Rareté |
|---|---|---|
| Vanja ILIC | Aix | Rare *(transféré de Chartres)* |
| Antoine BZDYNGA | Caen | Commun |
| Samuel VEDIE-MARCONNES | Caen | Commun |
| Josip ZAJA | Chambéry | Commun |

Théophile CAUSSE est encore listé par la LNH à Cesson-Rennes mais ne fait plus partie de
l'équipe : il est volontairement écarté, et la raison est consignée dans la clé
`ignores` du même fichier pour ne pas avoir à re-trancher à chaque comparaison.

Le roster passe donc à **259 joueurs** : Commun 95, Peu Commun 78, Rare 51, Épique 26,
Légendaire 9.

**Pol VALERO ROVIRA** (Montpellier) a été rapproché de `POL VALERA ROVIRA` par le
matching flou. C'est très probablement le même joueur avec une coquille dans ton xlsx,
mais ça vaut une vérification.

**Les raretés ne sont pas encore dans `cards.json`.** Elles sont lues et stockées dans
`data/roster_s2.json`, mais la fusion vers `cards.json` et la génération des cutouts
restent à faire une fois les rendus choisis.
