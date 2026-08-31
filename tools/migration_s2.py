"""Bascule S1 -> archive : purge des doublons S1 et remise a zero des fragments.
(A LANCER UNE SEULE FOIS, au lancement de la Saison 2.)

Ce que fait la migration :
  - chaque joueur ne garde QU'UN exemplaire de chaque carte S1 qu'il possede
    (l'archive reste consultable et complete) ;
  - fragments, packs non ouverts et points repassent a 0 pour tout le monde :
    remise a plat complete de l'economie, personne ne demarre la S2 avec une
    reserve constituee avant la bascule.

L'Elo des duels repart de sa valeur de depart (1000) : classement neuf pour la
saison 2. L'historique de la table `duels` est conserve comme journal.

Ce qu'elle ne touche pas : les pronostics, ni les collections elles-memes. Seule la
collection S1 survit, a un exemplaire par carte, en lecture seule.

A ne pas confondre avec database.wipe_all_user_data(), qui efface TOUT, collections
comprises. Ici les collections survivent, seuls les doublons disparaissent.

La base de production est sur le volume Railway (/data/collection.db) : ce script
doit donc etre lance LA-BAS, pas sur la copie locale.

Securites : mode simulation par defaut (il faut --go pour ecrire), et sauvegarde
horodatee de la base avant toute modification.

Usage :
    python tools/migration_s2.py              # simulation, n'ecrit rien
    python tools/migration_s2.py --go         # applique, apres sauvegarde
"""
import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import database  # noqa: E402
import duel_engine  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CARDS_S1 = os.path.join(ROOT, "cards.json")

# Compteurs de la table users remis a zero au lancement de la S2
COLONNES_RAZ = ("fragments", "packs", "points")

# L'Elo, lui, ne repart pas de zero mais de sa VALEUR DE DEPART : un classement
# de duels neuf pour la saison 2 (arbitrage de la reprise). L'historique de la
# table `duels` est conserve tel quel — c'est un journal, et chaque ligne porte
# deja l'Elo d'avant et d'apres, donc il reste lisible.
ELO_DEPART = duel_engine.ELO_START


def ids_s1():
    """Ids des cartes de la SAISON 1, en texte : la colonne card_id melange entiers
    et chaines (ex. 'noel_1'), on compare donc tout en TEXT.

    Le filtre sur `saison` rend cette migration INDEPENDANTE DE L'ORDRE. Sans lui,
    publier la saison 2 avant de migrer ferait purger les doublons de la saison
    NEUVE (ids_s1() rendrait aussi les slugs S2) : un joueur qui aurait ouvert des
    packs entre la publication et la bascule perdrait ce qu'il vient de gagner.
    Une carte sans champ `saison` date d'avant tools/publier_s2.py : c'est une S1."""
    cartes = json.load(open(CARDS_S1, encoding="utf-8"))
    return [str(c["id"]) for c in cartes if c.get("saison", 1) == 1]


def colonne_existe(cur, table, colonne):
    return colonne in [r[1] for r in cur.execute(f"PRAGMA table_info({table})")]


