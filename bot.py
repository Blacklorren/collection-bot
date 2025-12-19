import os
import discord
from discord.ext import commands
from dotenv import load_dotenv
import database
import pytz
import asyncio
from datetime import datetime, timedelta

# Le dossier où les données persistantes sont stockées
DATA_DIR = '/data'
# On définit le chemin complet pour le fichier de verrouillage
LOCK_FILE = os.path.join(DATA_DIR, 'reset_done.lock')

# 1. Chargement des variables d'environnements
load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')
GUILD_ID = os.getenv('GUILD_ID')

# Vérification que le token est bien présent
if TOKEN is None:
    print("Erreur : Le token Discord n'a pas été trouvé.")
    print("Assurez-vous d'avoir un fichier .env contenant DISCORD_TOKEN=VOTRE_TOKEN")
    exit()

if GUILD_ID is None:
    print("Erreur : L'ID du serveur (GUILD_ID) n'a pas été trouvé.")
    print("Assurez-vous d'avoir un fichier .env contenant GUILD_ID=VOTRE_ID")
    exit()

# --- MODIFICATION : Forcer la remises à zéro au démarrage ---
# Cette logique s'exécute AVANT l'initialisation du bot.
if not os.path.exists(LOCK_FILE):
    print(f"ℹ️  (RESET) Le fichier '{LOCK_FILE}' est absent.")
    print("🏁  (RESET) Lancement de la remise à zéro unique de la base de données...")
    database.initialize_database()  # S'assurer que la DB et les tables existent
    
    success = database.wipe_all_user_data()   # Vider les données et vérifier le succès
    
    if success:
        # Créer le fichier "lock" UNIQUEMENT si la remise à zéro a réussi
        with open(LOCK_FILE, 'w') as f:
            f.write(f"Reset performed on {datetime.now().isoformat()}")
        print("✅  (RESET) Remise à zéro réussie. Le bot va maintenant démarrer normalement.")
    else:
        print(f"ℹ️  (RESET) La remise à zéro unique a déjà été effectuée (lock trouvé à '{LOCK_FILE}'). Démarrage normal.")
else:
    print("ℹ️  (RESET) La remise à zéro unique a déjà été effectuée. Démarrage normal.")


# 2. Initialisation de la base de données
try:
    # On laisse cette initialisation ici, elle est sans danger ("IF NOT EXISTS")
    # et nécessaire si le bot démarre après un reset déjà effectué.
    database.initialize_database()
    print("✅ Base de données initialisée avec succès")
except Exception as e:
    print(f"Erreur lors de l'initialisation de la base de données : {e}")
    exit()


# 3. Définition des "Intents" (les autorisations du bot)
intents = discord.Intents.default()
intents.guilds = True
intents.members = True
intents.messages = True
intents.message_content = True
intents.reactions = True  # Important pour les pronostics

