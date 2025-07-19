import os
import discord
from discord.ext import commands
from dotenv import load_dotenv
import database

# 1. Chargement des variables d'environnement (pour le token)
load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')

# Vérification que le token est bien présent
if TOKEN is None:
    print("Erreur : Le token Discord n'a pas été trouvé.")
    print("Assurez-vous d'avoir un fichier .env contenant DISCORD_TOKEN=VOTRE_TOKEN")
    exit()

# 2. Initialisation de la base de données
# Cette fonction va créer le fichier collection.db et les tables si elles n'existent pas.
try:
    database.initialize_database()
except Exception as e:
    print(f"Erreur lors de l'initialisation de la base de données : {e}")
    exit()

# 3. Définition des "Intents" (les autorisations du bot)
# Nous avons besoin de `members` pour l'événement on_member_join (arrivée d'un membre)
# et `message_content` pour pouvoir lire les messages et attribuer des points.
intents = discord.Intents.default()
intents.guilds = True
intents.members = True
intents.messages = True
intents.message_content = True

# 4. Création de l'instance du bot avec le préfixe '!' et les intents définis
bot = commands.Bot(command_prefix='!', intents=intents)

@bot.event
async def on_ready():
    """Événement appelé lorsque le bot est connecté et entièrement prêt."""
    if bot.user:
        print(f'Connecté en tant que {bot.user.name} (ID: {bot.user.id})')
    else:
        print("Le bot n'a pas pu récupérer les informations de l'utilisateur.")
    print('Le bot est prêt à recevoir des commandes !')
    print('------')

    # Chargement du Cog qui contient toutes nos commandes
    try:
        await bot.load_extension('cogs.collection_cog')
        print("Le Cog 'collection_cog' a été chargé avec succès.")
    except Exception as e:
        print(f"Erreur lors du chargement du Cog : {e}")

@bot.event
async def on_command_error(ctx, error):
    """Gère les erreurs de commandes."""
    if isinstance(error, commands.CommandNotFound):
        # La commande n'existe pas, on en informe l'utilisateur
        await ctx.send("Commande inconnue. Tapez `!aide` pour voir la liste des commandes disponibles.", ephemeral=True)
    else:
        # Pour toutes les autres erreurs, on les affiche dans la console pour le débogage
        print(f"Une erreur est survenue lors de l'exécution de la commande '{ctx.command}': {error}")
        # On peut aussi informer l'utilisateur qu'une erreur s'est produite
        await ctx.send("Oups ! Une erreur est survenue lors de l'exécution de cette commande.", ephemeral=True)


# 5. Lancement du bot avec le token
try:
    bot.run(TOKEN)
except discord.errors.LoginFailure:
    print("Erreur de connexion : Le token fourni est invalide.")
except Exception as e:
    print(f"Une erreur est survenue lors du lancement du bot : {e}")