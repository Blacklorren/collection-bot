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
| 6 | Bande Elo douce (hors bande = K réduit) | `duel_engine.py`, `cogs/duel_cog.py` | ✅ testé hors-ligne |
| 6 | UX : compo préremplie, narration, `/historique_duel` | `cogs/duel_cog.py`, `database.py` | ✅ compile · ⚠️ à tester en vrai |
| 7 | **Duel ASYNCHRONE** (plus d'acceptation, défense auto, Elo à sens unique) | `duel_engine.py`, `database.py`, `cogs/duel_cog.py` | ✅ testé hors-ligne · ⚠️ à tester en vrai |
| 8 | **Économie : packs en fin de journée** (plus de points par match, 6 matchs/jour, 3 adversaires = 1 pack, 5 = 2 packs) | `duel_engine.py`, `database.py` (`duel_daily_rewards`), `cogs/duel_cog.py` | ✅ testé hors-ligne · ⚠️ à tester en vrai |
| 8 | **Anti-farm : `COUNT(DISTINCT)` sur les adversaires battus** (rebattre la même cible ne compte qu'une fois) | `database.py`, `duel_engine.py`, `cogs/duel_cog.py` | ✅ testé hors-ligne · ⚠️ à tester en vrai |

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
  const `ELO_K_SOFT`). Plus de blocage sec. Les packs, eux, ne dépendent plus de
  l'écart d'Elo : une victoire vaut une victoire, quelle que soit la cible.
- **Elo À SENS UNIQUE** (`elo_apply_attacker`) : seul l'**attaquant** met son Elo
  en jeu. Le défenseur n'a pas choisi ce match et n'était pas là pour le jouer —
  son Elo ne bouge jamais. L'espérance reste calculée contre l'Elo du défenseur,
  donc le barème se charge lui-même de l'anti-farm.
- **Récompenses : des PACKS, en fin de journée** (refonte du 5 septembre 2026).
  Un match ne crédite **plus aucun point** — il ne bouge que l'Elo. Les packs
  tombent une fois par nuit, par **paliers d'ADVERSAIRES DISTINCTS battus en
  attaque** (`DAILY_PACK_LADDER`) : **3 adversaires → 1 pack, 5 → 2 packs**.
  - ⚠️ **« Une victoire » = un adversaire distinct.** Rebattre la même cible dans
    la journée ne compte qu'une fois (`count_beaten_opponents_for`, un
    `COUNT(DISTINCT joueur2)`). C'est **l'anti-farm**, ajouté le 5 septembre 2026
    après mesure : à puissance moitié moindre l'attaquant gagne **99,7 %** du
    temps (20 000 matchs simulés — 0,8 → 82 %, 0,7 → 92 %, 0,6 → 98 %), et un
    joueur moyen contre un débutant est à un ratio de 0,38, soit une certitude.
    Sans le `DISTINCT`, le chemin le plus sûr vers les 2 packs était de matraquer
    trois débutants. Il faut désormais **cinq adversaires différents battus**.
  - **Pourquoi pas un filtre sur l'écart d'Elo** : impossible ici. `set_user_elo`
    n'est appelé que pour l'attaquant — un joueur qui n'attaque jamais reste à
    1000 à vie. L'Elo mesure l'activité offensive, pas la force : une grosse
    collection passive et un débutant sont tous deux à 1000, donc « dans la
    bande », donc indiscernables.
  - **Plafond dur de 6 matchs classés/jour** (`DUEL_DAILY_MATCH_CAP`) : passé ce
    seuil `/defi` refuse le classé et renvoie vers l'amical. Ce n'est plus un
    plafond de récompenses, c'est la longueur de la journée de jeu — et donc ce
    qui borne le revenu à 2 packs, quoi qu'il arrive.
  - **Max 2 attaques classées/jour vers une même cible** (`DUEL_DAILY_PAIR_CAP`,
    directionnel : subir n'empêche pas de riposter). Depuis le `DISTINCT`, ce
    plafond n'est plus l'anti-farm principal : c'est un **droit à la revanche**
    (la 2ᵉ attaque ne rapporte un pack que si la 1ʳᵉ a été perdue) et un
    garde-fou contre le harcèlement d'une seule cible.
  - **Pourquoi 5 sur 6 et non 6 sur 6** : ça laisse exactement **un match de
    marge**. On peut perdre une fois et décrocher quand même les deux packs, ou
    dépenser ce match en revanche sur la cible qui nous a battu (auquel cas elle
    finit dans les cinq). Exiger le sans-faute rendait le palier haut hostile :
    une seule mauvaise rencontre condamnait la journée dès le premier match.
  - **Conséquence à assumer** : il faut **5 joueurs attaquables** sur le serveur
    pour les 2 packs, et 3 pour le premier.
    ⚠️ **En bêta à un seul testeur, aucun pack de duel n'est atteignable** :
    l'entraînement contre le bot n'écrit rien en base. Pour tester la
    distribution, abaisser `DAILY_PACK_LADDER` à la main dans `duel_engine.py`.
  - **Le défenseur ne gagne rien.** Ni Elo (il ne l'a jamais mis en jeu), ni
    pack : sa défense **protège** son classement, elle ne le fait pas monter.
    C'est l'arbitrage qui remplace l'ancien `DEFENSE_HOLD_POINTS` — payer une
    défense qui tient revenait à rémunérer le sommeil. Il garde son MP de
    compte-rendu, toujours plafonné à 5/jour (`DUEL_DEFENSE_DM_CAP`).
  - **Pourquoi ce changement** : l'ancien barème (+100 pts/victoire jusqu'à 10
    attaques, +35/défense jusqu'à 5) cumulé au revenu de messages
    (`DAILY_BONUS = 100` + `MAX_DAILY_MESSAGE_POINTS = 300`) permettait environ
    **10 packs/jour**. Le plafond est désormais de **2 packs** côté duel.

  **Distribution** (`daily_packs_loop`, `cogs/duel_cog.py`) : boucle de polling
  toutes les 15 min (`DUEL_DAILY_PACKS_CHECK_MINUTES`) qui solde les journées
  **écoulées** — jamais celle en cours — et rattrape jusqu'à 7 jours en arrière
  (`DUEL_DAILY_PACKS_CATCHUP_DAYS`). Pas de rendez-vous fixe à minuit : un bot
  redémarré à 00 h 02 le raterait, et personne ne serait payé. L'idempotence est
  garantie en base par la table **`duel_daily_rewards`**, clé primaire
  `(user_id, jour)` : l'insertion du verrou et le crédit des packs sont dans la
  **même transaction**, donc jamais payé à moitié, jamais payé deux fois.
  Les journées sont découpées sur **minuit de Paris**, bornes calculées de date à
  date (`_day_bounds`) et non par `début + 24 h` — les nuits de changement d'heure
  durent 23 h et 25 h.

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
dès lors que les deux camps peuvent aligner une équipe complète** (7 cartes
distinctes de la saison en cours, cf `missing_slots`) — c'est le seul prérequis.
`DuelChallengeView` (accepter / refuser) a disparu : il n'y a plus rien à accepter.

**Le ticket d'entrée à 7 cartes** (ajouté le 5 septembre 2026) : un joueur à trois
cartes défendait avec quatre postes vides, et un poste vide vaut une Commune sans
club ni bonus (`team_power`). Sa défense n'était pas un match mais un cadeau —
l'attaquant y gagnait un adversaire distinct à moindres frais, et lui ne récoltait
que des MP de défaite. La règle vaut **des deux côtés** : on ne peut pas non plus
attaquer avec une équipe incomplète, ce qui serait une défaite garantie et un
match de quota gaspillé. Elle compte des cartes **distinctes**, car `auto_lineup`
dédoublonne par id : trois exemplaires de la même carte ne remplissent qu'un poste.
Seul l'**entraînement contre le bot** échappe à la règle (rien n'y est enregistré,
et on s'entraîne justement pour combler ses trous).

⚠️ **Effet de bord sur les packs** : les cibles éligibles se raréfient. Le palier
haut demande 5 adversaires distincts, donc **5 joueurs avec une équipe complète**
sur le serveur — à surveiller au lancement.

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
- Il ne **perd rien** : ni Elo, ni pack, ni carte. Il reçoit un **MP de
  compte-rendu** après chaque attaque subie (plafonné à 5/jour).
- Il ne **gagne rien** non plus, depuis la refonte du 5 septembre 2026 : une
  défense qui tient protège son classement, elle ne le fait pas monter.
- Conséquence voulue : se faire attaquer pendant son sommeil est **neutre**.
  Jamais une punition — c'est ce qui rend l'asymétrie acceptable — mais jamais
  un revenu passif non plus : les packs se gagnent en attaquant.

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
   - le second compte reçoit bien son **MP de compte-rendu**, et ni ses packs ni
     ses points ne bougent, même quand sa défense a tenu ;
   - son **Elo n'a pas bougé** (`/historique_duel` sur lui : pas de delta) ;
   - annuler pendant qu'un picker est ouvert (il doit répondre « attaque annulée ») ;
   - attaque hors bande Elo (note « gain d'Elo réduit » dans la préparation et
     l'embed) ;
   - 3ᵉ attaque classée du jour vers la même cible → refus, et vérifier que la
     cible peut quand même attaquer en retour (plafond directionnel) ;
   - 7ᵉ attaque classée du jour, toutes cibles confondues → refus avec le bilan
     de la journée et renvoi vers l'amical ;
   - le champ **« 🎁 Packs du jour »** de l'embed de résultat progresse bien
     (`n/6 matchs`, palier suivant) ;
   - **rebattre la même cible** : la préparation affiche l'avertissement `♻️`, et
     l'embed de résultat le `⚠️ Tu avais déjà battu ce joueur aujourd'hui` — le
     compteur d'adversaires ne doit PAS bouger, l'Elo si ;
   - attaquer un compte à **moins de 7 cartes jouables** → refus, avec le nombre
     exact de cartes qui lui manquent ;
   - **attaquer avec** moins de 7 cartes → refus symétrique, renvoi vers `/pack`
     et `/echange` ; mais `/defi` sur le **bot** doit rester possible ;
   - `/ma_defense` sur une collection incomplète → message « 🔒 personne ne peut
     t'attaquer », avec le compte de cartes manquantes ;
   - trois exemplaires d'une même carte ne comptent que pour **une** dans ce total.
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
défenseur ne bouge jamais sur 41 duels d'affilée, il ne perd rien, le double-clic
sur « Attaquer » ne joue qu'un duel, le sparring n'écrit rien.
Simulation de ligue (120 joueurs × 60 jours) : corrélation puissance↔Elo **0,976**,
dérive du pool **−15 Elo**, et un « farmer » doté de la meilleure équipe qui ne
vise que les 5 plus faibles finit **22ᵉ/120**.

> ⚠️ Ces chiffres datent de l'**ancienne** économie (points par match). La partie
> Elo reste valable — le barème Elo n'a pas changé — mais la conclusion « le
> farmer finit avec des points sous la médiane » ne s'applique plus : les points
> de duel n'existent plus. Ce qui borne le farm aujourd'hui, c'est le
> `COUNT(DISTINCT)` sur les adversaires battus, plus les plafonds
> (6 matchs/jour, 2 par cible).
>
> **Point ouvert refermé le 5 septembre 2026** : viser les trois collections les
> plus faibles était la façon la plus sûre d'empocher ses 2 packs. Ce n'est plus
> possible — cinq adversaires *différents* sont requis. Reste un résidu assumé :
> sur un serveur assez peuplé, on peut toujours choisir les cinq plus faibles.
> Les leviers écartés à ce stade, s'il fallait aller plus loin : gate sur le
> **ratio de puissance** (`pow_déf ≥ 0,75 × pow_att`, mais risque d'affamer le
> joueur le plus fort du serveur), **demi-victoire** pour un match déséquilibré
> (pas de blocage sec, mais victoires fractionnaires à l'écran), ou **bonus
> d'outsider** sur la défense auto des petites collections.

**Distribution quotidienne** (`py -3 tools/test_duel_packs.py`) : le test extrait
par `ast` les vraies fonctions de `cogs/duel_cog.py` (discord.py n'est pas
installé en dev) et vérifie 4 adversaires sur 6 matchs → 1 pack, 5 → 2 packs,
**le farmeur à 6 victoires sur 2 cibles → 0 pack**, la revanche (défaite puis
victoire sur la même cible, 3 adversaires distincts → 1 pack), **le match de
marge** (une défaite + 5 adversaires battus → 2 packs), l'amical qui ne
compte pas, les défenses gagnées qui ne rapportent rien, un duel à 23 h 45 qui
reste dans sa journée et un duel à 00 h 15 qui bascule dans la suivante,
l'idempotence sur deux relances, et les journées de 23 h / 25 h aux changements
d'heure.

Tests logiques hors-ligne (sans Discord) :
`py -3 tools/test_duel_balance.py` (équilibrage + Elo + paliers de packs) et
`py -3 tools/test_duel_packs.py` (distribution quotidienne : paliers, bornes de
journée, idempotence).

---

## 5. Mise en production / config

- **`.env`** (valeurs par défaut déjà câblées sur l'admin) :
  `PUBLIC_LAUNCH=2026-08-25`, `BETA_TESTER_IDS=133711821214449665`,
  `BETA_CHANNEL_ID=441230079100715008`, et options
  `DUEL_ELO_BAND`, `DUEL_SOFT_K`, `DUEL_DAILY_PAIR_CAP` (2),
  `DUEL_DAILY_MATCH_CAP` (6), `DUEL_DEFENSE_DM_CAP` (5),
  `DUEL_DAILY_PACKS_CHECK_MINUTES` (15), `DUEL_DAILY_PACKS_CATCHUP_DAYS` (7).
  ⚠️ `DUEL_DAILY_REWARD_CAP` et `DUEL_DEFENSE_REWARD_CAP` n'existent plus : si
  elles traînent dans le `.env` de prod, elles sont simplement ignorées.
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
