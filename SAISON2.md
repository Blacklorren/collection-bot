# Saison 2 — Échanges, Recyclage sélectif & Duels

Document de reprise. Tout est **bêta-gaté** (testable par l'admin dans le salon de
test, ouverture publique automatique le **1er août 2026**). Voir `beta.py`.

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
- **Elo** : départ 1000, K=32, bande classé ±150 (env `DUEL_ELO_BAND`).
- **Récompenses** (classé) : vainqueur +100 pts scalés ×0.25→×2 selon l'écart
  d'Elo ; perdant +20. Anti-farm : max 3 classés/jour entre 2 mêmes joueurs
  (`DUEL_DAILY_PAIR_CAP`), max 10 récompensés/jour/joueur (`DUEL_DAILY_REWARD_CAP`).

**Équilibrage validé** (`py -3 tools/test_duel_balance.py`, 10 000 matchs) :
7 Lég dépareillées battent 1 Lég+mixte même club seulement ~63 % du temps ;
7 Épiques même club battent 7 Lég dépareillées ~73 % → **le collectif prime**.

Les 7 postes / slots : `GB · ALG · ARG · DC · PIV · ARD · ALD`.

---

## 3. Composition MANUELLE des équipes — ✅ FAIT

Implémenté dans `cogs/duel_cog.py` (pattern repris de `TradePicker`).

### Flux
- Après acceptation du défi, le message de défi se transforme en **phase de
  composition** (`DuelLineupView`) : statut des deux joueurs + boutons
  **« Composer mon équipe »** et **« Annuler »**.
- Chaque joueur ouvre son **sélecteur privé** (`LineupPicker`, éphémère) :
  - **select de poste** (`GB…ALD`) — choisit quel slot éditer (montre la carte
    actuelle par slot),
  - **select de club** puis **select de carte** — place n'importe quelle carte
    possédée jouable ; ✓/✗ indique si elle est à son poste (bonus ×1.4),
  - boutons **« Vider le poste »**, **« Compo automatique »** (réutilise
    `auto_lineup()`) et **« Prêt »**.
- Le match (`play_match`) se lance automatiquement quand **les deux** ont cliqué
  « Prêt ». `play_match` lit `DuelSession.lineup_c/lineup_o` (plus d'`auto_lineup`
  forcé). Elo, récompenses, record et embed inchangés.

### Détails d'implémentation
- Une même carte ne peut occuper qu'un seul slot : la placer la **retire**
  automatiquement de son slot précédent.
- Slots laissés vides autorisés (note plancher Commun via `team_power`).
- Cartes Noël exclues ; dédup par carte (`get_user_collection`).
- Toute modif d'une lineup ré-arme le bouton « Prêt » de ce joueur.
- Verrou `ACTIVE_DUELISTS` conservé pendant toute la composition ; libéré sur
  annulation, timeout (300 s) ou fin de match.

---

## 4. Tester (en bêta, salon `441230079100715008`)

1. Déployer, lancer le bot, `!sync`.
2. `/echange @autre_compte` — panier des deux côtés, double validation.
3. `/recycler` — version sélective (liste + « Tout recycler »).
4. `/defi @autre_compte` (classé) et `/defi @autre amical:True`. Après acceptation :
   chaque joueur clique **« Composer mon équipe »**, aligne ses cartes (ou **« Compo
   automatique »**), puis **« Prêt »** ; le match se lance quand les deux sont prêts.
5. `/classement_duel`.
6. Hors testeurs / hors salon → message « arrive la saison prochaine ».

Tests logiques hors-ligne (sans Discord) :
`py -3 tools/test_duel_balance.py` (équilibrage + Elo).

---

## 5. Mise en production / config

- **`.env`** (valeurs par défaut déjà câblées sur l'admin) :
  `PUBLIC_LAUNCH=2026-08-01`, `BETA_TESTER_IDS=133711821214449665`,
  `BETA_CHANNEL_ID=441230079100715008`, et options
  `DUEL_ELO_BAND`, `DUEL_DAILY_PAIR_CAP`, `DUEL_DAILY_REWARD_CAP`.
- **Postes** : déjà dans `cards.json` (rien à faire ; l'outil xlsx reste dispo au cas où).
- **Dépendance dev uniquement** : `openpyxl` (scripts `tools/`, pas le runtime).
- Le **1er août**, tout s'ouvre au public automatiquement (aucune manip).

### Bug latent repéré (hors périmètre, non corrigé)
`database.get_journees_for_rappel` utilise `datetime.datetime.now()` alors que
l'import est `from datetime import datetime` → exception au déclenchement. À corriger.

---

## 6. Prompt de reprise (copier-coller pour la prochaine session)

> Reprends le chantier Saison 2 du bot `collection-bot` (dans `E:\Bot Handnews\collection-bot`).
> Lis `SAISON2.md`. **Tout le code est en place** (échanges, recyclage, duels avec
> composition manuelle — `DuelLineupView`/`LineupPicker` dans `cogs/duel_cog.py`).
> Il reste à **tester en vrai sur Discord** dans le salon bêta (§4), notamment le
> nouveau flux de composition des duels à deux joueurs (sélecteurs éphémères,
> bouton « Prêt » de chaque côté, lancement auto du match). Et corriger le bug
> latent `database.get_journees_for_rappel` (§5).
