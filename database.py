import sqlite3
import datetime

DB_NAME = '/data/collection.db' # Assurez-vous que c'est le bon chemin pour Railway

def initialize_database():
    """Crée et met à jour les tables de la base de données."""
    with sqlite3.connect(DB_NAME) as con:
        cur = con.cursor()
        
        cur.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                points INTEGER NOT NULL DEFAULT 0,
                packs INTEGER NOT NULL DEFAULT 0,
                last_activity_date TEXT,
                last_message_time TEXT,
                daily_message_points INTEGER NOT NULL DEFAULT 0,
                fragments INTEGER NOT NULL DEFAULT 0
            )
        ''')

        # --- AJOUT SÉCURISÉ DES NOUVELLES COLONNES ---
        try:
            cur.execute("ALTER TABLE users ADD COLUMN daily_message_points INTEGER NOT NULL DEFAULT 0")
        except sqlite3.OperationalError: pass
        try:
            cur.execute("ALTER TABLE users ADD COLUMN fragments INTEGER NOT NULL DEFAULT 0")
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

        con.commit()

def check_user(user_id):
    """Vérifie si un utilisateur existe dans la DB, sinon le crée avec des valeurs initiales."""
    with sqlite3.connect(DB_NAME) as con:
        cur = con.cursor()
        cur.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,))
        if cur.fetchone() is None:
            cur.execute("INSERT INTO users (user_id, points, packs) VALUES (?, 100, 1)", (user_id,))
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

def update_on_message_activity(user_id, points_to_add):
    check_user(user_id)
    with sqlite3.connect(DB_NAME) as con:
        cur = con.cursor()
        now_time = datetime.datetime.now().isoformat()
        today_date = datetime.date.today().isoformat()
        cur.execute("""
            UPDATE users SET points = points + ?, daily_message_points = daily_message_points + ?,
            last_activity_date = ?, last_message_time = ? WHERE user_id = ?
        """, (points_to_add, points_to_add, today_date, now_time, user_id))
        con.commit()
        
def reset_daily_and_add_first_bonus(user_id, bonus_points, message_points):
    check_user(user_id)
    with sqlite3.connect(DB_NAME) as con:
        cur = con.cursor()
        now_time = datetime.datetime.now().isoformat()
        today_date = datetime.date.today().isoformat()
        total_points_to_add = bonus_points + message_points
        cur.execute("""
            UPDATE users SET points = points + ?, daily_message_points = ?,
            last_activity_date = ?, last_message_time = ? WHERE user_id = ?
        """, (total_points_to_add, message_points, today_date, now_time, user_id))
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
