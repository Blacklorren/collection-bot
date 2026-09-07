"""Ou couper le classement des pronostics entre deux saisons ? (lecture seule)

Le classement general (!cg) ne compte plus que les matchs joues DEPUIS le debut de
la saison en cours : database.DEBUT_SAISON_PRONOS. Rien n'est efface, la coupure est
un simple filtre sur matchs.date_match, donc elle se corrige a tout moment.

Ce script sert a choisir cette date en connaissance de cause, puis a la verifier :
  - il montre le calendrier des matchs joues, mois par mois, pour faire apparaitre
    l'intersaison (le trou de l'ete) ou doit tomber la coupure ;
  - il affiche cote a cote le classement de la saison et celui de tout l'historique.

Il n'ecrit RIEN : on peut le lancer sur la production sans precaution.
La base de production est sur le volume Railway (/data/collection.db) : sans --db,
c'est bien celle-la qui est lue, donc lancer le script LA-BAS.

Usage :
    python tools/saison_pronos.py                      # avec la date du code
    python tools/saison_pronos.py --depuis 2026-08-15  # essayer une autre date
    python tools/saison_pronos.py --db ./collection.db # sur une copie locale
"""
import argparse
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import database  # noqa: E402

POINTS_BON_PRONO = 50  # doit rester aligne sur cogs/pronostics_cog.py


def calendrier(con):
    """Matchs joues (resultat connu) par mois, avec le nombre de pronos corrects."""
    return con.execute("""
        SELECT substr(m.date_match, 1, 7) AS mois,
               COUNT(DISTINCT m.id)       AS matchs,
               MIN(date(m.date_match))    AS premier,
               MAX(date(m.date_match))    AS dernier,
               SUM(CASE WHEN p.pronostic = m.resultat THEN 1 ELSE 0 END) AS bons
        FROM matchs m
        LEFT JOIN pronostics p ON p.match_id = m.id
        WHERE m.resultat IS NOT NULL
        GROUP BY mois
        ORDER BY mois
    """).fetchall()


def classement(con, depuis, limite):
    requete = """
        SELECT p.user_id, COUNT(*) AS bons
        FROM pronostics p
        JOIN matchs m ON p.match_id = m.id
        WHERE p.pronostic = m.resultat AND m.resultat IS NOT NULL
    """
    params = []
    if depuis:
        requete += " AND m.date_match >= ?"
        params.append(depuis)
    requete += " GROUP BY p.user_id ORDER BY bons DESC LIMIT ?"
    params.append(limite)
    return con.execute(requete, params).fetchall()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--depuis", default=database.DEBUT_SAISON_PRONOS,
                    help="date ISO de debut de saison a tester (defaut : celle du code)")
    ap.add_argument("--limite", type=int, default=15, help="taille des classements affiches")
    ap.add_argument("--db", default=database.DB_NAME)
    args = ap.parse_args()

    if not os.path.exists(args.db):
        print(f"Base introuvable : {args.db}")
        print("Sur Railway la base vit dans /data ; en local, passer --db ./collection.db")
        sys.exit(1)

    con = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    print(f"Base    : {args.db} (lecture seule)")
    print(f"Coupure : {args.depuis}   (database.DEBUT_SAISON_PRONOS = {database.DEBUT_SAISON_PRONOS})")

    print("")
    print("--- Matchs joues, mois par mois -------------------------------------")
    print("Un mois sans ligne = aucun match : c'est la que doit tomber la coupure.")
    print("")
    print(f"{'mois':<9}{'matchs':>7}{'bons pronos':>13}   fenetre")
    lignes = calendrier(con)
    if not lignes:
        print("(aucun match avec resultat dans cette base)")
    coupure_posee = False
    for mois, matchs, premier, dernier, bons in lignes:
        if not coupure_posee and args.depuis and dernier and dernier >= args.depuis:
            print(f"{'':>9}{'':>7}{'':>13}   ---- coupure : {args.depuis} ----")
            coupure_posee = True
        print(f"{mois or '?':<9}{matchs:>7}{bons or 0:>13}   {premier} -> {dernier}")
    if lignes and not coupure_posee:
        print(f"{'':>9}{'':>7}{'':>13}   ---- coupure : {args.depuis} (apres tout l'historique) ----")

    for titre, depuis in (("SAISON EN COURS", args.depuis), ("TOUT L'HISTORIQUE", None)):
        print("")
        borne = f"depuis {depuis}" if depuis else "depuis toujours"
        print(f"--- {titre} ({borne}) ".ljust(70, "-"))
        rangs = classement(con, depuis, args.limite)
        if not rangs:
            print("(personne)")
            continue
        for rang, (user_id, bons) in enumerate(rangs, 1):
            print(f"{rang:>3}. {user_id:<20} {bons:>4} bons  {bons * POINTS_BON_PRONO:>6} pts")

    con.close()
    print("")
    print("Si la coupure ne tombe pas au bon endroit, changer DEBUT_SAISON_PRONOS")
    print("dans database.py, ou poser la variable d'environnement du meme nom sur")
    print("Railway (elle a la priorite, et evite un redeploiement).")


if __name__ == "__main__":
    main()