# 4. Création de la classe du bot fusionné
class HandnewsBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix='!', intents=intents)
        self.guild_id = int(GUILD_ID)

    async def setup_hook(self):
        """Charge tous les cogs au démarrage."""
        # Charger les cogs existants
        await self.load_extension('cogs.collection_cog')
        print("✅ Cog Collection chargé")
        
        await self.load_extension('cogs.events_cog')
        print("✅ Cog Events (RSS + Matchs) chargé")
        
        await self.load_extension('cogs.pronostics_cog')
        print("✅ Cog Pronostics chargé")

        await self.load_extension('cogs.test_cog')
        print("✅ Cog Test chargé")
        
      
        print("🎮 Tous les systèmes sont opérationnels !")

    async def on_ready(self):
        """Événement appelé lorsque le bot est connecté et entièrement prêt."""
        if self.user:
            print(f'🤖 Connecté en tant que {self.user.name} (ID: {self.user.id})')
        else:
            print("Le bot n'a pas pu récupérer les informations de l'utilisateur.")
        
        # Vérifier que le serveur est accessible
        guild = self.get_guild(self.guild_id)
        if guild:
            print(f'🏠 Serveur trouvé : {guild.name} (ID: {guild.id})')
            print(f'👥 Membres : {guild.member_count}')
        else:
            print(f'❌ ERREUR : Serveur {self.guild_id} introuvable!')
            
        print('✨ Le bot est prêt à recevoir des commandes !')
        print('------')

    async def on_command_error(self, ctx, error):
        """Gère les erreurs de commandes."""
        if isinstance(error, commands.CommandNotFound):
            # Ignorer silencieusement les commandes inconnues
            return
        elif isinstance(error, commands.MissingPermissions):
            await ctx.send("❌ Vous n'avez pas les permissions nécessaires pour exécuter cette commande.", ephemeral=True)
        elif isinstance(error, commands.MissingRequiredArgument):
            await ctx.send(f"❌ Argument manquant : `{error.param.name}`", ephemeral=True)
        elif isinstance(error, commands.BadArgument):
            await ctx.send("❌ Argument invalide. Vérifiez la syntaxe de la commande.", ephemeral=True)
        else:
            print(f"Une erreur est survenue lors de l'exécution de la commande '{ctx.command}': {error}")
            try:
                await ctx.send("Oups ! Une erreur est survenue lors de l'exécution de cette commande.", ephemeral=True)
            except discord.errors.NotFound:
                pass

    async def on_message(self, message):
        """Gère les messages pour le système de points ET les commandes."""
        # Ignorer les messages du bot
        if message.author.bot:
            return
            
        # Traiter d'abord les commandes spéciales (!testevent)
        if message.content.lower() == '!testevent':
            if not message.author.guild_permissions.administrator:
                try:
                    await message.channel.send("❌ Vous n'avez pas la permission d'utiliser cette commande.", delete_after=10)
                except discord.Forbidden:
                    pass
                return

            guild = message.guild
            if not guild:
                return

            try:
                await message.channel.send("✅ Reçu ! Création d'un événement de test dans 30 secondes (il sera supprimé dans 60 secondes)...", delete_after=15)

                now_utc = datetime.now(pytz.timezone('UTC'))
                start_time = now_utc + timedelta(seconds=30)
                end_time = start_time + timedelta(hours=2)

                # Créer l'événement
                test_event = await guild.create_scheduled_event(
                    name="Événement de Test (Suppression auto)",
                    description="Ceci est un événement de test pour vérifier le rendu visuel et les notifications de rappel.",
                    start_time=start_time,
                    end_time=end_time,
                    entity_type=discord.EntityType.external,
                    location="Stade Virtuel de Handnews",
                    privacy_level=discord.PrivacyLevel.guild_only
                )
                print(f"✨ (TEST) Événement de test créé : {test_event.id}")

                await asyncio.sleep(60)

                print(f"🗑️ (TEST) Suppression de l'événement de test : {test_event.id}")
                await test_event.delete()
                await message.channel.send("🧹 Événement de test supprimé.", delete_after=10)

            except discord.Forbidden:
                await message.channel.send("❌ Erreur : Le bot n'a pas la permission 'Gérer les événements' pour effectuer ce test.")
            except Exception as e:
                print(f"❌ (TEST) Une erreur est survenue lors du test : {e}")
                await message.channel.send(f"❌ Une erreur est survenue : {e}")
            return
            
        # Traiter les autres commandes normalement
        await self.process_commands(message)
        
        # Le système de points est géré dans collection_cog via l'event on_message
    
    

