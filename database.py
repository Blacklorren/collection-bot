import sqlite3
import os
from datetime import datetime, date, timedelta, timezone
from typing import List, Tuple, Dict, Any

# Le dossier où les données persistantes seront stockées
DATA_DIR = '/data'

# On garde le nom de variable DB_NAME, mais on lui assigne le chemin complet
DB_NAME = os.path.join(DATA_DIR, 'collection.db')
# La valeur de DB_NAME est maintenant '/data/collection.db'

def initialize_database():
    """Crée les tables de la base de données si elles n'existent pas."""
    # S'assurer que le dossier /data existe (au cas où)
    os.makedirs(DATA_DIR, exist_ok=True)
    with sqlite3.connect(DB_NAME) as con:
        cur = con.cursor()
        
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
        
        con.commit()

# === FONCTIONS EXISTANTES POUR LE JEU DE CARTES ===

def get_week_dates(for_date):
    """Calcule les dates de début (lundi) et de fin (dimanche) pour la semaine d'une date donnée."""
    start_of_week = for_date - timedelta(days=for_date.weekday())
    end_of_week = start_of_week + timedelta(days=6)
    return start_of_week, end_of_week

def get_matches_in_date_range(start_date, end_date):
    """Récupère tous les matchs dans un intervalle de dates donné."""
    with sqlite3.connect(DB_NAME) as con:
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
    with sqlite3.connect(DB_NAME) as con:
        con.row_factory = sqlite3.Row
        cur = con.cursor()
        cur.execute(query, match_ids)
        return cur.fetchall()

