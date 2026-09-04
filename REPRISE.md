# Reprise du 4 septembre 2026 — mode d'emploi

Bascule Saison 1 -> Saison 2, vendredi 4 septembre à 2 h.
Trois choses à faire, dans cet ordre. Chaque étape a une commande de contrôle.

**Les arbitrages qui structurent cette reprise** (pris le 1er septembre, à ne pas re-litiger) :

- **la Saison 1 devient une archive pure** — les cartes restent visibles dans
  `/collection` (un exemplaire chacune), mais ne sont **plus tirables en pack, plus
  créables au craft, et plus alignables en duel** ;
- **l'Elo repart à 1000 pour tout le monde** — classement de duels neuf ;
- **les 258 cartes de la Saison 2 sont prêtes.** Samuel Vedie-Marconnes a été retiré
  du jeu (aucun portrait officiel possible), et deux joueurs encore en silhouette
  côté LNH portent une silhouette de remplacement.

---

## Ce qui distingue une saison d'une autre

Une carte porte un champ **`saison`**. C'est lui, et lui seul, qui décide de tout :
tirage, craft, duel, progression, classement. Une carte sans ce champ est une carte
de la Saison 1 — c'est le cas des 275 cartes actuelles, que `tools/publier_s2.py`
marque au passage.

**Le bot ne relit `cards.json` qu'au démarrage.** Toute publication demande donc un
redémarrage, ou un `!reload collection` + `!reload duel`.

**Si les cartes de la saison 2 ne sont pas encore publiées, le bot continue la
saison 1** au lieu de tomber en marche (pool de tirage vide). Il l'annonce dans ses
logs. Déployer le code avant les cartes est donc sans danger.

---

## Étape 1 — finir les cartes (mercredi / jeudi)

Voir `docs/GENERATION_S2.md` pour le détail de la chaîne. En résumé :

```bash
py tools/fetch_lnh_s2.py                       # récupérer les portraits publiés depuis
py tools/build_prompts_s2.py --skip-done       # prompts des seuls nouveaux
# ... collage Midjourney, puis récupération des rendus ...
py tools/pick_renders.py --downloads "C:/Users/quent/Downloads/mj" --per-player 1
py tools/finalize_s2.py                        # cutouts + cartes de contrôle
```

**Contrôle** : `out/cards/_planche-<club>.png` — la planche contact du club. C'est
exactement ce que le bot enverra sur Discord (le cadrage « buste » est désormais
commun aux deux).

---

## Étape 2 — publier les cartes (jeudi soir)

```bash
py tools/publier_s2.py
```

Simulation : rien n'est écrit. Lis le rapport — il dit combien de cartes seront
ajoutées et **quels joueurs n'ont pas encore de portrait** (ceux-là sont ignorés,
publier une carte sans portrait casserait l'album de son club).

```bash
py tools/publier_s2.py --go
```

Le script est **rejouable** : si des portraits arrivent après coup, relance-le, il
n'ajoute que les nouveaux.

**Contrôle** : le bloc « Controle de relecture » doit afficher `sans saison : 0`,
`ids en double : 0`, `S2 sans cutout : 0`.

Puis on pousse. **Les cutouts font partie du déploiement** — sans eux le bot n'a
aucun portrait à composer :

```bash
git add cards.json assets/cutouts refs data/roster_s2.json cogs utils tools requirements.txt .python-version docs REPRISE.md
git commit -m "Saison 2 : publication des cartes et bascule de l archive"
git push
```

**Contrôle** : dans le log de build Railway, vérifier la **version de Python**. Le
dépôt la fixe désormais à 3.12 (`.python-version`) : `discord.py 2.3.2` ne s'importe
pas sous 3.13, et `Pillow 10.2.0` n'y a pas de wheel. Si le build part sur 3.13, le
bot ne démarrera pas du tout.

---

## Étape 3 — la bascule (vendredi 2 h)

### 3.1 Vérifier le garde-fou AVANT tout

```
/data/reset_done.lock
```

**Ce fichier doit exister sur le volume Railway.** S'il manque, `bot.py` lance
`wipe_all_user_data()` au démarrage et **efface tout, collections comprises** —
l'inverse exact de ce qu'on veut. À vérifier avant de toucher au volume ou de
redéployer proprement.

### 3.2 Migrer la base : rien à lancer, c'est le redémarrage qui le fait