# 5. Commandes globales (optionnel - pour les admins)
async def setup_global_commands(bot):
    """Ajoute des commandes globales au bot."""

    @bot.command(name='sync')
    @commands.has_permissions(administrator=True)
    async def sync(ctx: commands.Context):
        """Synchronise les commandes slash avec le serveur."""
        guild = discord.Object(id=bot.guild_id)
        bot.tree.copy_global_to(guild=guild)
        try:
            synced = await bot.tree.sync(guild=guild)
            await ctx.send(f"✅ Synchronisé **{len(synced)}** commande(s) slash avec le serveur.")
            print(f"Synced {len(synced)} commands.")
        except Exception as e:
            await ctx.send(f"❌ Une erreur est survenue lors de la synchronisation : {e}")
            print(e)
            
    @bot.command(name='status')
    @commands.has_permissions(administrator=True)
    async def status_command(ctx):
        """Affiche le statut de tous les systèmes."""
        embed = discord.Embed(
            title="📊 Statut du Bot Handnews",
            color=discord.Color.green(),
            timestamp=datetime.now(pytz.timezone('UTC'))
        )
        
        # Statut des cogs
        cog_status = ""
        for cog_name in ['CollectionCog', 'EventsCog', 'PronosticsCog']:
            cog = bot.get_cog(cog_name)
            status = "✅ Actif" if cog else "❌ Inactif"
            cog_status += f"**{cog_name}** : {status}\n"
            
        embed.add_field(name="🎮 Modules", value=cog_status, inline=False)
        
        # Statistiques de la base de données
        try:
            with database.sqlite3.connect(database.DB_NAME) as con:
                cur = con.cursor()
                cur.execute("SELECT COUNT(*) FROM users")
                user_count = cur.fetchone()[0]
                
                cur.execute("SELECT COUNT(*) FROM matchs WHERE resultat IS NULL")
                matches_pending = cur.fetchone()[0]
                
                cur.execute("SELECT COUNT(*) FROM pronostics")
                total_pronos = cur.fetchone()[0]
                
            stats = (
                f"**Utilisateurs** : {user_count}\n"
                f"**Matchs en attente** : {matches_pending}\n"
                f"**Pronostics totaux** : {total_pronos}"
            )
            embed.add_field(name="📈 Statistiques", value=stats, inline=False)
        except Exception as e:
            embed.add_field(name="📈 Statistiques", value="Erreur de lecture", inline=False)
            
        # Informations système
        guild = ctx.guild
        system_info = (
            f"**Serveur** : {guild.name}\n"
            f"**Membres** : {guild.member_count}\n"
            f"**Ping** : {round(bot.latency * 1000)}ms"
        )
        embed.add_field(name="⚙️ Système", value=system_info, inline=False)
        
        await ctx.send(embed=embed, ephemeral=True)
    
    @bot.command(name='reload')
    @commands.has_permissions(administrator=True)
    async def reload_command(ctx, cog_name: str = None):
        """Recharge un cog spécifique ou tous les cogs."""
        if cog_name:
            try:
                await bot.reload_extension(f'cogs.{cog_name}_cog')
                await ctx.send(f"✅ Le module `{cog_name}_cog` a été rechargé.", ephemeral=True)
            except Exception as e:
                await ctx.send(f"❌ Erreur lors du rechargement : {e}", ephemeral=True)
        else:
            # Recharger tous les cogs
            errors = []
            for cog in ['collection', 'events', 'pronostics']:
                try:
                    await bot.reload_extension(f'cogs.{cog}_cog')
                except Exception as e:
                    errors.append(f"{cog}: {e}")
                    
            if errors:
                await ctx.send(f"⚠️ Rechargement partiel. Erreurs :\n" + "\n".join(errors), ephemeral=True)
            else:
                await ctx.send("✅ Tous les modules ont été rechargés.", ephemeral=True)

# 6. Lancement du bot
async def main():
    """Fonction principale pour lancer le bot."""
    bot = HandnewsBot()
    
    # Ajouter les commandes globales
    await setup_global_commands(bot)
    
    try:
        print("⏳ Attente de 5 secondes avant connexion API...")
        await asyncio.sleep(5) 
        await bot.start(TOKEN)
    except discord.errors.LoginFailure:
        print("❌ Erreur de connexion : Le token fourni est invalide.")
    except discord.errors.HTTPException as e:
        if e.status == 429:
            print("❌ ERREUR 429 (RATE LIMIT): L'IP est bannie temporairement. Arrêt forcé pour 1h.")
            # On force un long sommeil pour empêcher le conteneur de redémarrer immédiatement
            await asyncio.sleep(3600) 
        else:
            print(f"❌ Erreur HTTP : {e}")
    except Exception as e:
        print(f"❌ Une erreur est survenue lors du lancement du bot : {e}")
    finally:
        await bot.close()

# Point d'entrée
if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n👋 Arrêt du bot...")