# La fonction suivante est modifiée pour ne plus dépendre des journées
def create_match(journee_id, event_id, discord_event_id, equipe1, equipe2, date_match):
    """Crée un match dans la base de données. journee_id peut être None."""
    try:
        with sqlite3.connect(DB_NAME) as con:
            cur = con.cursor()
            # On utilise désormais journee_id de manière facultative
            cur.execute("""
                INSERT INTO matchs (journee_id, event_id, discord_event_id, equipe1, equipe2, date_match)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (journee_id, event_id, discord_event_id, equipe1, equipe2, date_match.isoformat()))
            con.commit()
            return cur.lastrowid
    except sqlite3.IntegrityError:
        return None # Le match existe déjà

def wipe_all_user_data():
    """
    Vide toutes les données liées aux utilisateurs, collections, et pronostics.
    Cette fonction est conçue pour une remise à zéro complète du jeu.
    Elle est maintenant plus robuste et ne plantera pas si une table est manquante.
    """
    print("⚠️  [DATABASE] Lancement de la procédure de remise à zéro des données...")
    try:
        with sqlite3.connect(DB_NAME) as con:
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

def check_user(user_id):
    """Vérifie si un utilisateur existe dans la DB, sinon le crée."""
    with sqlite3.connect(DB_NAME) as con:
        cur = con.cursor()
        cur.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,))
        if cur.fetchone() is None:
            cur.execute("INSERT INTO users (user_id, points, packs) VALUES (?, 100, 1)", (user_id,))
            con.commit()

def set_onboarding_received(user_id):
    """Marque un utilisateur comme ayant reçu le message d'accueil."""
    check_user(user_id)
    with sqlite3.connect(DB_NAME) as con:
        cur = con.cursor()
        cur.execute("UPDATE users SET has_received_onboarding = 1 WHERE user_id = ?", (user_id,))
        con.commit()

def get_user_data(user_id):
    check_user(user_id)
    with sqlite3.connect(DB_NAME) as con:
        con.row_factory = sqlite3.Row
        cur = con.cursor()
        cur.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
        return cur.fetchone()

def update_points(user_id, amount):
    check_user(user_id)
    with sqlite3.connect(DB_NAME) as con:
        cur = con.cursor()
        cur.execute("UPDATE users SET points = points + ? WHERE user_id = ?", (amount, user_id))
        con.commit()

def update_fragments(user_id, amount):
    """Ajoute ou retire des fragments à un utilisateur."""
    check_user(user_id)
    with sqlite3.connect(DB_NAME) as con:
        cur = con.cursor()
        cur.execute("UPDATE users SET fragments = fragments + ? WHERE user_id = ?", (amount, user_id))
        con.commit()

def update_on_message_activity(user_id, points_to_add, current_iso_time):
    """
    Met à jour les points et l'heure du dernier message pour une activité normale.
    Version corrigée qui accepte le timestamp en argument.
    """
    check_user(user_id)
    with sqlite3.connect(DB_NAME) as con:
        cur = con.cursor()
        cur.execute("""
            UPDATE users 
            SET 
                points = points + ?, 
                daily_message_points = daily_message_points + ?,
                last_message_time = ? 
            WHERE user_id = ?
        """, (points_to_add, points_to_add, current_iso_time, user_id))
        con.commit()
        
def reset_daily_and_add_first_bonus(user_id, bonus_points, message_points, current_iso_time):
    """
    Réinitialise les points quotidiens et ajoute le bonus du premier message.
    Version corrigée qui accepte le timestamp en argument.
    """
    check_user(user_id)
    with sqlite3.connect(DB_NAME) as con:
        cur = con.cursor()
        # On extrait la date de la chaîne de caractères ISO (ex: '2025-08-15')
        today_date = current_iso_time.split('T')[0]
        total_points_to_add = bonus_points + message_points
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

def add_pack(user_id, amount=1):
    check_user(user_id)
    with sqlite3.connect(DB_NAME) as con:
        cur = con.cursor()
        cur.execute("UPDATE users SET packs = packs + ? WHERE user_id = ?", (amount, user_id))
        con.commit()

def remove_pack(user_id, amount=1):
    check_user(user_id)
    with sqlite3.connect(DB_NAME) as con:
        cur = con.cursor()
        cur.execute("UPDATE users SET packs = packs - ? WHERE user_id = ?", (amount, user_id))
        con.commit()
        
def add_card_to_collection(user_id, card_id):
    check_user(user_id)
    with sqlite3.connect(DB_NAME) as con:
        cur = con.cursor()
        cur.execute("INSERT INTO user_cards (user_id, card_id) VALUES (?, ?)", (user_id, card_id))
        con.commit()

def get_user_collection(user_id):
    check_user(user_id)
    with sqlite3.connect(DB_NAME) as con:
        cur = con.cursor()
        cur.execute("SELECT card_id FROM user_cards WHERE user_id = ?", (user_id,))
        return [item[0] for item in cur.fetchall()]

def reset_and_set_collection(user_id, unique_card_ids):
    """Supprime la collection actuelle et la remplace par une nouvelle liste d'IDs (pour le recyclage)."""
    check_user(user_id)
    with sqlite3.connect(DB_NAME) as con:
        cur = con.cursor()
        cur.execute("DELETE FROM user_cards WHERE user_id = ?", (user_id,))
        if unique_card_ids:
            new_collection_data = [(user_id, card_id) for card_id in unique_card_ids]
            cur.executemany("INSERT INTO user_cards (user_id, card_id) VALUES (?, ?)", new_collection_data)
        con.commit()

def get_leaderboard_data():
    """Récupère les données pour le classement (top collectionneurs)."""
    with sqlite3.connect(DB_NAME) as con:
        cur = con.cursor()
        cur.execute("""
            SELECT user_id, COUNT(DISTINCT card_id) as unique_cards
            FROM user_cards
            GROUP BY user_id
            ORDER BY unique_cards DESC
            LIMIT 10
        """)
        return cur.fetchall()

# === NOUVELLES FONCTIONS POUR LES PRONOSTICS ===

def create_or_update_journee(numero, date_debut, date_fin):
    """Crée ou met à jour une journée."""
    with sqlite3.connect(DB_NAME) as con:
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
    with sqlite3.connect(DB_NAME) as con:
        con.row_factory = sqlite3.Row
        cur = con.cursor()
        cur.execute("""
            SELECT * FROM journees 
            WHERE is_active = 1 
            ORDER BY date_debut DESC 
            LIMIT 1
        """)
        return cur.fetchone()

def create_match(journee_id, event_id, discord_event_id, equipe1, equipe2, date_match):
    """Crée un match dans la base de données."""
    with sqlite3.connect(DB_NAME) as con:
        cur = con.cursor()
        cur.execute("""
            INSERT OR IGNORE INTO matchs 
            (journee_id, event_id, discord_event_id, equipe1, equipe2, date_match)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (journee_id, event_id, discord_event_id, equipe1, equipe2, date_match))
        con.commit()
        return cur.lastrowid

def get_match_by_event_id(event_id):
    """Récupère un match par son event_id Livescore."""
    with sqlite3.connect(DB_NAME) as con:
        con.row_factory = sqlite3.Row
        cur = con.cursor()
        cur.execute("SELECT * FROM matchs WHERE event_id = ?", (event_id,))
        return cur.fetchone()

def get_match_by_id(match_id):
    """Récupère un match par son ID."""
    with sqlite3.connect(DB_NAME) as con:
        con.row_factory = sqlite3.Row
        cur = con.cursor()
        cur.execute("SELECT * FROM matchs WHERE id = ?", (match_id,))
        return cur.fetchone()

def update_match_result(match_id, resultat, score):
    """Met à jour le résultat d'un match."""
    with sqlite3.connect(DB_NAME) as con:
        cur = con.cursor()
        cur.execute("""
            UPDATE matchs 
            SET resultat = ?, score = ?
            WHERE id = ?
        """, (resultat, score, match_id))
        con.commit()

def save_prono_message(match_id, message_id, channel_id):
    """Sauvegarde l'ID du message de pronostic."""
    with sqlite3.connect(DB_NAME) as con:
        cur = con.cursor()
        cur.execute("""
            INSERT OR REPLACE INTO prono_messages 
            (match_id, message_id, channel_id)
            VALUES (?, ?, ?)
        """, (match_id, message_id, channel_id))
        con.commit()

def get_prono_message(match_id):
    """Récupère les infos du message de pronostic."""
    with sqlite3.connect(DB_NAME) as con:
        con.row_factory = sqlite3.Row
        cur = con.cursor()
        cur.execute("SELECT * FROM prono_messages WHERE match_id = ?", (match_id,))
        return cur.fetchone()

def save_or_update_pronostic(user_id, match_id, pronostic):
    """Sauvegarde ou met à jour un pronostic."""
    check_user(user_id)
    with sqlite3.connect(DB_NAME) as con:
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
    with sqlite3.connect(DB_NAME) as con:
        con.row_factory = sqlite3.Row
        cur = con.cursor()
        cur.execute("""
            SELECT * FROM pronostics 
            WHERE user_id = ? AND match_id = ?
        """, (user_id, match_id))
        return cur.fetchone()

def get_match_pronostics(match_id):
    """Récupère tous les pronostics pour un match."""
    with sqlite3.connect(DB_NAME) as con:
        cur = con.cursor()
        cur.execute("""
            SELECT user_id, pronostic FROM pronostics 
            WHERE match_id = ?
        """, (match_id,))
        return cur.fetchall()

def attribute_points_for_match(match_id, resultat, points_par_bon_prono=50):
    """Attribue les points aux bons pronostiqueurs."""
    with sqlite3.connect(DB_NAME) as con:
        cur = con.cursor()
        # Récupérer les bons pronostics
        cur.execute("""
            UPDATE pronostics 
            SET points_gagnes = ?
            WHERE match_id = ? AND pronostic = ?
        """, (points_par_bon_prono, match_id, resultat))
        
        # Ajouter les points aux utilisateurs
        cur.execute("""
            UPDATE users 
            SET points = points + ?
            WHERE user_id IN (
                SELECT user_id FROM pronostics 
                WHERE match_id = ? AND pronostic = ?
            )
        """, (points_par_bon_prono, match_id, resultat))
        
        con.commit()

def get_journee_leaderboard(journee_id):
    """Récupère le classement des pronostiqueurs pour une journée."""
    with sqlite3.connect(DB_NAME) as con:
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
    with sqlite3.connect(DB_NAME) as con:
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
    with sqlite3.connect(DB_NAME) as con:
        cur = con.cursor()
        cur.execute("UPDATE matchs SET pronos_fermes = 1 WHERE id = ?", (match_id,))
        con.commit()

def mark_journee_rappel_sent(journee_id):
    """Marque qu'un rappel a été envoyé pour une journée."""
    with sqlite3.connect(DB_NAME) as con:
        cur = con.cursor()
        cur.execute("UPDATE journees SET rappel_envoye = 1 WHERE id = ?", (journee_id,))
        con.commit()

def get_journees_for_rappel():
    """Récupère les journées nécessitant un rappel (24h avant)."""
    with sqlite3.connect(DB_NAME) as con:
        con.row_factory = sqlite3.Row
        cur = con.cursor()
        tomorrow = datetime.datetime.now() + datetime.timedelta(days=1)
        cur.execute("""
            SELECT * FROM journees 
            WHERE rappel_envoye = 0 
            AND date_debut <= ? 
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
    with sqlite3.connect(DB_NAME) as con:
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
    with sqlite3.connect(DB_NAME) as con:
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
    with sqlite3.connect(DB_NAME) as con:
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
