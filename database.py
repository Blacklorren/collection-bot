import sqlite3
import os
import contextlib
from datetime import datetime, date, timedelta, timezone
from typing import List, Tuple, Dict, Any

# Le dossier où les données persistantes seront stockées
DATA_DIR = '/data'

# On garde le nom de variable DB_NAME, mais on lui assigne le chemin complet
DB_NAME = os.path.join(DATA_DIR, 'collection.db')
# La valeur de DB_NAME est maintenant '/data/collection.db'

# Attente max avant de lever "database is locked" (ms côté SQLite / s côté driver)
BUSY_TIMEOUT_MS = 10000


@contextlib.contextmanager
def _connect():
    """Connexion SQLite avec commit/rollback ET fermeture garantis.

    ⚠️ `with sqlite3.connect(...)` ne ferme PAS la connexion (il ne fait que
    commit/rollback), ce qui faisait fuir un descripteur de fichier à chaque appel.
    On passe aussi un busy_timeout pour ne pas exploser en "database is locked"
    quand les boucles de fond et les interactions Discord se croisent.
    """
    con = sqlite3.connect(DB_NAME, timeout=BUSY_TIMEOUT_MS / 1000)
    try:
        con.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def initialize_database():
    """Crée les tables de la base de données si elles n'existent pas."""
    # S'assurer que le dossier /data existe (au cas où)
    os.makedirs(DATA_DIR, exist_ok=True)
    with _connect() as con:
        cur = con.cursor()

        # WAL : lectures concurrentes pendant une écriture. Réglage persistant,
        # stocké dans le fichier .db, donc à poser une seule fois.
        cur.execute("PRAGMA journal_mode = WAL")
        cur.execute("PRAGMA synchronous = NORMAL")

        # Tables existantes pour le jeu de cartes
        cur.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                points INTEGER NOT NULL DEFAULT 0,
                packs INTEGER NOT NULL DEFAULT 0,
                last_activity_date TEXT,
                last_message_time TEXT,
                daily_message_points INTEGER NOT NULL DEFAULT 0,
                fragments INTEGER NOT NULL DEFAULT 0,
                has_received_onboarding INTEGER NOT NULL DEFAULT 0
            )
        ''')

        # Ajout sécurisé des colonnes existantes
        try:
            cur.execute("ALTER TABLE users ADD COLUMN daily_message_points INTEGER NOT NULL DEFAULT 0")
        except sqlite3.OperationalError: pass
        try:
            cur.execute("ALTER TABLE users ADD COLUMN fragments INTEGER NOT NULL DEFAULT 0")
        except sqlite3.OperationalError: pass
        try:
            cur.execute("ALTER TABLE users ADD COLUMN has_received_onboarding INTEGER NOT NULL DEFAULT 0")
        except sqlite3.OperationalError: pass 
        try:
            cur.execute("ALTER TABLE users ADD COLUMN last_advent_pack_date TEXT")
        except sqlite3.OperationalError: pass    
        try:
            cur.execute("ALTER TABLE users RENAME COLUMN last_daily TO last_activity_date")
        except sqlite3.OperationalError: pass

            
        cur.execute('''
            CREATE TABLE IF NOT EXISTS user_cards (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                card_id INTEGER NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users (user_id)
            )
        ''')
        
        # NOUVELLES TABLES POUR LES PRONOSTICS
        
        # Table des journées
        cur.execute('''
            CREATE TABLE IF NOT EXISTS journees (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                numero INTEGER NOT NULL,
                date_debut TIMESTAMP,
                date_fin TIMESTAMP,
                is_active BOOLEAN DEFAULT 1,
                rappel_envoye BOOLEAN DEFAULT 0
            )
        ''')
        
        # Table des matchs
        cur.execute('''
            CREATE TABLE IF NOT EXISTS matchs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                journee_id INTEGER,
                event_id TEXT UNIQUE,
                discord_event_id INTEGER,
                equipe1 TEXT NOT NULL,
                equipe2 TEXT NOT NULL,
                competition TEXT,
                date_match TIMESTAMP,
                resultat TEXT,
                score TEXT,
                pronos_fermes BOOLEAN DEFAULT 0,
                FOREIGN KEY (journee_id) REFERENCES journees(id)
            )
        ''')
        
        # Table des pronostics
        cur.execute('''
            CREATE TABLE IF NOT EXISTS pronostics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                match_id INTEGER,
                pronostic TEXT NOT NULL,
                points_gagnes INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(user_id),
                FOREIGN KEY (match_id) REFERENCES matchs(id),
                UNIQUE(user_id, match_id)
            )
        ''')
        
        # Table des messages de pronostics
        cur.execute('''
            CREATE TABLE IF NOT EXISTS prono_messages (
                match_id INTEGER PRIMARY KEY,
                message_id INTEGER,
                channel_id INTEGER,
                FOREIGN KEY (match_id) REFERENCES matchs(id)
            )
        ''')
        
        # Index pour optimiser les requêtes
        cur.execute("CREATE INDEX IF NOT EXISTS idx_matchs_date ON matchs(date_match)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_matchs_journee ON matchs(journee_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_pronostics_user ON pronostics(user_id)")
        
        # MIGRATION : Ajout de la colonne competition si elle n'existe pas
        try:
            cur.execute("ALTER TABLE matchs ADD COLUMN competition TEXT")
        except sqlite3.OperationalError:
            pass

        # === SAISON 2 : Elo, échanges, duels ===
        # Note d'Elo pour le matchmaking des duels (défaut 1000).
        try:
            cur.execute("ALTER TABLE users ADD COLUMN elo INTEGER NOT NULL DEFAULT 1000")
        except sqlite3.OperationalError:
            pass

        # Journal des échanges (traçabilité / litiges).
        cur.execute('''
            CREATE TABLE IF NOT EXISTS trade_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                user_a INTEGER NOT NULL,
                user_b INTEGER NOT NULL,
                cards_a TEXT NOT NULL,
                cards_b TEXT NOT NULL
            )
        ''')

        # Historique des duels entre joueurs.
        cur.execute('''
            CREATE TABLE IF NOT EXISTS duels (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                joueur1 INTEGER NOT NULL,
                joueur2 INTEGER NOT NULL,
                score1 INTEGER NOT NULL,
                score2 INTEGER NOT NULL,
                gagnant INTEGER,
                classe INTEGER NOT NULL DEFAULT 1,
                elo1_before INTEGER,
                elo2_before INTEGER,
                elo1_after INTEGER,
                elo2_after INTEGER,
                lineup1 TEXT,
                lineup2 TEXT
            )
        ''')
        # Points de message EN ATTENTE de maturation.
        # Les points d'un message ne sont pas credites tout de suite : ils murissent
        # apres un delai. Tant qu'ils n'ont pas muri, ils ne sont NI depensables NI
        # visibles dans le solde, donc supprimer le message ne peut rien reprendre
        # qui aurait deja ete converti en packs. Une ligne muri=1 reste pour pouvoir
        # debiter si le message est supprime plus tard.
        cur.execute('''
            CREATE TABLE IF NOT EXISTS pending_message_points (
                message_id INTEGER PRIMARY KEY,
                user_id    INTEGER NOT NULL,
                channel_id INTEGER NOT NULL,
                points     INTEGER NOT NULL,
                created_at TIMESTAMP NOT NULL,
                muri       INTEGER NOT NULL DEFAULT 0
            )
        ''')
        cur.execute("CREATE INDEX IF NOT EXISTS idx_pending_user ON pending_message_points(user_id, muri)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_pending_due ON pending_message_points(muri, created_at)")

        cur.execute("CREATE INDEX IF NOT EXISTS idx_duels_joueurs ON duels(joueur1, joueur2)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_duels_date ON duels(created_at)")

        con.commit()

# === FONCTIONS EXISTANTES POUR LE JEU DE CARTES ===

def get_week_dates(for_date):
    """Calcule les dates de début (lundi) et de fin (dimanche) pour la semaine d'une date donnée."""
    start_of_week = for_date - timedelta(days=for_date.weekday())
    end_of_week = start_of_week + timedelta(days=6)
    return start_of_week, end_of_week

def get_matches_in_date_range(start_date, end_date):
    """Récupère tous les matchs dans un intervalle de dates donné."""
    with _connect() as con:
        con.row_factory = sqlite3.Row
        cur = con.cursor()
        # On s'assure que la date de fin inclut toute la journée
        end_date_str = (end_date + timedelta(days=1)).isoformat()
        cur.execute("""
            SELECT * FROM matchs 
            WHERE date_match >= ? AND date_match < ?
            ORDER BY date_match ASC
        """, (start_date.isoformat(), end_date_str))
        return cur.fetchall()

def get_leaderboard_for_matches(match_ids):
    """Calcule le classement pour une liste spécifique d'ID de matchs."""
    if not match_ids:
        return []
    
    query = f"""
        SELECT 
            p.user_id,
            COUNT(p.id) AS bons_pronos,
            SUM(p.points_gagnes) AS total_points
        FROM pronostics p
        JOIN matchs m ON p.match_id = m.id
        WHERE p.pronostic = m.resultat AND p.match_id IN ({','.join('?' for _ in match_ids)})
        GROUP BY p.user_id
        ORDER BY total_points DESC, bons_pronos DESC;
    """
    with _connect() as con:
        con.row_factory = sqlite3.Row
        cur = con.cursor()
        cur.execute(query, match_ids)
        return cur.fetchall()

def wipe_all_user_data():
    """
    Vide toutes les données liées aux utilisateurs, collections, et pronostics.
    Cette fonction est conçue pour une remise à zéro complète du jeu.
    Elle est maintenant plus robuste et ne plantera pas si une table est manquante.
    """
    print("⚠️  [DATABASE] Lancement de la procédure de remise à zéro des données...")
    try:
        with _connect() as con:
            cur = con.cursor()

            # Récupérer la liste de toutes les tables existantes pour éviter les erreurs
            cur.execute("SELECT name FROM sqlite_master WHERE type='table';")
            existing_tables = [row[0] for row in cur.fetchall()]

            # Liste des tables à vider avec le nom corrigé
            tables_to_wipe = [
                "users",
                "user_cards",
                "points",
                "pronostics",
                "prono_messages"
            ]

            for table in tables_to_wipe:
                if table in existing_tables:
                    cur.execute(f"DELETE FROM {table};")
                    # Réinitialiser les compteurs auto-incrémentés (facultatif mais propre)
                    if 'sqlite_sequence' in existing_tables:
                        cur.execute(f"DELETE FROM sqlite_sequence WHERE name='{table}';")
                    print(f"  - Table '{table}' vidée.")
                else:
                    print(f"  - Table '{table}' non trouvée, ignorée.")

            # Mettre à jour les journées si la table existe
            if 'journees' in existing_tables:
                cur.execute("UPDATE journees SET rappel_envoye = 0, is_active = 1;")
                print("  - Statut des journées réinitialisé.")

            con.commit()
            print("✅  [DATABASE] Remise à zéro des données terminée avec succès.")
            return True  # Indiquer que l'opération a réussi

    except sqlite3.Error as e:
        print(f"❌  [DATABASE] Une erreur est survenue lors de la remise à zéro : {e}")
        return False # Indiquer que l'opération a échoué

def check_user(user_id, con=None):
    """Crée l'utilisateur s'il n'existe pas (INSERT OR IGNORE : 1 requête au lieu de 2).

    `con` permet de réutiliser une connexion existante et d'éviter d'en ouvrir
    une seconde — utile dans les chemins chauds comme on_message.
    """
    sql = "INSERT OR IGNORE INTO users (user_id, points, packs) VALUES (?, 100, 1)"
    if con is not None:
        con.execute(sql, (user_id,))
        return
    with _connect() as c:
        c.execute(sql, (user_id,))

def set_onboarding_received(user_id):
    """Marque un utilisateur comme ayant reçu le message d'accueil."""
    check_user(user_id)
    with _connect() as con:
        cur = con.cursor()
        cur.execute("UPDATE users SET has_received_onboarding = 1 WHERE user_id = ?", (user_id,))
        con.commit()

def get_user_data(user_id):
    # Une seule connexion pour la création + la lecture : get_user_data est appelée
    # sur CHAQUE message du serveur via on_message.
    with _connect() as con:
        check_user(user_id, con)
        con.row_factory = sqlite3.Row
        cur = con.cursor()
        cur.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
        return cur.fetchone()

def update_points(user_id, amount):
    check_user(user_id)
    with _connect() as con:
        cur = con.cursor()
        cur.execute("UPDATE users SET points = points + ? WHERE user_id = ?", (amount, user_id))
        con.commit()

def mass_add_points(amount):
    """Ajoute des points à TOUS les joueurs enregistrés (chaque ligne de `users`).

    « Tout le monde » = les joueurs connus du bot, pas les membres du serveur :
    qui n'a jamais déclenché `check_user` n'a pas de ligne, donc ne reçoit rien.
    Volontairement additif seulement (garde-fou côté `/addpointsall`) : un retrait
    de masse ferait passer des soldes sous zéro sans aucune vérification.
    Retourne le nombre de joueurs crédités.
    """
    with _connect() as con:
        cur = con.cursor()
        cur.execute("UPDATE users SET points = points + ?", (amount,))
        con.commit()
        return cur.rowcount

def get_all_user_ids():
    """Tous les joueurs enregistrés dans le bot (une ligne de `users` = un compte)."""
    with _connect() as con:
        return [r[0] for r in con.cursor().execute("SELECT user_id FROM users").fetchall()]


def get_points_earned_since(since_iso, include_pronos=True):
    """Points GAGNÉS par joueur depuis `since_iso` : {user_id: points}.

    « Gagnés », pas « crédités » : les points de message mûrissent 12 h avant
    d'entrer dans le solde, donc une bonne partie de la fenêtre récente est encore
    en attente. On les compte quand même — ils ont bien été obtenus, et les lignes
    des messages supprimés ont déjà disparu de la table (cf `revoke_message_points`).

    ⚠️ Fenêtre max fiable = POINT_CLAWBACK_HOURS (48 h par défaut) : au-delà,
    `purge_matured_message_points` a effacé les lignes et le total serait sous-évalué.

    Les gains de pronostic sont datés par `matchs.date_match` : l'attribution ne
    laisse aucun horodatage propre (elle n'écrit que `points_gagnes`), et la boucle
    de résultats crédite peu après la fin du match. C'est donc une approximation.
    Les dons admin (`/addpoints`, `/addpointsall`) ne sont tracés nulle part et ne
    peuvent pas être comptés.
    """
    gains = {}
    with _connect() as con:
        cur = con.cursor()
        # Points de message (bonus du 1er message du jour inclus : il part dans la
        # même ligne de maturation que le message qui le déclenche).
        for uid, pts in cur.execute("""
            SELECT user_id, COALESCE(SUM(points), 0)
            FROM pending_message_points
            WHERE datetime(created_at) >= datetime(?)
            GROUP BY user_id
        """, (since_iso,)).fetchall():
            gains[uid] = gains.get(uid, 0) + pts

        if include_pronos:
            for uid, pts in cur.execute("""
                SELECT p.user_id, COALESCE(SUM(p.points_gagnes), 0)
                FROM pronostics p
                JOIN matchs m ON m.id = p.match_id
                WHERE p.points_gagnes > 0
                  AND datetime(m.date_match) >= datetime(?)
                GROUP BY p.user_id
            """, (since_iso,)).fetchall():
                gains[uid] = gains.get(uid, 0) + pts
    return gains


def mass_add_points_variable(grants):
    """Crédite un montant DIFFÉRENT par joueur, en UNE transaction.

    `grants` : {user_id: points à ajouter}. Tout passe ou rien ne passe — une
    distribution à moitié appliquée serait impossible à rattraper proprement,
    puisque rien ne journalise les dons admin.
    Retourne le nombre de joueurs crédités.
    """
    if not grants:
        return 0
    with _connect() as con:
        cur = con.cursor()
        cur.executemany(
            "UPDATE users SET points = points + ? WHERE user_id = ?",
            [(int(pts), int(uid)) for uid, pts in grants.items()])
        con.commit()
        return len(grants)


def update_fragments(user_id, amount):
    """Ajoute ou retire des fragments à un utilisateur."""
    check_user(user_id)
    with _connect() as con:
        cur = con.cursor()
        cur.execute("UPDATE users SET fragments = fragments + ? WHERE user_id = ?", (amount, user_id))
        con.commit()

def update_on_message_activity(user_id, points_to_add, current_iso_time, credit=True):
    """
    Met à jour les points et l'heure du dernier message pour une activité normale.

    `credit=False` : on consomme le QUOTA quotidien et on horodate, mais on ne
    crédite pas le solde — les points partent en maturation (cf.
    `add_pending_message_points`). Le quota, lui, est brûlé dès l'écriture et
    n'est JAMAIS rendu : sans ça, supprimer un message libérerait de la place
    pour en regagner, et la triche deviendrait rentable.
    """
    check_user(user_id)
    with _connect() as con:
        cur = con.cursor()
        cur.execute("""
            UPDATE users 
            SET 
                points = points + ?, 
                daily_message_points = daily_message_points + ?,
                last_message_time = ? 
            WHERE user_id = ?
        """, (points_to_add if credit else 0, points_to_add, current_iso_time, user_id))
        con.commit()
        
def reset_daily_and_add_first_bonus(user_id, bonus_points, message_points, current_iso_time,
                                    credit=True):
    """
    Réinitialise les points quotidiens et ajoute le bonus du premier message.

    `credit=False` : la remise à zéro quotidienne a bien lieu, mais le bonus n'est
    pas crédité — il part en maturation comme les autres points de message.
    """
    check_user(user_id)
    with _connect() as con:
        cur = con.cursor()
        # On extrait la date de la chaîne de caractères ISO (ex: '2025-08-15')
        today_date = current_iso_time.split('T')[0]
        total_points_to_add = (bonus_points + message_points) if credit else 0
        cur.execute("""
            UPDATE users 
            SET 
                points = points + ?, 
                daily_message_points = ?,
                last_activity_date = ?, 
                last_message_time = ? 
            WHERE user_id = ?
        """, (total_points_to_add, message_points, today_date, current_iso_time, user_id))
        con.commit()

# === POINTS DE MESSAGE EN ATTENTE (anti-triche ecrire/supprimer) ===
# Principe : les points d'un message ne deviennent depensables qu'apres un delai de
# maturation, et seulement si le message existe toujours. Un message supprime avant
# maturation ne rapporte rien — il n'y a donc aucune fenetre pendant laquelle des
# points obtenus par un message qu'on s'apprete a effacer pourraient etre convertis
# en packs. Le quota quotidien, lui, est consomme des l'ecriture et jamais rendu.

def add_pending_message_points(message_id, user_id, channel_id, points, created_iso):
    """Enregistre des points en attente de maturation. Ignore un doublon d'id."""
    check_user(user_id)
    with _connect() as con:
        cur = con.cursor()
        cur.execute("""
            INSERT OR IGNORE INTO pending_message_points
                (message_id, user_id, channel_id, points, created_at, muri)
            VALUES (?, ?, ?, ?, ?, 0)
        """, (int(message_id), user_id, int(channel_id), int(points), created_iso))
        con.commit()

def get_due_message_points(before_iso, limit=200):
    """Lignes pas encore mûres dont le délai est écoulé, plus anciennes d'abord."""
    with _connect() as con:
        con.row_factory = sqlite3.Row
        cur = con.cursor()
        cur.execute("""
            SELECT * FROM pending_message_points
            WHERE muri = 0 AND datetime(created_at) <= datetime(?)
            ORDER BY created_at ASC LIMIT ?
        """, (before_iso, limit))
        return [dict(r) for r in cur.fetchall()]

def credit_message_points(message_id):
    """Fait mûrir une ligne : crédite le solde et la marque `muri`.

    Le passage muri 0→1 et le crédit se font dans la MÊME transaction, et la mise à
    jour est conditionnée à `muri = 0` : si deux passages de la boucle se croisent,
    le second ne crédite rien. Retourne True si le crédit a bien eu lieu.
    """
    with _connect() as con:
        cur = con.cursor()
        cur.execute("UPDATE pending_message_points SET muri = 1 WHERE message_id = ? AND muri = 0",
                    (int(message_id),))
        if cur.rowcount == 0:
            return False
        row = cur.execute("SELECT user_id, points FROM pending_message_points WHERE message_id = ?",
                          (int(message_id),)).fetchone()
        cur.execute("UPDATE users SET points = points + ? WHERE user_id = ?", (row[1], row[0]))
        con.commit()
        return True

def revoke_message_points(message_ids):
    """Retire les points d'un ou plusieurs messages supprimés.

    - pas encore mûrs : la ligne disparaît, rien n'avait été crédité, rien à reprendre ;
    - déjà mûrs : on débite le solde (il peut devenir négatif — le joueur ne pourra
      plus rien acheter tant qu'il n'est pas repassé positif).
    Le quota quotidien n'est jamais restitué. Retourne {user_id: points_retires}.
    """
    ids = [int(m) for m in (message_ids if isinstance(message_ids, (list, tuple, set)) else [message_ids])]
    if not ids:
        return {}
    marks = ",".join("?" * len(ids))
    debits = {}
    with _connect() as con:
        cur = con.cursor()
        rows = cur.execute(
            f"SELECT message_id, user_id, points, muri FROM pending_message_points "
            f"WHERE message_id IN ({marks})", ids).fetchall()
        for _mid, uid, pts, muri in rows:
            if muri:
                cur.execute("UPDATE users SET points = points - ? WHERE user_id = ?", (pts, uid))
                debits[uid] = debits.get(uid, 0) + pts
        cur.execute(f"DELETE FROM pending_message_points WHERE message_id IN ({marks})", ids)
        con.commit()
    return debits

def get_pending_message_points(user_id):
    """Total des points encore en maturation pour ce joueur (affichage `/points`)."""
    with _connect() as con:
        cur = con.cursor()
        row = cur.execute("""
            SELECT COALESCE(SUM(points), 0) FROM pending_message_points
            WHERE user_id = ? AND muri = 0
        """, (user_id,)).fetchone()
        return row[0] if row else 0

def purge_matured_message_points(before_iso):
    """Oublie les lignes mûres et anciennes : passé ce délai, supprimer son message
    ne reprend plus rien. Empêche la table de grossir indéfiniment."""
    with _connect() as con:
        cur = con.cursor()
        cur.execute("""
            DELETE FROM pending_message_points
            WHERE muri = 1 AND datetime(created_at) < datetime(?)
        """, (before_iso,))
        con.commit()
        return cur.rowcount

def add_pack(user_id, amount=1):
    check_user(user_id)
    with _connect() as con:
        cur = con.cursor()
        cur.execute("UPDATE users SET packs = packs + ? WHERE user_id = ?", (amount, user_id))
        con.commit()

def remove_pack(user_id, amount=1):
    """⚠️ Décrémente sans vérifier le solde : le total peut devenir négatif.
    Pour toute dépense déclenchée par un utilisateur, utiliser consume_packs()."""
    check_user(user_id)
    with _connect() as con:
        cur = con.cursor()
        cur.execute("UPDATE users SET packs = MAX(0, packs - ?) WHERE user_id = ?", (amount, user_id))
        con.commit()


def consume_packs(user_id, amount=1):
    """Dépense ATOMIQUE de packs. Renvoie True si le solde a bien été débité.

    Le UPDATE conditionnel fait office de verrou : deux clics simultanés sur le
    bouton d'ouverture ne peuvent plus débiter deux fois le même pack (rowcount
    vaut 0 pour le second).
    """
    if amount <= 0:
        return False
    check_user(user_id)
    with _connect() as con:
        cur = con.cursor()
        cur.execute(
            "UPDATE users SET packs = packs - ? WHERE user_id = ? AND packs >= ?",
            (amount, user_id, amount)
        )
        con.commit()
        return cur.rowcount > 0


def get_packs(user_id):
    """Solde de packs uniquement (évite un SELECT * sur toute la ligne users)."""
    check_user(user_id)
    with _connect() as con:
        cur = con.cursor()
        row = cur.execute("SELECT packs FROM users WHERE user_id = ?", (user_id,)).fetchone()
        return row[0] if row else 0


def add_card_to_collection(user_id, card_id):
    check_user(user_id)
    with _connect() as con:
        cur = con.cursor()
        cur.execute("INSERT INTO user_cards (user_id, card_id) VALUES (?, ?)", (user_id, card_id))
        con.commit()

def get_user_collection(user_id):
    check_user(user_id)
    with _connect() as con:
        cur = con.cursor()
        cur.execute("SELECT card_id FROM user_cards WHERE user_id = ?", (user_id,))
        return [item[0] for item in cur.fetchall()]

def reset_and_set_collection(user_id, unique_card_ids):
    """Supprime la collection actuelle et la remplace par une nouvelle liste d'IDs (pour le recyclage)."""
    check_user(user_id)
    with _connect() as con:
        cur = con.cursor()
        cur.execute("DELETE FROM user_cards WHERE user_id = ?", (user_id,))
        if unique_card_ids:
            new_collection_data = [(user_id, card_id) for card_id in unique_card_ids]
            cur.executemany("INSERT INTO user_cards (user_id, card_id) VALUES (?, ?)", new_collection_data)
        con.commit()

def get_leaderboard_data(valid_card_ids=None, limit=10):
    """
    Récupère les données pour le classement (top collectionneurs).
    valid_card_ids: Liste d'IDs de cartes valides pour ne compter que celles-ci.
    limit: Nombre maximum de résultats.
    """
    with _connect() as con:
        cur = con.cursor()
        
        query = """
            SELECT user_id, COUNT(DISTINCT card_id) as unique_cards
            FROM user_cards
        """
        
        params = []
        if valid_card_ids:
            # On filtre pour ne garder que les cartes existantes
            placeholders = ','.join('?' for _ in valid_card_ids)
            query += f" WHERE card_id IN ({placeholders})"
            params.extend(valid_card_ids)
            
        query += f"""
            GROUP BY user_id
            ORDER BY unique_cards DESC
            LIMIT ?
        """
        params.append(limit)
        
        cur.execute(query, params)
        return cur.fetchall()

# === NOUVELLES FONCTIONS POUR LES PRONOSTICS ===

def create_or_update_journee(numero, date_debut, date_fin):
    """Crée ou met à jour une journée."""
    with _connect() as con:
        cur = con.cursor()
        # Vérifier si la journée existe déjà
        cur.execute("SELECT id FROM journees WHERE numero = ?", (numero,))
        existing = cur.fetchone()
        
        if existing:
            # Mise à jour
            cur.execute("""
                UPDATE journees 
                SET date_debut = ?, date_fin = ?
                WHERE numero = ?
            """, (date_debut, date_fin, numero))
            return existing[0]
        else:
            # Création
            cur.execute("""
                INSERT INTO journees (numero, date_debut, date_fin)
                VALUES (?, ?, ?)
            """, (numero, date_debut, date_fin))
            con.commit()
            return cur.lastrowid

def get_active_journee():
    """Récupère la journée active."""
    with _connect() as con:
        con.row_factory = sqlite3.Row
        cur = con.cursor()
        cur.execute("""
            SELECT * FROM journees 
            WHERE is_active = 1 
            ORDER BY date_debut DESC 
            LIMIT 1
        """)
        return cur.fetchone()

def create_match(journee_id, event_id, discord_event_id, equipe1, equipe2, date_match, competition=None):
    """Crée un match dans la base de données."""
    with _connect() as con:
        cur = con.cursor()
        cur.execute("""
            INSERT OR IGNORE INTO matchs 
            (journee_id, event_id, discord_event_id, equipe1, equipe2, date_match, competition)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (journee_id, event_id, discord_event_id, equipe1, equipe2, date_match, competition))
        con.commit()
        return cur.lastrowid

def get_match_by_event_id(event_id):
    """Récupère un match par son event_id Livescore."""
    with _connect() as con:
        con.row_factory = sqlite3.Row
        cur = con.cursor()
        cur.execute("SELECT * FROM matchs WHERE event_id = ?", (event_id,))
        return cur.fetchone()

def get_match_by_id(match_id):
    """Récupère un match par son ID."""
    with _connect() as con:
        con.row_factory = sqlite3.Row
        cur = con.cursor()
        cur.execute("SELECT * FROM matchs WHERE id = ?", (match_id,))
        return cur.fetchone()

def update_match_result(match_id, resultat, score):
    """Met à jour le résultat d'un match."""
    with _connect() as con:
        cur = con.cursor()
        cur.execute("""
            UPDATE matchs 
            SET resultat = ?, score = ?
            WHERE id = ?
        """, (resultat, score, match_id))
        con.commit()

def update_match_competition(match_id, competition):
    """Met à jour la compétition d'un match (pour la migration)."""
    with _connect() as con:
        cur = con.cursor()
        cur.execute("UPDATE matchs SET competition = ? WHERE id = ?", (competition, match_id))
        con.commit()

def save_prono_message(match_id, message_id, channel_id):
    """Sauvegarde l'ID du message de pronostic."""
    with _connect() as con:
        cur = con.cursor()
        cur.execute("""
            INSERT OR REPLACE INTO prono_messages 
            (match_id, message_id, channel_id)
            VALUES (?, ?, ?)
        """, (match_id, message_id, channel_id))
        con.commit()

def get_prono_message(match_id):
    """Récupère les infos du message de pronostic."""
    with _connect() as con:
        con.row_factory = sqlite3.Row
        cur = con.cursor()
        cur.execute("SELECT * FROM prono_messages WHERE match_id = ?", (match_id,))
        return cur.fetchone()

def save_or_update_pronostic(user_id, match_id, pronostic):
    """Sauvegarde ou met à jour un pronostic."""
    check_user(user_id)
    with _connect() as con:
        cur = con.cursor()
        # Vérifier si le pronostic existe déjà
        cur.execute("""
            SELECT id FROM pronostics 
            WHERE user_id = ? AND match_id = ?
        """, (user_id, match_id))
        existing = cur.fetchone()
        
        if existing:
            # Mise à jour
            cur.execute("""
                UPDATE pronostics 
                SET pronostic = ?, updated_at = CURRENT_TIMESTAMP
                WHERE user_id = ? AND match_id = ?
            """, (pronostic, user_id, match_id))
        else:
            # Création
            cur.execute("""
                INSERT INTO pronostics (user_id, match_id, pronostic)
                VALUES (?, ?, ?)
            """, (user_id, match_id, pronostic))
        con.commit()

def get_user_pronostic(user_id, match_id):
    """Récupère le pronostic d'un utilisateur pour un match."""
    with _connect() as con:
        con.row_factory = sqlite3.Row
        cur = con.cursor()
        cur.execute("""
            SELECT * FROM pronostics 
            WHERE user_id = ? AND match_id = ?
        """, (user_id, match_id))
        return cur.fetchone()

def get_match_pronostics(match_id):
    """Récupère tous les pronostics pour un match."""
    with _connect() as con:
        cur = con.cursor()
        cur.execute("""
            SELECT user_id, pronostic FROM pronostics 
            WHERE match_id = ?
        """, (match_id,))
        return cur.fetchall()

def attribute_points_for_match(match_id, resultat, points_par_bon_prono=50):
    """Attribue les points aux bons pronostiqueurs. IDEMPOTENT.

    Le garde-fou `points_gagnes = 0` évite le double crédit si la boucle
    automatique et un !checkresults manuel traitent le même match en parallèle.
    """
    with _connect() as con:
        cur = con.cursor()
        # 1. Créditer les utilisateurs AVANT de marquer les pronos comme payés,
        #    sinon la sous-requête ne trouve plus personne.
        cur.execute("""
            UPDATE users 
            SET points = points + ?
            WHERE user_id IN (
                SELECT user_id FROM pronostics 
                WHERE match_id = ? AND pronostic = ? AND points_gagnes = 0
            )
        """, (points_par_bon_prono, match_id, resultat))

        # 2. Marquer les pronos comme payés (verrou contre un second passage)
        cur.execute("""
            UPDATE pronostics 
            SET points_gagnes = ?
            WHERE match_id = ? AND pronostic = ? AND points_gagnes = 0
        """, (points_par_bon_prono, match_id, resultat))

        con.commit()
        return cur.rowcount

def get_journee_leaderboard(journee_id):
    """Récupère le classement des pronostiqueurs pour une journée."""
    with _connect() as con:
        cur = con.cursor()
        cur.execute("""
            SELECT 
                p.user_id,
                COUNT(CASE WHEN p.points_gagnes > 0 THEN 1 END) as bons_pronos,
                SUM(p.points_gagnes) as total_points
            FROM pronostics p
            JOIN matchs m ON p.match_id = m.id
            WHERE m.journee_id = ?
            GROUP BY p.user_id
            ORDER BY bons_pronos DESC, total_points DESC
        """, (journee_id,))
        return cur.fetchall()

def get_matchs_journee(journee_id):
    """Récupère tous les matchs d'une journée."""
    with _connect() as con:
        con.row_factory = sqlite3.Row
        cur = con.cursor()
        cur.execute("""
            SELECT * FROM matchs 
            WHERE journee_id = ?
            ORDER BY date_match
        """, (journee_id,))
        return cur.fetchall()

def close_match_pronostics(match_id):
    """Ferme les pronostics pour un match."""
    with _connect() as con:
        cur = con.cursor()
        cur.execute("UPDATE matchs SET pronos_fermes = 1 WHERE id = ?", (match_id,))
        con.commit()

def mark_journee_rappel_sent(journee_id):
    """Marque qu'un rappel a été envoyé pour une journée."""
    with _connect() as con:
        cur = con.cursor()
        cur.execute("UPDATE journees SET rappel_envoye = 1 WHERE id = ?", (journee_id,))
        con.commit()

def get_journees_for_rappel():
    """Récupère les journées nécessitant un rappel (24h avant)."""
    with _connect() as con:
        con.row_factory = sqlite3.Row
        cur = con.cursor()
        # Chaîne explicite (l'adaptateur datetime de sqlite3 est déprécié en 3.12+)
        # et datetime() des deux côtés pour normaliser les formats ISO ('T' vs espace).
        tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
        cur.execute("""
            SELECT * FROM journees
            WHERE rappel_envoye = 0
            AND datetime(date_debut) <= datetime(?)
            AND is_active = 1
        """, (tomorrow,))
        return cur.fetchall()

def determine_journee_from_matches(matches):
    """Détermine automatiquement le numéro de journée à partir des matchs."""
    if not matches:
        return None
        
    # Logique simple : grouper par semaine
    match_dates = [match['start_time_utc'] for match in matches]
    min_date = min(match_dates)
    
    # Récupérer la dernière journée
    with _connect() as con:
        cur = con.cursor()
        cur.execute("SELECT MAX(numero) FROM journees")
        last_numero = cur.fetchone()[0] or 0
        
        # Vérifier si ces matchs appartiennent à une journée existante
        cur.execute("""
            SELECT id, numero FROM journees 
            WHERE date_debut <= ? AND date_fin >= ?
        """, (min_date, min_date))
        existing = cur.fetchone()
        
        if existing:
            return existing[0], existing[1]
        else:
            # Créer une nouvelle journée
            max_date = max(match_dates)
            new_numero = last_numero + 1
            journee_id = create_or_update_journee(new_numero, min_date, max_date)
            return journee_id, new_numero

def get_matches_to_check_results(since_date):
    """Récupère les matchs PASSÉS sans résultat depuis une certaine date."""
    with _connect() as con:
        con.row_factory = sqlite3.Row
        cur = con.cursor()
        # On ajoute la condition que la date du match doit être passée
        now_utc_iso = datetime.now(timezone.utc).isoformat()
        cur.execute("""
            SELECT * FROM matchs
            WHERE resultat IS NULL 
            AND date_match >= ? 
            AND date_match < ?
            ORDER BY date_match ASC
        """, (since_date.isoformat(), now_utc_iso))
        return cur.fetchall()

def get_user_correct_pronostics(user_id):
    """
    Récupère tous les pronostics corrects d'un utilisateur avec les détails du match.
    """
    with _connect() as con:
        con.row_factory = sqlite3.Row
        cur = con.cursor()
        # On retire la colonne points_obtenus qui n'existe pas
        cur.execute("""
            SELECT
                m.equipe1,
                m.equipe2,
                m.date_match,
                m.resultat
            FROM pronostics p
            JOIN matchs m ON p.match_id = m.id
            WHERE p.user_id = ? AND p.pronostic = m.resultat AND m.resultat IS NOT NULL
            ORDER BY m.date_match DESC
        """, (user_id,))
        return cur.fetchall()

def set_advent_pack_opened(user_id, date_str):
    """Enregistre que l'utilisateur a ouvert son pack de l'avent pour cette date."""
    check_user(user_id)
    with _connect() as con:
        cur = con.cursor()
        cur.execute("UPDATE users SET last_advent_pack_date = ? WHERE user_id = ?", (date_str, user_id))
        con.commit()

def get_general_leaderboard(points_per_win, limit=10, competition=None):
    """
    Récupère le classement général des pronostics basé sur tous les matchs terminés.

    Args:
        points_per_win (int): Le nombre de points pour un pronostic correct.
        limit (int): Le nombre maximum de joueurs à retourner.

    Returns:
        list: Une liste de dictionnaires contenant user_id, bons_pronos, et total_points.
    """
    with _connect() as con:
        con.row_factory = sqlite3.Row
        cur = con.cursor()
        
        # Cette requête compte les pronostics corrects pour chaque utilisateur
        # sur tous les matchs qui ont un résultat enregistré.
        query = """
            SELECT
                p.user_id,
                COUNT(p.user_id) AS bons_pronos,
                COUNT(p.user_id) * ? AS total_points
            FROM pronostics p
            JOIN matchs m ON p.match_id = m.id
            WHERE p.pronostic = m.resultat AND m.resultat IS NOT NULL
        """
        
        params = [points_per_win]
        
        if competition:
            # Filtrer par compétition
            # Note: Si des anciens matchs ont NULL, ils ne seront pas comptés ici si on filtre.
            query += " AND m.competition = ?"
            params.append(competition)

        query += """    
            GROUP BY p.user_id
            ORDER BY total_points DESC, bons_pronos DESC
            LIMIT ?
        """
        params.append(limit)
        
        cur.execute(query, params)
        
        leaderboard = cur.fetchall()
        return [dict(row) for row in leaderboard]

def update_match_discord_event_id(match_id, discord_event_id):
    """Met à jour l'ID de l'événement Discord pour un match existant."""
    with _connect() as con:
        cur = con.cursor()
        cur.execute("UPDATE matchs SET discord_event_id = ? WHERE id = ?", (discord_event_id, match_id))
        con.commit()

def update_match_time(match_id, new_time):
    """Met à jour l'horaire d'un match."""
    with _connect() as con:
        cur = con.cursor()
        cur.execute("UPDATE matchs SET date_match = ? WHERE id = ?", (new_time.isoformat(), match_id))
        con.commit()

def fix_null_competitions(default_name="Starligue"):
    """
    Attribue un nom de compétition par défaut à tous les matchs 
    qui n'en ont pas (pour récupérer l'historique).
    """
    with _connect() as con:
        cur = con.cursor()
        # On met à jour les matchs où la compétition est NULL ou vide
        cur.execute("""
            UPDATE matchs 
            SET competition = ? 
            WHERE competition IS NULL OR competition = ''
        """, (default_name,))
        changes = cur.rowcount
        con.commit()
        return changes

def mass_give_card_if_missing(card_id):
    """
    Donne une carte spécifique à TOUS les utilisateurs enregistrés
    qui ne possèdent pas encore cette carte.
    Retourne le nombre de cartes distribuées.
    """
    with _connect() as con:
        cur = con.cursor()
        
        # Cette requête sélectionne tous les user_id de la table users
        # qui ne sont PAS dans la liste des gens possédant déjà la carte.
        # Puis elle insère la carte pour ces gens-là.
        cur.execute("""
            INSERT INTO user_cards (user_id, card_id)
            SELECT user_id, ?
            FROM users
            WHERE user_id NOT IN (
                SELECT user_id FROM user_cards WHERE card_id = ?
            )
        """, (card_id, card_id))
        
        con.commit()
        return cur.rowcount

# === SAISON 2 : ÉCHANGES DE CARTES ===

def get_user_cards_with_rowid(user_id):
    """Retourne la liste des exemplaires possédés : [(rowid, card_id), ...].
    Le rowid identifie un exemplaire précis (utile pour échanger un doublon donné)."""
    check_user(user_id)
    with _connect() as con:
        cur = con.cursor()
        cur.execute("SELECT id, card_id FROM user_cards WHERE user_id = ? ORDER BY id", (user_id,))
        return cur.fetchall()

def execute_trade(user_a, rowids_a, user_b, rowids_b):
    """Échange ATOMIQUE de N cartes (de A vers B) contre M cartes (de B vers A).
    Les listes contiennent des rowids de user_cards. Tout est revérifié DANS la
    transaction : si un exemplaire ne correspond plus au bon propriétaire, on
    annule tout et on retourne False (aucune carte n'est dupliquée ni perdue)."""
    rowids_a = list(rowids_a)
    rowids_b = list(rowids_b)
    # Sécurités basiques avant d'ouvrir la transaction
    if not rowids_a or not rowids_b:
        return False
    if len(set(rowids_a)) != len(rowids_a) or len(set(rowids_b)) != len(rowids_b):
        return False
    if set(rowids_a) & set(rowids_b):
        return False

    with _connect() as con:
        cur = con.cursor()
        # Revérifier la propriété de CHAQUE exemplaire dans la transaction
        for rid in rowids_a:
            if not cur.execute("SELECT 1 FROM user_cards WHERE id = ? AND user_id = ?", (rid, user_a)).fetchone():
                return False
        for rid in rowids_b:
            if not cur.execute("SELECT 1 FROM user_cards WHERE id = ? AND user_id = ?", (rid, user_b)).fetchone():
                return False
        # Transfert : on réaffecte simplement le propriétaire de chaque ligne
        cur.executemany("UPDATE user_cards SET user_id = ? WHERE id = ?", [(user_b, r) for r in rowids_a])
        cur.executemany("UPDATE user_cards SET user_id = ? WHERE id = ?", [(user_a, r) for r in rowids_b])
        con.commit()
        return True

def log_trade(user_a, card_ids_a, user_b, card_ids_b):
    """Enregistre un échange réalisé (pour traçabilité / gestion des litiges)."""
    import json as _json
    with _connect() as con:
        cur = con.cursor()
        cur.execute(
            "INSERT INTO trade_log (user_a, user_b, cards_a, cards_b) VALUES (?, ?, ?, ?)",
            (user_a, user_b, _json.dumps(list(card_ids_a)), _json.dumps(list(card_ids_b)))
        )
        con.commit()
        return cur.lastrowid

def remove_extra_copies(user_id, card_ids):
    """Recyclage sélectif : pour chaque card_id donné, supprime tous les
    exemplaires SAUF UN. Retourne {card_id: nb_supprimés}."""
    removed = {}
    with _connect() as con:
        cur = con.cursor()
        for cid in card_ids:
            rows = cur.execute(
                "SELECT id FROM user_cards WHERE user_id = ? AND card_id = ? ORDER BY id",
                (user_id, cid)
            ).fetchall()
            if len(rows) > 1:
                to_delete = [(r[0],) for r in rows[1:]]  # on garde le 1er exemplaire
                cur.executemany("DELETE FROM user_cards WHERE id = ?", to_delete)
                removed[cid] = len(to_delete)
        con.commit()
    return removed

# === SAISON 2 : ELO (duels) ===

def get_user_elo(user_id):
    """Retourne l'Elo de l'utilisateur (1000 par défaut)."""
    check_user(user_id)
    with _connect() as con:
        cur = con.cursor()
        row = cur.execute("SELECT elo FROM users WHERE user_id = ?", (user_id,)).fetchone()
        return row[0] if row and row[0] is not None else 1000

def set_user_elo(user_id, elo):
    """Fixe l'Elo d'un utilisateur."""
    check_user(user_id)
    with _connect() as con:
        cur = con.cursor()
        cur.execute("UPDATE users SET elo = ? WHERE user_id = ?", (int(elo), user_id))
        con.commit()

def record_duel(joueur1, joueur2, score1, score2, gagnant, classe,
                elo1_before, elo2_before, elo1_after, elo2_after, lineup1, lineup2):
    """Enregistre un duel joué. lineup1/lineup2 : dicts {slot: card_id} (sérialisés en JSON)."""
    import json as _json
    with _connect() as con:
        cur = con.cursor()
        cur.execute("""
            INSERT INTO duels
                (joueur1, joueur2, score1, score2, gagnant, classe,
                 elo1_before, elo2_before, elo1_after, elo2_after, lineup1, lineup2)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (joueur1, joueur2, score1, score2, gagnant, 1 if classe else 0,
              elo1_before, elo2_before, elo1_after, elo2_after,
              _json.dumps(lineup1), _json.dumps(lineup2)))
        con.commit()
        return cur.lastrowid

def count_ranked_attacks_between(attaquant, defenseur, since_iso):
    """Nb d'ATTAQUES classées de `attaquant` CONTRE `defenseur` depuis `since_iso`.

    Anti-farm de paire, mais DIRECTIONNEL : en duel asymétrique, se faire attaquer
    trois fois le matin ne doit pas empêcher de riposter l'après-midi — la victime
    n'a rien fait pour mériter d'être bloquée.
    """
    with _connect() as con:
        cur = con.cursor()
        return cur.execute("""
            SELECT COUNT(*) FROM duels
            WHERE classe = 1 AND datetime(created_at) >= datetime(?)
              AND joueur1 = ? AND joueur2 = ?
        """, (since_iso, attaquant, defenseur)).fetchone()[0]

def count_ranked_duels_for(user_id, since_iso):
    """Nb de duels CLASSÉS joués par un utilisateur depuis `since_iso`, attaque ET
    défense confondues. Vue globale (stats, modération) : pour un plafond, préférer
    `count_ranked_attacks_for` ou `count_defenses_for`, qui séparent les deux rôles."""
    with _connect() as con:
        cur = con.cursor()
        return cur.execute("""
            SELECT COUNT(*) FROM duels
            WHERE classe = 1 AND datetime(created_at) >= datetime(?)
              AND (joueur1 = ? OR joueur2 = ?)
        """, (since_iso, user_id, user_id)).fetchone()[0]

def count_ranked_attacks_for(user_id, since_iso):
    """Nb d'ATTAQUES classées lancées par un joueur depuis `since_iso`.

    Duels asymétriques : `joueur1` est l'attaquant. Le plafond quotidien de
    récompenses porte sur ce qu'on lance, jamais sur ce qu'on subit — sinon se faire
    attaquer dix fois pendant la nuit épuiserait son propre quota du lendemain.
    """
    with _connect() as con:
        cur = con.cursor()
        return cur.execute("""
            SELECT COUNT(*) FROM duels
            WHERE classe = 1 AND datetime(created_at) >= datetime(?)
              AND joueur1 = ?
        """, (since_iso, user_id)).fetchone()[0]

def count_defenses_for(user_id, since_iso, ranked_only=True):
    """Nb de duels SUBIS en défense (le joueur était la cible) depuis `since_iso`.

    Sert à deux plafonds distincts : les récompenses de défense (qu'on ne veut pas
    voir s'accumuler passivement) et les MP de compte-rendu (qu'on ne veut pas voir
    spammer une cible populaire).
    """
    clause = "classe = 1 AND " if ranked_only else ""
    with _connect() as con:
        cur = con.cursor()
        return cur.execute(f"""
            SELECT COUNT(*) FROM duels
            WHERE {clause}datetime(created_at) >= datetime(?)
              AND joueur2 = ?
        """, (since_iso, user_id)).fetchone()[0]

def get_user_defenses(user_id, limit=10):
    """Dernières attaques subies par un joueur (il était `joueur2`), plus récentes d'abord."""
    with _connect() as con:
        con.row_factory = sqlite3.Row
        cur = con.cursor()
        cur.execute("""
            SELECT * FROM duels
            WHERE joueur2 = ?
            ORDER BY id DESC LIMIT ?
        """, (user_id, limit))
        return [dict(r) for r in cur.fetchall()]

def get_last_duel_lineup(user_id):
    """Dernière compo que le joueur a lui-même COMPOSÉE : {slot: card_id | None}, ou None.

    On ne regarde que les duels où il était `joueur1`, c'est-à-dire l'attaquant : en
    duel asymétrique, la compo du défenseur (`lineup2`) est générée automatiquement,
    la reproposer en préremplissage écraserait le dernier choix réel du joueur.
    """
    import json as _json
    with _connect() as con:
        cur = con.cursor()
        row = cur.execute("""
            SELECT lineup1 FROM duels
            WHERE joueur1 = ?
            ORDER BY id DESC LIMIT 1
        """, (user_id,)).fetchone()
    if not row:
        return None
    try:
        return _json.loads(row[0]) if row[0] else None
    except ValueError:
        return None

def get_user_duels(user_id, limit=10):
    """Derniers duels d'un joueur (classés et amicaux), plus récents d'abord."""
    with _connect() as con:
        con.row_factory = sqlite3.Row
        cur = con.cursor()
        cur.execute("""
            SELECT * FROM duels
            WHERE joueur1 = ? OR joueur2 = ?
            ORDER BY id DESC LIMIT ?
        """, (user_id, user_id, limit))
        return [dict(r) for r in cur.fetchall()]

def get_duel_leaderboard(limit=10):
    """Classement Elo des joueurs ayant lancé au moins une ATTAQUE classée.

    L'Elo ne bouge qu'à l'attaque (duels asymétriques) : classer quelqu'un qui n'a
    jamais attaqué le figerait à 1000 au milieu du tableau sans rien vouloir dire.
    On remonte quand même son bilan défensif, qui est l'autre moitié de son jeu.
    """
    with _connect() as con:
        con.row_factory = sqlite3.Row
        cur = con.cursor()
        cur.execute("""
            SELECT u.user_id, u.elo,
                (SELECT COUNT(*) FROM duels d
                   WHERE d.classe = 1 AND d.joueur1 = u.user_id) AS matchs,
                (SELECT COUNT(*) FROM duels d
                   WHERE d.classe = 1 AND d.joueur1 = u.user_id AND d.gagnant = u.user_id) AS victoires,
                (SELECT COUNT(*) FROM duels d
                   WHERE d.classe = 1 AND d.joueur2 = u.user_id) AS defenses,
                (SELECT COUNT(*) FROM duels d
                   WHERE d.classe = 1 AND d.joueur2 = u.user_id AND d.gagnant = u.user_id) AS defenses_tenues
            FROM users u
            WHERE EXISTS (SELECT 1 FROM duels d
                          WHERE d.classe = 1 AND d.joueur1 = u.user_id)
            ORDER BY u.elo DESC
            LIMIT ?
        """, (limit,))
        return [dict(r) for r in cur.fetchall()]
