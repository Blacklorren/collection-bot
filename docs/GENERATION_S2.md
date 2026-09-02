# Génération des portraits Saison 2 — mode d'emploi

État au 2 septembre 2026 : **256 refs disponibles sur 259 joueurs**, 16 clubs sur 16.
La LNH a publié les portraits des trois derniers clubs — Caen, Chartres et Dunkerque —
soit 46 refs de plus. La collecte des refs est donc terminée, à trois joueurs près.

**Rendus retenus : 161 sur 259**, dix clubs complets : Aix, Limoges et Nantes (générés
avec le prompt « Semi-realistic anime »), puis Saran, Paris et Saint-Raphaël, enfin
Cesson-Rennes, Chambéry, Nîmes et Tremblay (prompt « Stylized comic book » actuel).

Restent donc **98 joueurs sans rendu choisi**, en trois états distincts :

| Club | Refs | Où ça en est | Fichier |
|---|---|---|---|
| Caen | 17/18 | **à coller** | `out/prompts/caen.txt` |
| Chartres | 14/15 | **à coller** | `out/prompts/chartres.txt` |
| Dunkerque | 15/15 | **à coller** | `out/prompts/dunkerque.txt` |
| Sélestat | 17/17 | collé, rendus téléchargés — **à trier** | `Downloads/mj/selestat/` |
| Toulouse | 15/16 | collé, rendus téléchargés — **à trier** | `Downloads/mj/toulouse/` |
| Montpellier | 17/17 | collé ? aucun rendu trouvé dans `Downloads/mj` | `out/prompts/montpellier.txt` |

`--skip-done` écarte les rendus déjà choisis, pas les prompts déjà collés : les fichiers
de Sélestat, Toulouse et Montpellier sont donc régénérés à chaque run alors qu'ils n'ont
plus à être collés. **Ne recolle que les trois premières lignes du tableau.**

**Trois joueurs restent sans portrait** et sont écartés de la génération :

| Joueur | Club | Pourquoi |
|---|---|---|
| Samuel VEDIE-MARCONNES | Caen | absent de l'effectif LNH (ajouté via `roster_complements.json`) |
| Oussama HOSNI | Chartres | fiche LNH encore en silhouette |
| Pontus BROLIN | Toulouse | fiche LNH encore en silhouette |

Pour les débloquer sans attendre la LNH : une URL de portrait trouvée sur le site du
club, ajoutée à `data/refs_manuelles.json`, suffit (voir « Points de vigilance »). Sinon
`publier_s2.py` les ignorera et la saison partira à 256 cartes.

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

**La couleur du maillot est injectée dans chaque prompt.** Elle n'est pas codée en dur
par club : `tools/jersey_colors.py` la lit sur la photo de référence elle-même — fond
détouré par remplissage connexe depuis les bords (pour qu'un maillot blanc ne parte pas
avec le fond), peau filtrée, pixels du torse nommés puis comptés. Le résultat est
ensuite lissé par club **en séparant les gardiens**, dont le maillot diffère réellement.
La valeur est stockée dans le champ `jersey` du manifest : si une couleur est fausse,
corrige-la à la main, un rerun la respectera. `--recompute-jersey` force le recalcul.

**La coiffure et la pilosité sont décrites joueur par joueur.** Contrairement à la
couleur du maillot, elles ne sont pas mesurables en pixels : rasé, bouclé, chignon,
dreadlocks, calvitie, ça se regarde. `tools/build_head_sheets.py` assemble des planches
contact de 12 têtes cadrées, numérotées et légendées ; les descriptions sont stockées
dans `data/hair.json` et corrigeables à la main. Pour les futurs clubs :

```bash
py tools/build_head_sheets.py --seulement-sans-description
```

Au 2 septembre, `data/hair.json` couvre les **256 joueurs qui ont une ref** : les 46 de
Caen, Chartres et Dunkerque ont été décrits depuis quatre planches, après les 95 de la
vague du 30 août. La collecte des refs étant finie, la commande ci-dessus ne sortira
plus rien tant que les trois derniers joueurs n'ont pas de portrait.

Deux conséquences sur le prompt d'origine :

- `Athletic man` devient `Athletic man with long black dreadlocks` — la coiffure est
  placée tôt, là où Midjourney lui donne le plus de poids.
- **`textured stubble` a disparu.** Il était figé dans le prompt et imposait de la barbe
  aux joueurs glabres, ce qui est une source de divergence en soi. Il est remplacé par
  la pilosité réelle : `clean-shaven`, `a thick black beard`, `a thin moustache`… Pour
  revenir en arrière, remets la chaîne en dur à la place de `{barbe}`.