def sauvegarder(chemin):
    """Copie coherente meme si le bot tourne (API backup de SQLite)."""
    horo = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = f"{chemin}.avant-s2-{horo}.bak"
    src = sqlite3.connect(chemin)
    dst = sqlite3.connect(dest)
    with dst:
        src.backup(dst)
    dst.close()
    src.close()
    return dest


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--go", action="store_true", help="applique reellement (sinon simulation)")
    ap.add_argument("--db", default=database.DB_NAME)
    args = ap.parse_args()

    if not os.path.exists(args.db):
        raise SystemExit(f"Base introuvable : {args.db}\n"
                         "Sur Railway elle est sur le volume monte (/data/collection.db).")

    s1 = ids_s1()
    marques = ",".join("?" * len(s1))
    con = sqlite3.connect(args.db)
    cur = con.cursor()

    # --- etat avant ---
    total = cur.execute("SELECT COUNT(*) FROM user_cards").fetchone()[0]
    a_supprimer = cur.execute(f"""
        SELECT COUNT(*) FROM user_cards
        WHERE id NOT IN (SELECT MIN(id) FROM user_cards GROUP BY user_id, card_id)
          AND CAST(card_id AS TEXT) IN ({marques})
    """, s1).fetchone()[0]
    joueurs = cur.execute(f"""
        SELECT COUNT(DISTINCT user_id) FROM user_cards
        WHERE id NOT IN (SELECT MIN(id) FROM user_cards GROUP BY user_id, card_id)
          AND CAST(card_id AS TEXT) IN ({marques})
    """, s1).fetchone()[0]

    presentes = [c for c in COLONNES_RAZ if colonne_existe(cur, "users", c)]
    elo_present = colonne_existe(cur, "users", "elo")

    print(f"Base            : {args.db}")
    print(f"Cartes en base  : {total} lignes")
    print(f"Doublons S1     : {a_supprimer} lignes a supprimer, chez {joueurs} joueurs")
    for col in COLONNES_RAZ:
        if col not in presentes:
            print(f"{col.capitalize():15s} : colonne absente, rien a faire")
            continue
        somme, combien = cur.execute(
            f"SELECT COALESCE(SUM({col}),0), COUNT(*) FILTER (WHERE {col} > 0) FROM users"
        ).fetchone()
        print(f"{col.capitalize():15s} : {somme} au total, chez {combien} joueurs")
    if elo_present:
        combien, mini, maxi = cur.execute(
            f"SELECT COUNT(*) FILTER (WHERE elo != {ELO_DEPART}), MIN(elo), MAX(elo) FROM users"
        ).fetchone()
        print(f"{'Elo':15s} : {combien} joueur(s) hors de {ELO_DEPART} "
              f"(de {mini} a {maxi}) -> tous ramenes a {ELO_DEPART}")
    else:
        print(f"{'Elo':15s} : colonne absente, rien a faire")

    if not args.go:
        print("\nSIMULATION — rien n'a ete ecrit. Relance avec --go pour appliquer.")
        con.close()
        return

    con.close()
    bak = sauvegarder(args.db)
    print(f"\nSauvegarde      : {bak}")

    con = sqlite3.connect(args.db)
    cur = con.cursor()
    try:
        cur.execute("BEGIN")
        cur.execute(f"""
            DELETE FROM user_cards
            WHERE id NOT IN (SELECT MIN(id) FROM user_cards GROUP BY user_id, card_id)
              AND CAST(card_id AS TEXT) IN ({marques})
        """, s1)
        supprimees = cur.rowcount
        remises = {}
        for col in presentes:
            cur.execute(f"UPDATE users SET {col} = 0 WHERE {col} != 0")
            remises[col] = cur.rowcount
        if elo_present:
            cur.execute("UPDATE users SET elo = ? WHERE elo != ?", (ELO_DEPART, ELO_DEPART))
            remises["elo"] = cur.rowcount
        con.commit()
    except Exception:
        con.rollback()
        con.close()
        print("ECHEC : rien n'a ete modifie, la base est intacte.")
        raise

    restants = cur.execute(f"""
        SELECT COUNT(*) FROM user_cards
        WHERE id NOT IN (SELECT MIN(id) FROM user_cards GROUP BY user_id, card_id)
          AND CAST(card_id AS TEXT) IN ({marques})
    """, s1).fetchone()[0]
    con.close()

    detail = ", ".join(f"{n} {col}" for col, n in remises.items() if col != "elo")
    print("")
    print(f"{supprimees} doublons supprimes. Compteurs remis a zero : {detail}.")
    if remises.get("elo"):
        print(f"{remises['elo']} joueur(s) ramene(s) a {ELO_DEPART} d'Elo (classement neuf).")
    print(f"Controle : {restants} doublon(s) S1 restant(s) (doit valoir 0).")
    print(f"En cas de probleme, restaurer la sauvegarde : {os.path.basename(bak)}")


if __name__ == "__main__":
    main()
