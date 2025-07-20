import os
import discord
from discord.ext import commands
from dotenv import load_dotenv
import database
import pytz
import asyncio
from datetime import datetime

# 1. Chargement des variables d'environnement (pour le token)
load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')

# Vérification que le token est bien présent
if TOKEN is None:
    print("Erreur : Le token Discord n'a pas été trouvé.")
    print("Assurez-vous d'avoir un fichier .env contenant DISCORD_TOKEN=VOTRE_TOKEN")
    exit()

# 2. Initialisation de la base de données
try:
    database.initialize_database()
except Exception as e:
    print(f"Erreur lors de l'initialisation de la base de données : {e}")
    exit()

# 3. Définition des "Intents" (les autorisations du bot)
intents = discord.Intents.default()
intents.guilds = True
intents.members = True
intents.messages = True
intents.message_content = True

# 4. Création de la classe du bot pour une meilleure organisation
class CollectionBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix='!', intents=intents)

    async def setup_hook(self):
        """Cette fonction est appelée une seule fois avant que le bot soit prêt."""
        # Chargement du Cog qui contient toutes nos commandes
        try:
            await self.load_extension('cogs.collection_cog')
            print("Le Cog 'collection_cog' a été chargé avec succès.")
        except Exception as e:
            print(f"Erreur lors du chargement du Cog : {e}")
        
        # Lancement de la tâche de fond pour la remise à zéro
        self.loop.create_task(self.reset_scheduler_loop())

    async def on_ready(self):
        """Événement appelé lorsque le bot est connecté et entièrement prêt."""
        if self.user:
            print(f'Connecté en tant que {self.user.name} (ID: {self.user.id})')
        else:
            print("Le bot n'a pas pu récupérer les informations de l'utilisateur.")
        print('Le bot est prêt à recevoir des commandes !')
        print('------')

    async def on_command_error(self, ctx, error):
        """Gère les erreurs de commandes."""
        if isinstance(error, commands.CommandNotFound):
            try:
                await ctx.send("Commande inconnue. Tapez `!aide` pour voir la liste des commandes disponibles.", ephemeral=True)
            except discord.errors.NotFound: # Si la commande a été supprimée entre temps
                pass
        elif isinstance(error, commands.MissingPermissions):
            await ctx.send("❌ Vous n'avez pas les permissions nécessaires pour exécuter cette commande.", ephemeral=True)
        else:
            print(f"Une erreur est survenue lors de l'exécution de la commande '{ctx.command}': {error}")
            try:
                await ctx.send("Oups ! Une erreur est survenue lors de l'exécution de cette commande.", ephemeral=True)
            except discord.errors.NotFound:
                pass
    
    # --- NOUVELLE FONCTION DE REMISE À ZÉRO ---
    async def reset_scheduler_loop(self):
        """Tâche unique qui vérifie s'il faut remettre le jeu à zéro."""
        await self.wait_until_ready()
        
        # On utilise un fichier "lock" pour s'assurer que le reset n'arrive qu'une seule fois
        if os.path.exists('reset_done.lock'):
            print("ℹ️ (RESET) La remise à zéro a déjà été effectuée. La tâche ne démarrera pas.")
            return

        print("⏰ (RESET) Tâche de remise à zéro programmée pour le 14/08/2025.")
        
        while not self.is_closed():
            now_paris = datetime.now(pytz.timezone('Europe/Paris'))
            target_reset_date = datetime(2025, 8, 14, 0, 0, 0, tzinfo=pytz.timezone('Europe/Paris'))

            # Si on est le 14 août 2025 ou après, et que le reset n'a pas été fait
            if now_paris >= target_reset_date:
                print("🏁 (RESET) Date de remise à zéro atteinte. Lancement de la procédure.")
                
                # Appeler la fonction de suppression de la base de données
                database.wipe_all_user_data()
                
                # Créer le fichier "lock" pour ne plus jamais refaire le reset
                with open('reset_done.lock', 'w') as f:
                    f.write(f"Reset performed on {now_paris.isoformat()}")
                
                print("✅ (RESET) Remise à zéro terminée. La tâche va s'arrêter.")
                break # Arrête la boucle, sa mission est accomplie.

            # Vérifier toutes les heures jusqu'à la date fatidique
            await asyncio.sleep(3600)

# 5. Lancement du bot
try:
    bot = CollectionBot()
    bot.run(TOKEN)
except discord.errors.LoginFailure:
    print("Erreur de connexion : Le token fourni est invalide.")
except Exception as e:
    print(f"Une erreur est survenue lors du lancement du bot : {e}")
