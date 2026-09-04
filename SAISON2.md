# Saison 2 — Échanges, Recyclage sélectif & Duels

Document de reprise. Tout est **bêta-gaté** (testable par l'admin dans le salon de
test, ouverture publique automatique le **25 août 2026**). Voir `beta.py`.

---

## 1. État d'avancement

| # | Chantier | Fichiers | Statut |
|---|----------|----------|--------|
| 1 | Outil postes (xlsx + ré-injection) | `tools/generate_postes_xlsx.py`, `tools/inject_postes.py` | ✅ |
| 2 | Gating bêta | `beta.py` | ✅ |
| 3 | Migration DB | `database.py` (elo, `trade_log`, `duels` + fonctions) | ✅ testé |
| 4 | Échange de cartes | `cogs/trade_cog.py` | ✅ testé |
| 4b| Recyclage sélectif (A+B) | `cogs/collection_cog.py`, `database.remove_extra_copies` | ✅ testé |
| 5 | Moteur de duel | `duel_engine.py` | ✅ testé Monte-Carlo |
| 5 | DB duels | `database.py` | ✅ testé |
| 5 | Cog duel (baseline auto) | `cogs/duel_cog.py` | ✅ compile · ⚠️ à tester en vrai |
| 5 | **Composition MANUELLE** | `cogs/duel_cog.py` (`DuelLineupView`/`LineupPicker`) | ✅ compile · ⚠️ à tester en vrai |
| 6 | Corrections revue (verrous, annulation, off-by-one) | `cogs/duel_cog.py` | ✅ compile · ⚠️ à tester en vrai |
| 6 | Bande Elo douce (hors bande = K et gains réduits) | `duel_engine.py`, `cogs/duel_cog.py` | ✅ testé hors-ligne |
| 6 | UX : compo préremplie, narration, `/historique_duel` | `cogs/duel_cog.py`, `database.py` | ✅ compile · ⚠️ à tester en vrai |
| 7 | **Duel ASYNCHRONE** (plus d'acceptation, défense auto, Elo à sens unique) | `duel_engine.py`, `database.py`, `cogs/duel_cog.py` | ✅ testé hors-ligne · ⚠️ à tester en vrai |

**Postes : déjà présents dans `cards.json`** (champ `poste` en toutes lettres, ex.
« Gardien », « Demi Centre »). `duel_engine.normalize_poste()` les mappe vers les
codes de slot. L'outil `postes_a_remplir.xlsx`/`inject_postes.py` n'est donc **plus
nécessaire**. 3 cartes jouables n'ont pas de poste (ORAEI, ABDELHAK, PETERSEN) →
pas de bonus de poste pour elles, sans gravité.

---

## 2. Design figé (constantes : `duel_engine.py`)

**Notes par rareté** (échelle compressée, Lég ≈ 5× Commun) :
`Commun 3 · Peu Commun 5 · Rare 8 · Épique 12 · Légendaire 16` (Noël non jouable).

- **Bonus de poste** : ×1.4 si la carte est sur son poste naturel.
- **Slot vide** : note = Commun (3), sans club ni bonus.
- **Synergie de club** (plus gros groupe de même club aligné) :
  `2→×1.05 · 3→×1.12 · 4→×1.20 · 5→×1.30 · 6→×1.42 · 7→×1.55`.
- **Puissance équipe** = (Σ notes) × synergie.
- **Simulation** : 50 possessions, conversion ~55 % modulée par la puissance
  relative, variance « forme du jour » (±12 %), mort subite si égalité.
- **Elo** : départ 1000, K=32, **bande DOUCE** ±150 (env `DUEL_ELO_BAND`) —
  un classé hors bande reste possible mais K passe à 8 (env `DUEL_SOFT_K`,
  const `ELO_K_SOFT`) et les récompenses sont réduites de moitié
  (`SOFT_REWARD_FACTOR = 0.5`). Plus de blocage sec.
- **Elo À SENS UNIQUE** (`elo_apply_attacker`) : seul l'**attaquant** met son Elo
  en jeu. Le défenseur n'a pas choisi ce match et n'était pas là pour le jouer —
  son Elo ne bouge jamais. L'espérance reste calculée contre l'Elo du défenseur,
  donc le barème se charge lui-même de l'anti-farm.
- **Récompenses** (classé) : attaquant +100 pts scalés ×0.25→×2 selon l'écart
  d'Elo s'il gagne, +20 de consolation sinon (le tout ×0.5 hors bande).
  **Défenseur absent** : rien s'il perd (on ne punit pas une absence),
  `DEFENSE_HOLD_POINTS = 35` si sa défense tient, `DEFENSE_DRAW_POINTS = 10` sur
  un nul. Anti-farm : max 3 attaques classées/jour **d'un joueur vers une même
  cible** (`DUEL_DAILY_PAIR_CAP`, directionnel : subir n'empêche pas de riposter),
  max 10 attaques récompensées/jour (`DUEL_DAILY_REWARD_CAP`), max 5 défenses
  récompensées/jour (`DUEL_DEFENSE_REWARD_CAP`) et 5 MP de compte-rendu
  (`DUEL_DEFENSE_DM_CAP`). Comparaison stricte `<` : le compte exclut le duel
  en cours. Le plafond de défense est calibré sur `PACK_COST = 150` — une journée
  de défense parfaite vaut ~1 pack, pas de revenu passif.

**Équilibrage validé** (`py -3 tools/test_duel_balance.py`, 10 000 matchs) :
7 Lég dépareillées battent 1 Lég+mixte même club seulement ~63 % du temps ;
7 Épiques même club battent 7 Lég dépareillées ~73 % → **le collectif prime**.

Les 7 postes / slots : `GB · ALG · ARG · DC · PIV · ARD · ALD`.

---

## 3. Duel ASYNCHRONE + composition manuelle — ✅ FAIT

Implémenté dans `cogs/duel_cog.py` (pattern repris de `TradePicker`).

### Pourquoi asynchrone
Exiger deux joueurs connectés en même temps rendait le duel quasi injouable sur un
Discord où personne n'est là aux mêmes heures. **On attaque désormais qui on veut,
dès que la cible possède au moins une carte jouable** — c'est le seul prérequis.
`DuelChallengeView` (accepter / refuser) a disparu : il n'y a plus rien à accepter.

### L'équipe du défenseur absent
C'est **sa compo automatique** (`defense_lineup` → `auto_lineup`) : ses meilleures
cartes de la saison en cours, une par poste. Ce choix a trois vertus :
- **aucune action requise** — tout joueur possédant des cartes est défiable
  immédiatement, sans avoir jamais ouvert un menu ;
- **impossible à saboter** — personne ne peut laisser une défense fantoche pour
  offrir des victoires à ses amis ;
- **auto-entretenue** — elle se renforce toute seule à chaque pack ouvert, ce qui
  relie directement la collection à la défense (`/ma_defense` rend ce lien visible).

Elle est **figée au lancement du `/defi`**, pas à la résolution : la puissance
annoncée à l'attaquant est exactement celle qu'il affrontera, même si la cible
ouvre un pack pendant qu'il compose.

Rappel de conception : dans ce moteur les deux compos **n'interagissent pas**
(`simulate_match` compare deux puissances). Il n'y a donc ni contre-pick ni
information à cacher — c'est ce qui rend une défense automatique acceptable, et
c'est aussi pourquoi la puissance de la défense est affichée avant l'attaque.

### Ce que risque le défenseur : rien
- Son **Elo ne bouge jamais** (`elo_apply_attacker`). En base, `elo2_before ==
  elo2_after` : la trace le montre explicitement.
- Il ne **perd jamais de points**. Sa défense lui en **rapporte** quand elle tient,
  et il reçoit un **MP de compte-rendu** après chaque attaque subie (plafonné).
- Conséquence voulue : se faire attaquer est une bonne nouvelle. C'est ce qui rend
  l'asymétrie acceptable pour quelqu'un qui dormait.

### Flux
- `/defi @membre` ouvre directement la **phase de préparation** (`DuelPrepView`) :
  puissance de l'attaquant, puissance de la défense adverse, et boutons
  **« Composer mon équipe »**, **« Attaquer »** et **« Annuler »**.
- La compo de l'attaquant est **préremplie** (`initial_lineup`) : dernière compo
  jouée (cartes encore possédées, via `database.get_last_duel_lineup`), sinon
  compo auto. Le joueur pressé clique directement « Attaquer ».
- Il peut ouvrir son **sélecteur privé** (`LineupPicker`, éphémère) :
  - **select de poste** (`GB…ALD`) — choisit quel slot éditer (montre la carte
    actuelle par slot),
  - **select de club** puis **select de carte** — place n'importe quelle carte
    possédée jouable ; ✓/✗ indique si elle est à son poste (bonus ×1.4),
  - boutons **« Vider le poste »**, **« Compo automatique »** (réutilise
    `auto_lineup()`) et **« Lancer l'attaque »**.
- Le match (`play_match`) part au clic. Il lit `DuelSession.lineup_a/lineup_d`.

### Détails d'implémentation
- Une même carte ne peut occuper qu'un seul slot : la placer la **retire**
  automatiquement de son slot précédent.
- Slots laissés vides autorisés (note plancher Commun via `team_power`).
- Cartes Noël exclues ; dédup par carte (`get_user_collection`).
- Verrou `ACTIVE_DUELISTS` : **seuls les attaquants y figurent**. Un défenseur
  absent n'a rien à verrouiller, et plusieurs joueurs peuvent parfaitement
  attaquer la même cible en parallèle. Libéré sur annulation, timeout (300 s) ou
  fin de match (`try/finally` dans `play_match` : plus de fuite en cas d'exception).
- `DuelPrepView.launch()` est **idempotent** (`launched`) : un double-clic ou un
  clic simultané sur les deux boutons ne joue pas deux duels.
- Annulation/expiration : `DuelSession.cancelled` invalide le sélecteur éphémère
  encore ouvert (plus de « match fantôme » après Annuler).
- **Narration** : le match s'affiche en trois temps (coup d'envoi → mi-temps →
  résultat, via `duel_engine.simulate_match` qui expose le score à la mi-temps
  et le flag mort subite).
- **`/historique_duel [membre]`** : 10 derniers duels (⚔️ attaque / 🛡️ défense,
  V/N/D, score, mode, delta Elo), via `database.get_user_duels`.
- **`/defenses [membre]`** : les 10 dernières attaques subies et le nombre de
  défenses tenues (`database.get_user_defenses`).
- **`/ma_defense`** : consultation de sa propre défense automatique (éphémère).
- `get_last_duel_lineup` ne lit que les lignes où le joueur était `joueur1` :
  reproposer sa compo de défense (générée) écraserait son dernier choix réel.
- `get_duel_leaderboard` n'inclut que les joueurs ayant lancé au moins une
  **attaque** classée — l'Elo ne bougeant qu'à l'attaque, classer un joueur
  purement défensif le figerait à 1000 sans rien vouloir dire. Son bilan
  défensif (`defenses_tenues/defenses`) est affiché à côté de son ratio d'attaque.

---

## 3bis. Points de message : maturation différée — ✅ FAIT

Des membres écrivaient un message, encaissaient les points, l'effaçaient, et
recommençaient : le plafond de la journée tombait en quelques minutes, sans présence
réelle et sans laisser de trace dans le salon.

Le trou n'était pas la suppression mais le **crédit immédiat** : les points étaient
dépensables avant que le message ait eu le temps d'exister, donc n'importe quelle
déduction a posteriori arrivait après la conversion en packs. Corriger la suppression
seule n'aurait rien réglé.

**Mécanique** (`cogs/collection_cog.py`, table `pending_message_points`) :
- à l'écriture, les points partent dans une table d'attente — invisibles dans le solde
  et non dépensables ;
- `mature_points_loop` (60 s) les crédite **12 h plus tard**
  (`POINT_MATURATION_MINUTES = 720`), après avoir revérifié par `fetch_message` que le
  message est toujours là ;
- suppression avant maturité → la ligne disparaît, il n'y a rien à reprendre ;
- suppression après maturité (jusqu'à `POINT_CLAWBACK_HOURS = 48`) → le solde est
  débité, quitte à devenir négatif : `/pack` refuse alors tout achat
  (`affordable = pts // PACK_COST <= 0`).

**Le détail qui décide de tout** : le quota quotidien (`daily_message_points`) est
consommé dès l'écriture et **jamais rendu**. Le restituer à la suppression libérerait
de la place pour regagner et rendrait la triche plus rentable qu'avant.

**Deux contournements fermés** :
- on écoute `on_raw_message_delete` / `on_raw_bulk_message_delete` — les versions non
  RAW ne se déclenchent que pour les messages encore en cache, donc ni ceux d'hier ni
  après un redémarrage ;
- la maturation revérifie l'existence du message, sinon couper le bot au bon moment
  suffisait à perdre l'événement de suppression. En cas de doute (salon introuvable,
  permission, réseau) on crédite : mieux vaut payer un message effacé que spolier
  quelqu'un pour une panne.

**Le cooldown de 10 s a disparu.** Ce qui freine désormais n'est plus le rythme
d'écriture mais l'obligation de laisser ses messages en ligne une demi-journée : un
spam de quinze lignes reste sous les yeux des modérateurs le temps de mûrir, ou il ne
rapporte rien. Le plafond quotidien reste le seul garde-fou sur le volume.

**Limite assumée** : plafonner sa journée reste possible en quelques secondes. Ce
n'est plus discret, c'est tout. Pour l'empêcher vraiment il faudrait étaler les gains
(un gain par fenêtre de N minutes), ce qui n'a pas été retenu.

---

## 4. Tester (en bêta, salon `441230079100715008`)

1. Déployer, lancer le bot, `!sync`.
2. `/echange @autre_compte` — panier des deux côtés, double validation.
3. `/recycler` — version sélective (liste + « Tout recycler »).
4. `/defi @autre_compte` (classé) et `/defi @autre amical:True`, **le second compte
   déconnecté** : compo préremplie → cliquer **« Attaquer »** (ou ajuster via
   **« Composer mon équipe »**) ; le match part immédiatement, avec narration
   coup d'envoi → mi-temps → résultat.
   Cas à tester :
   - le second compte reçoit bien son **MP de compte-rendu**, et ses points
     augmentent si sa défense a tenu ;
   - son **Elo n'a pas bougé** (`/historique_duel` sur lui : pas de delta) ;
   - annuler pendant qu'un picker est ouvert (il doit répondre « attaque annulée ») ;
   - attaque hors bande Elo (note « gains réduits » dans la préparation et l'embed) ;
   - 4ᵉ attaque classée du jour vers la même cible → refus, et vérifier que la
     cible peut quand même attaquer en retour (plafond directionnel) ;
   - attaquer un compte **sans carte jouable** → refus explicite.
5. `/ma_defense`, `/defenses`, `/classement_duel` et `/historique_duel`.
6. Hors testeurs / hors salon → message « arrive la saison prochaine ».

### Tester SEUL (sans second compte)

`/defi` en visant **le bot lui-même** lance un **entraînement** : son équipe est
synthétique, et le match part dès que tu cliques « Attaquer ». Réservé aux
`BETA_TESTER_IDS` (`beta.is_tester`, indépendant de la date : reste fermé au public
après le 25 août). Le paramètre `difficulte` fixe la rareté de son équipe :

| `difficulte` | Puissance moyenne du bot |
|---|---|
| Commun | ~31 |
| Peu Commun | ~51 |
| Rare *(défaut)* | ~83 |
| Épique | ~123 |
| Légendaire | ~161 |

Couvre : picker de composition, compo préremplie, « Compo automatique », narration
mi-temps, embed de résultat, MVP, annulation avec picker ouvert. **Aucune écriture en
base** (pas d'Elo, pas d'historique, pas de ligne `users` pour le bot).

Ne couvre **pas** (il faut un second compte) : la défense automatique d'un vrai
joueur, le MP de compte-rendu, le classé (Elo à sens unique, bande douce,
récompenses, plafonds anti-farm) et tout `/echange`.

**Vérifié hors-ligne** (invariants du duel asymétrique, sans Discord) : l'Elo du
défenseur ne bouge jamais sur 41 duels d'affilée, il ne perd jamais de points et
n'en gagne que si sa défense tient, les plafonds de récompense et de MP mordent,
le double-clic sur « Attaquer » ne joue qu'un duel, le sparring n'écrit rien.
Simulation de ligue (120 joueurs × 60 jours) : corrélation puissance↔Elo **0,976**,
dérive du pool **−15 Elo**, et un « farmer » doté de la meilleure équipe qui ne vise
que les 5 plus faibles finit **22ᵉ/120** avec des points **sous la médiane** — le
barème sanctionne le farm tout seul, avant même les plafonds.

Tests logiques hors-ligne (sans Discord) :
`py -3 tools/test_duel_balance.py` (équilibrage + Elo).

---

## 5. Mise en production / config

- **`.env`** (valeurs par défaut déjà câblées sur l'admin) :
  `PUBLIC_LAUNCH=2026-08-25`, `BETA_TESTER_IDS=133711821214449665`,
  `BETA_CHANNEL_ID=441230079100715008`, et options
  `DUEL_ELO_BAND`, `DUEL_SOFT_K`, `DUEL_DAILY_PAIR_CAP`, `DUEL_DAILY_REWARD_CAP`.
- **Postes** : déjà dans `cards.json` (rien à faire ; l'outil xlsx reste dispo au cas où).
- **Dépendance dev uniquement** : `openpyxl` (scripts `tools/`, pas le runtime).
- Le **25 août**, tout s'ouvre au public automatiquement (aucune manip).

### Bug latent — ✅ traité
Le `datetime.datetime.now()` signalé dans `database.get_journees_for_rappel`
n'existait pas (le code utilisait déjà `datetime.now()`, correct avec l'import
du fichier). La fonction a néanmoins été blindée : paramètre passé en chaîne
(l'adaptateur datetime de sqlite3 est déprécié en Python 3.12+) et
`datetime()` SQLite des deux côtés de la comparaison pour normaliser les
formats ISO. NB : la fonction n'est appelée par aucun cog à ce jour.

---

## 6. Prompt de reprise (copier-coller pour la prochaine session)

> Reprends le chantier Saison 2 du bot `collection-bot` (dans `E:\Bot Handnews\collection-bot`).
> Lis `SAISON2.md`. **Tout le code est en place** (échanges, recyclage, duels avec
> composition manuelle — `DuelLineupView`/`LineupPicker` dans `cogs/duel_cog.py`).
> Il reste à **tester en vrai sur Discord** dans le salon bêta (§4), notamment le
> nouveau flux de composition des duels à deux joueurs (compos préremplies,
> bouton « Prêt » sur le message partagé, narration mi-temps, lancement auto du
> match), l'annulation avec picker ouvert, et un défi classé hors bande Elo
> (gains réduits). Le bug latent `get_journees_for_rappel` est traité (§5).
