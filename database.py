import sqlite3
import datetime

DB_NAME = 'collection.db'

def initialize_database():
    """Crée les tables de la base de données si elles n'existent pas."""
    with sqlite3.connect(DB_NAME) as con:
        cur = con.cursor()

        cur.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                points INTEGER NOT NULL DEFAULT 100,
                packs INTEGER NOT NULL DEFAULT 0,
                last_daily TEXT,
                last_message_time TEXT
            )
        ''')

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
    """Vérifie si un utilisateur existe dans la DB, sinon le crée."""
    with sqlite3.connect(DB_NAME) as con:
        cur = con.cursor()
        cur.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,))
        if cur.fetchone() is None:
            # Donne 1 pack de démarrage au nouveau joueur
            cur.execute("INSERT INTO users (user_id, packs) VALUES (?, 1)", (user_id,))
            con.commit()

def get_user_data(user_id):
    """Récupère toutes les données d'un utilisateur."""
    check_user(user_id)
    with sqlite3.connect(DB_NAME) as con:
        cur = con.cursor()
        cur.execute("SELECT points, packs, last_daily, last_message_time FROM users WHERE user_id = ?", (user_id,))
        return cur.fetchone()

def update_points(user_id, amount):
    """Ajoute ou retire des points à un utilisateur."""
    check_user(user_id)
    with sqlite3.connect(DB_NAME) as con:
        cur = con.cursor()
        cur.execute("UPDATE users SET points = points + ? WHERE user_id = ?", (amount, user_id))
        con.commit()

def update_last_message_time(user_id):
    """Met à jour l'heure du dernier message d'un utilisateur."""
    check_user(user_id)
    with sqlite3.connect(DB_NAME) as con:
        cur = con.cursor()
        now = datetime.datetime.now().isoformat()
        cur.execute("UPDATE users SET last_message_time = ? WHERE user_id = ?", (now, user_id))
        con.commit()

def add_pack(user_id, amount=1):
    """Ajoute un ou plusieurs packs à un utilisateur."""
    check_user(user_id)
    with sqlite3.connect(DB_NAME) as con:
        cur = con.cursor()
        cur.execute("UPDATE users SET packs = packs + ? WHERE user_id = ?", (amount, user_id))
        con.commit()

def remove_pack(user_id, amount=1):
    """Retire un ou plusieurs packs à un utilisateur."""
    check_user(user_id)
    with sqlite3.connect(DB_NAME) as con:
        cur = con.cursor()
        cur.execute("UPDATE users SET packs = packs - ? WHERE user_id = ?", (amount, user_id))
        con.commit()

def add_card_to_collection(user_id, card_id):
    """Ajoute une carte à la collection d'un utilisateur."""
    check_user(user_id)
    with sqlite3.connect(DB_NAME) as con:
        cur = con.cursor()
        cur.execute("INSERT INTO user_cards (user_id, card_id) VALUES (?, ?)", (user_id, card_id))
        con.commit()

def get_user_collection(user_id):
    """Récupère toutes les card_id de la collection d'un utilisateur."""
    check_user(user_id)
    with sqlite3.connect(DB_NAME) as con:
        cur = con.cursor()
        cur.execute("SELECT card_id FROM user_cards WHERE user_id = ?", (user_id,))
        # fetchall() retourne une liste de tuples, ex: [(1,), (23,)]
        # nous la transformons en une simple liste d'IDs, ex: [1, 23]
        return [item[0] for item in cur.fetchall()]

def set_daily_claimed(user_id):
    """Met à jour la date de la dernière réclamation du bonus quotidien."""
    check_user(user_id)
    with sqlite3.connect(DB_NAME) as con:
        cur = con.cursor()
        today = datetime.date.today().isoformat()
        cur.execute("UPDATE users SET last_daily = ? WHERE user_id = ?", (today, user_id))
        con.commit()