La migration se déclenche **au démarrage de `bot.py`**, une seule fois, sur le même
principe que la remise à zéro ci-dessus : un témoin sur le volume,
`/data/migration_s2_done.lock`. Tant qu'il est absent la bascule s'exécute ; une fois
posé, elle ne se rejoue plus, quel que soit le nombre de redéploiements.

Il n'y a donc **ni SSH, ni CLI, ni arrêt manuel du bot** : on redéploie, et on lit le
log. C'est aussi ce qui règle la question du pack ouvert pendant la migration — elle a
lieu avant que le bot ne se connecte à Discord.

Ce qu'on doit voir dans le log de déploiement :

```
🏁  (S2) Lancement de la bascule Saison 1 -> Saison 2...
Base            : /data/collection.db
Doublons S1     : N lignes a supprimer, chez M joueurs
Sauvegarde      : /data/collection.db.avant-s2-<horodatage>.bak
N doublons supprimes. Compteurs remis a zero : ...
Controle : 0 doublon(s) S1 restant(s) (doit valoir 0).
✅  (S2) Bascule terminée. Le témoin est posé, elle ne se rejouera pas.
```

Une **sauvegarde horodatée** est prise avant écriture (`collection.db.avant-s2-*.bak`),
sur le volume. En cas de problème, c'est elle qu'on restaure.

**Si la bascule échoue**, le témoin n'est pas posé et la base n'est pas modifiée (tout
passe en une transaction) : le bot démarre quand même — pour ne pas laisser le service
en boucle de crash — et la bascule sera retentée au redémarrage suivant. Le log affiche
alors `❌ (S2) Bascule EN ÉCHEC` suivi de la classe de l'exception et de la trace.

Pour la relancer volontairement, il suffit de supprimer
`/data/migration_s2_done.lock`. Et pour la jouer à la main sur une base de test :

```bash
python tools/migration_s2.py --db chemin/vers/copie.db        # simulation
python tools/migration_s2.py --db chemin/vers/copie.db --go
```

Ce que ça fait :
- un seul exemplaire de chaque carte S1 par joueur (l'archive reste complète) ;
- points, packs et fragments à **0** ;
- Elo à **1000**.

Ce que ça ne touche pas : les collections elles-mêmes, les pronostics, et
l'historique des duels (c'est un journal, chaque ligne porte l'Elo d'avant/après).

Le script ne purge **que** la saison 1, même s'il tourne après la publication : un
joueur qui aurait ouvert des packs entre-temps ne perd rien.

### 3.3 Synchroniser les commandes

```
!sync
```

---

## Les contrôles d'après-bascule

| Commande | Ce qu'on doit voir |
|---|---|
| `/pack` puis `/open` | des cartes de la **saison 2** uniquement, au cadrage buste |
| `/collection` | s'ouvre sur « Album de Collection — Saison 2 », progression sur 258 |
| bouton **Archive S1** | la collection de la saison passée, complète, un exemplaire par carte |
| `/points` | 0 point, 0 pack |
| `/classement_duel` | tout le monde à 1000 |
| `/defi @quelquun` | compo à 7 postes avec des cartes S2 **seulement** |
| `/creer <nom>` | trouve la carte sans dire « Trop de résultats » |
| `!test all` | tous les modules verts |

**Le test qui compte le plus, et qui n'a jamais été fait** : un vrai duel à **deux
comptes**. Le moteur, la base, l'Elo et les récompenses sont vérifiés hors ligne,
mais l'interface Discord à deux joueurs (composition partagée, bouton « Prêt » des
deux côtés, lancement automatique, annulation avec un sélecteur ouvert) n'a jamais
tourné en vrai. À faire **avant** vendredi, pas pendant.

---

## Si ça tourne mal

| Symptôme | Cause probable | Geste |
|---|---|---|
| le bot ne démarre pas | build parti sur Python 3.13 | vérifier `.python-version`, redéployer |
| collections vides | `reset_done.lock` absent -> wipe au démarrage | restaurer la sauvegarde `.bak` |
| `/pack` donne des cartes S1 | `cards.json` sans champ `saison` | relancer `tools/publier_s2.py --go`, redémarrer |
| `/pack` plante | pool de tirage vide | les logs disent la saison suivie ; publier puis redémarrer |
| une carte sans portrait | cutout manquant | `tools/finalize_s2.py`, puis republier |
| doublons S1 encore là | bascule non jouée, ou en échec | chercher `(S2)` dans le log ; si le témoin manque elle se rejouera au prochain démarrage |
| points/Elo remis à zéro après coup | `migration_s2_done.lock` effacé du volume | restaurer la sauvegarde `.bak` la plus récente |
