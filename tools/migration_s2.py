"""Bascule S1 -> archive : purge des doublons S1 et remise a zero des fragments.
(A LANCER UNE SEULE FOIS, au lancement de la Saison 2.)

Ce que fait la migration :
  - chaque joueur ne garde QU'UN exemplaire de chaque carte S1 qu'il possede
    (l'archive reste consultable et complete) ;
  - fragments, packs non ouverts et points repassent a 0 pour tout le monde :
    remise a plat complete de l'economie, personne ne demarre la S2 avec une
    reserve constituee avant la bascule.

Ce qu'elle ne touche pas : les pronostics et l'Elo des duels. Seule la collection S1
survit, a un exemplaire par carte, en lecture seule.

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

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CARDS_S1 = os.path.join(ROOT, "cards.json")

# Compteurs de la table users remis a zero au lancement de la S2
COLONNES_RAZ = ("fragments", "packs", "points")


def ids_s1():
    """Ids des cartes S1, en texte : la colonne card_id melange entiers et chaines
    (ex. 'noel_1'), on compare donc tout en TEXT."""
    return [str(c["id"]) for c in json.load(open(CARDS_S1, encoding="utf-8"))]


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

    detail = ", ".join(f"{n} {col}" for col, n in remises.items())
    print(f"\n{supprimees} doublons supprimes. Compteurs remis a zero : {detail}.")
    print(f"Controle : {restants} doublon(s) S1 restant(s) (doit valoir 0).")
    print(f"En cas de probleme, restaurer la sauvegarde : {os.path.basename(bak)}")


if __name__ == "__main__":
    main()