Les prompts embarquent la référence de style `https://s.mj.run/Dq9JJbmxWzE`, celle
du rendu validé — c'est elle qui donne son sens au `--sw 100`. Elle est codée en dur
dans `DEFAULT_SREF` en tête de `tools/build_prompts_s2.py` : une regénération sans le
flag la conserve, et il faut un `--no-sref` explicite pour s'en passer. Si tu changes
de style en cours de saison, change cette constante, pas la ligne de commande, sinon la
collection se retrouvera à cheval sur deux esthétiques.

Vérifie sur ces 14 :
- **cadrage** — buste avec les deux épaules, pas de tête flottante ni de coupe au cou
- **ressemblance** — `--ow 300` est un compromis, les visages doivent rester
  reconnaissables sans virer à la photo
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

Le tri se faisant directement dans Midjourney, il n'y a qu'**une image par joueur** à
la sortie. Le picker ne sert donc plus à départager 4 rendus mais à garantir que chaque
image tombe sur le bon joueur — ce qui est justement plus risqué dans ce sens, puisque
l'ordre de téléchargement ne suit plus l'ordre de collage.

```bash
py tools/pick_renders.py --downloads "C:/Users/quent/Downloads/mj" --per-player 1
```

`--per-player 1` bascule en **mode banque** : chaque joueur se voit proposer les 16
images du club, pas seulement celle que l'ordre lui attribuerait. Une image déjà prise
est grisée et porte le nom de son joueur, donc l'affectation reste lisible.

La marche à suivre : `P` pré-affecte tout le club dans l'ordre, puis tu descends les
lignes et tu corriges celles où le visage de gauche ne correspond pas. Si l'ordre était
bon, il n'y a rien à faire ; s'il était mélangé, tu ne reprends que les fautives.

Un compteur en haut signale en rouge toute image affectée à deux joueurs : avec 16
images pour 16 joueurs, c'est forcément une erreur, et elle laisse quelqu'un sans rendu.
Le message s'affiche aussi à l'arrêt du serveur.

Ça ouvre `http://localhost:8765` : une ligne par joueur, le portrait officiel LNH à
gauche, les rendus à droite. Le manifest est écrit à chaque choix, il n'y a rien à
enregistrer.

| Touche | Effet |
|---|---|
| `1`–`9` | choisir ce rendu et passer au joueur suivant |
| `←` `→` | naviguer |
| `0` | effacer le choix |
| `[` `]` | recaler tout le club d'un cran (mode 4 rendus uniquement) |
| `P` | pré-affecter les images restantes du club dans l'ordre |
| `Entrée` | sauter au prochain joueur non traité |

**La photo de référence à gauche est le filet de sécurité.** Tant qu'elle montre le même
joueur que l'image retenue à droite, l'affectation est bonne. C'est la seule vérification
qui compte, et elle est visuelle.

Compte 3 secondes par joueur, soit une quinzaine de minutes pour les 115.

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

## Le réglage `--ow`, seul paramètre qui a bougé

C'est le poids de la photo de référence face au style demandé. Son histoire tient en
trois valeurs, et elle explique les écarts d'esthétique entre clubs déjà rendus :

| Valeur | Quand | Effet | Clubs générés ainsi |
|---|---|---|---|
| 550 | à l'origine | au-dessus du plafond conseillé par MJ (400) : la photo prime sur le style, rendus photoréalistes | Aix, Limoges, Nantes |
| 100 | 17 août | défaut MJ : le style comic s'impose, mais la ressemblance devient trop lâche | Saran, Paris |
| **300** | **25 août** | **compromis retenu (option C de la grille de test)** | **Saint-Raphaël, puis tout le reste** |

Le 25 août, `300` avait été passé en ligne de commande sans toucher la constante `OW`
de `tools/build_prompts_s2.py`, restée à `100` : n'importe quel rerun serait
silencieusement reparti sur l'ancien réglage. **La constante vaut désormais `300`**, un
`build_prompts_s2.py` sans argument produit donc la bonne valeur. Pour explorer autre
chose, `--ow`, `--sw` et `--stylize` restent surchargeables, et `--test <ids>` balaie la
grille complète sur un joueur donné.

---

## Points de vigilance

**Les trois joueurs introuvables sont réglés.** Alexiou STAVROS, Alexandre BARADAT et
Dimitri CLAUDE sont en centre de formation ; ils apparaissent depuis le passage à
`clubs-effectif`. Stavros a son portrait officiel LNH. Les deux autres sont encore en
silhouette côté LNH, donc leur ref vient de `data/refs_manuelles.json` :

- **Baradat** — portrait studio du site de Cesson, équivalent à un portrait LNH.
- **Claude** — recadré dans une photo de signature du site de Chambéry, où il n'est pas
  seul. Le visage est net et de face, mais **il porte un t-shirt d'entraînement gris et
  non le maillot du club** : le rendu risque de reprendre cette tenue. Son champ
  `jersey` a donc été forcé à la main sur la couleur de Chambéry (idem Baradat sur
  celle de Cesson), le prompt réclame bien le maillot du club — mais regarde-le
  quand même en premier dans le picker.

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
