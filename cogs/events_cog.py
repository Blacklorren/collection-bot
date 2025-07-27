import discord
from discord.ext import commands, tasks
import feedparser
import asyncio
import os
from datetime import datetime, timezone, timedelta
import re
import requests
import pytz
import database

# Configuration
RSS_URL = "https://handnews.fr/feed"
CHANNEL_ID = int(os.getenv('CHANNEL_ID')) if os.getenv('CHANNEL_ID') else None
CHECK_INTERVAL = 1800  # 30 minutes pour RSS

# Configuration Livescore
LIVESCORE_API_URL = "https://prod-public-api.livescore.com/v1/api/app/calendar/neCDnec2/3?locale=fr"
MATCH_CHECK_INTERVAL = 3600  # Vérifier les matchs toutes les heures
RESULTS_CHECK_INTERVAL = 1800  # Vérifier les résultats toutes les 30 minutes

class EventsCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.seen_articles = set()
        self.first_check = True
        self.created_matches = {}
        
        # Démarrer les tâches
        self.check_rss_loop.start()
        self.check_matches_loop.start()
        self.check_results_loop.start()
        
    def cog_unload(self):
        """Arrête les tâches lors du déchargement du cog."""
        self.check_rss_loop.cancel()
        self.check_matches_loop.cancel()
        self.check_results_loop.cancel()

    # === GESTION RSS ===
    @tasks.loop(seconds=CHECK_INTERVAL)
    async def check_rss_loop(self):
        """Vérifie le flux RSS pour de nouveaux articles."""
        if CHANNEL_ID is None: 
            return
            
        channel = self.bot.get_channel(CHANNEL_ID)
        if not channel:
            print(f"❌ (RSS) Canal {CHANNEL_ID} introuvable!")
            return
            
        try:
            print("🔍 (RSS) Vérification du flux RSS...")
            feed = feedparser.parse(RSS_URL)
            
            if not feed.bozo:
                new_articles = 0
                for entry in reversed(feed.entries):
                    if entry.link not in self.seen_articles:
                        if not self.first_check:
                            await self.send_article_to_discord(channel, entry)
                            new_articles += 1
                        self.seen_articles.add(entry.link)
                        
                if self.first_check: 
                    self.first_check = False
                    print(f"ℹ️ (RSS) Premier check, {len(self.seen_articles)} articles ignorés")
                elif new_articles > 0: 
                    print(f"✅ (RSS) {new_articles} nouveaux articles envoyés")
                else: 
                    print("ℹ️ (RSS) Aucun nouvel article")
            else: 
                print("⚠️ (RSS) Flux RSS invalide")
                
        except Exception as e:
            print(f"❌ (RSS) Erreur lors de la vérification : {e}")

    async def send_article_to_discord(self, channel, entry):
        """Envoie un article vers Discord."""
        try:
            # Créer l'embed
            embed = discord.Embed(
                title=entry.title[:256],
                url=entry.link,
                color=0xe8874f,
                timestamp=datetime.now(timezone.utc)
            )

            # Description (résumé de l'article)
            if hasattr(entry, 'summary'):
                description = entry.summary
                description = re.sub('<[^<]+?>', '', description)
                if len(description) > 2048:
                    description = description[:2045] + "..."
                embed.description = description

            # Recherche de l'image
            image_url = None
            if 'media_content' in entry and entry.media_content:
                image_url = entry.media_content[0]['url']
            elif 'links' in entry:
                for link in entry.links:
                    if link.get('rel') == 'enclosure' and link.get('type', '').startswith('image/'):
                        image_url = link.href
                        break
            
            if not image_url and hasattr(entry, 'content'):
                content = entry.content[0].value
                match = re.search(r'<img[^>]+src="([^">]+)"', content)
                if match:
                    image_url = match.group(1)

            if image_url:
                embed.set_image(url=image_url)
          
            # Auteur
            embed.set_author(
                name="📰 Handnews.fr",
                icon_url="https://handnews.fr/favicon.ico",
                url="https://handnews.fr"
            )

            # Footer avec date
            if hasattr(entry, 'published'):
                try:
                    pub_date = datetime.strptime(entry.published, "%a, %d %b %Y %H:%M:%S %z")
                    embed.set_footer(text=f"Publié le {pub_date.strftime('%d/%m/%Y à %H:%M')}")
                except ValueError:
                    embed.set_footer(text=f"Publié le • {entry.published}")
            else:
                embed.set_footer(text="Handnews.fr")

            # Envoyer le message
            await channel.send(embed=embed)
            print(f"📤 (RSS) Article envoyé : {entry.title[:50]}...")

        except discord.HTTPException as e:
            print(f"❌ (RSS) Erreur Discord : {e}")
        except Exception as e:
            print(f"❌ (RSS) Erreur générale : {e}")

    # === GESTION DES MATCHS LIVESCORE ===
    def scrape_livescore_matches(self):
        """Récupère les matchs à venir via l'API Livescore."""
        try:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                'Accept': 'application/json'
            }
            response = requests.get(LIVESCORE_API_URL, headers=headers, timeout=15)
            response.raise_for_status()
            data = response.json()
            
            matches = []
            now_utc = datetime.now(timezone.utc)
            limit_date = now_utc + timedelta(days=5)
            
            for stage in data.get('Stages', []):
                for event in stage.get('Events', []):
                    t1 = event.get('T1', [{}])
                    t2 = event.get('T2', [{}])
                    
                    if not t1 or not t2:
                        continue
                        
                    team1 = t1[0].get('Nm', 'Équipe inconnue')
                    team2 = t2[0].get('Nm', 'Équipe inconnue')
                    start_time_str = event.get('Esd', '')
                    
                    if not start_time_str:
                        continue
                    
                    # Convertir le temps UTC
                    if start_time_str.endswith('Z'):
                        start_time_str = start_time_str[:-1] + '+00:00'
                    
                    try:
                        match_time = datetime.fromisoformat(start_time_str).astimezone(timezone.utc)
                    except ValueError:
                        continue
                    
                    # Filtrer les matchs dans les 5 prochains jours
                    if now_utc < match_time < limit_date:
                        matches.append({
                            "team1": team1,
                            "team2": team2,
                            "start_time_utc": match_time,
                            "event_id": event.get('Eid', '')
                        })
            
            return matches
            
        except requests.RequestException as e:
            print(f"❌ (LIVESCORE) Erreur API: {e}")
            return []
        except Exception as e:
            print(f"❌ (LIVESCORE) Erreur inattendue: {e}")
            return []

    @tasks.loop(seconds=MATCH_CHECK_INTERVAL)
    async def check_matches_loop(self):
        """Vérifie et crée les événements Discord pour les nouveaux matchs."""
        guild = self.bot.get_guild(self.bot.guild_id)
        if not guild:
            print(f"❌ (MATCHES) Serveur {self.bot.guild_id} introuvable!")
            return
        
        print("🔍 (MATCHES) Vérification des matchs de Starligue...")
        
        try:
            # Récupérer les matchs depuis Livescore
            scraped_matches = await self.bot.loop.run_in_executor(None, self.scrape_livescore_matches)
            
            if not scraped_matches:
                print("ℹ️ (MATCHES) Aucun match trouvé dans l'API.")
                return
            
            print(f"🏐 (MATCHES) {len(scraped_matches)} matchs trouvés (J+5)")
            
            # Déterminer la journée
            journee_id, journee_numero = database.determine_journee_from_matches(scraped_matches)
            
            # Récupérer les événements Discord existants
            existing_events = guild.scheduled_events
            existing_event_names = {event.name: event for event in existing_events}
            
            # Importer le cog des pronostics pour créer les messages
            pronos_cog = self.bot.get_cog('PronosticsCog')
            new_matches_for_pronos = []
            
            for match in scraped_matches:
                event_name = f"{match['team1']} vs {match['team2']}"
                
                # Vérifier si le match existe déjà dans la base
                existing_match = database.get_match_by_event_id(match['event_id'])
                
                if not existing_match and event_name not in existing_event_names:
                    print(f"✨ (MATCHES) Création événement: {event_name}")
                    
                    # Créer l'événement Discord
                    event = await guild.create_scheduled_event(
                        name=event_name,
                        description=(
                            f"Match de Starligue - Journée {journee_numero}\n\n"
                            "📊 Faites vos pronostics dans le canal dédié !\n"
                            "Les pronostics fermeront 5 minutes avant le début du match."
                        ),
                        start_time=match['start_time_utc'],
                        end_time=match['start_time_utc'] + timedelta(hours=2),
                        entity_type=discord.EntityType.external,
                        location="Starligue Handball",
                        privacy_level=discord.PrivacyLevel.guild_only
                    )
                    
                    # Sauvegarder dans la base de données
                    match_id = database.create_match(
                        journee_id=journee_id,
                        event_id=match['event_id'],
                        discord_event_id=event.id,
                        equipe1=match['team1'],
                        equipe2=match['team2'],
                        date_match=match['start_time_utc']
                    )
                    
                    # Préparer pour la création du message de pronostic
                    new_matches_for_pronos.append({
                        'id': match_id,
                        'equipe1': match['team1'],
                        'equipe2': match['team2'],
                        'date_match': match['start_time_utc'],
                        'journee_numero': journee_numero
                    })
                    
                    # Stocker pour le suivi
                    self.created_matches[event.id] = {
                        "match_id": match_id,
                        "event_id": match['event_id'],
                        "team1": match['team1'],
                        "team2": match['team2'],
                        "start_time": match['start_time_utc']
                    }
            
            # Créer les messages de pronostics pour les nouveaux matchs
            if new_matches_for_pronos and pronos_cog:
                await pronos_cog.create_pronostic_messages_for_matches(new_matches_for_pronos)
                print(f"✅ (MATCHES) {len(new_matches_for_pronos)} messages de pronostics créés")
            
            # Archivage des anciens événements
            now_utc = datetime.now(timezone.utc)
            for event in existing_events:
                if (event.creator == self.bot.user and 
                    event.status in (discord.EventStatus.scheduled, discord.EventStatus.active) and 
                    now_utc > (event.start_time + timedelta(hours=12))):
                    
                    print(f"🗄️ (MATCHES) Archivage événement: {event.name}")
                    await event.edit(status=discord.EventStatus.completed)
                    
        except Exception as e:
            print(f"❌ (MATCHES) Erreur dans la boucle: {e}")

    @tasks.loop(seconds=RESULTS_CHECK_INTERVAL)
    async def check_results_loop(self):
        """Vérifie et met à jour les résultats des matchs terminés."""
        print("🔍 (RESULTS) Vérification des résultats...")
        
        # Récupérer les matchs sans résultat dont l'heure de début + 3h est passée
        now_utc = datetime.now(timezone.utc)
        three_hours_ago = now_utc - timedelta(hours=3)
        
        with database.sqlite3.connect(database.DB_NAME) as con:
            con.row_factory = database.sqlite3.Row
            cur = con.cursor()
            cur.execute("""
                SELECT * FROM matchs 
                WHERE resultat IS NULL 
                AND date_match <= ?
            """, (three_hours_ago,))
            matches_to_check = cur.fetchall()
        
        pronos_cog = self.bot.get_cog('PronosticsCog')
        
        for match in matches_to_check:
            try:
                # Récupérer le résultat depuis Livescore
                result = self.get_match_result(match['event_id'])
                
                if result:
                    print(f"✅ (RESULTS) Résultat trouvé: {match['equipe1']} {result} {match['equipe2']}")
                    
                    # Traiter le résultat via le cog des pronostics
                    if pronos_cog:
                        pronos_cog.process_match_result(match['id'], result)
                    
                    # Mettre à jour l'événement Discord avec le score
                    await self.update_discord_event_with_result(
                        match['discord_event_id'], 
                        match['equipe1'], 
                        match['equipe2'], 
                        result
                    )
                else:
                    print(f"⚠️ (RESULTS) Pas encore de résultat pour {match['equipe1']} vs {match['equipe2']}")
                    
            except Exception as e:
                print(f"❌ (RESULTS) Erreur pour le match {match['id']}: {e}")

    def get_match_result(self, event_id):
        """Récupère le résultat d'un match spécifique depuis Livescore."""
        try:
            url = f"https://prod-public-api.livescore.com/v1/api/app/event/{event_id}/3?locale=fr"
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                'Accept': 'application/json'
            }
            response = requests.get(url, headers=headers, timeout=10)
            response.raise_for_status()
            data = response.json()
            
            # Extraction du score
            tr1 = data.get('Tr1', '?')
            tr2 = data.get('Tr2', '?')
            
            if tr1 is not None and tr2 is not None and tr1 != '?' and tr2 != '?':
                return f"{tr1}-{tr2}"
            return None
            
        except Exception as e:
            print(f"❌ (RESULTS) Erreur API: {e}")
            return None

    async def update_discord_event_with_result(self, discord_event_id, team1, team2, score):
        """Met à jour l'événement Discord avec le résultat du match."""
        try:
            guild = self.bot.get_guild(self.bot.guild_id)
            if not guild:
                return
                
            # Récupérer l'événement
            event = guild.get_scheduled_event(discord_event_id)
            if not event:
                print(f"⚠️ (RESULTS) Événement Discord {discord_event_id} introuvable")
                return
            
            # Mettre à jour la description avec le score
            new_description = (
                f"Match de Starligue\n\n"
                f"🏐 **RÉSULTAT FINAL : {score}**\n\n"
                f"{event.description.split('📊')[1] if '📊' in event.description else ''}"
            )
            
            # Mettre à jour le nom si l'événement est toujours modifiable
            if event.status == discord.EventStatus.scheduled:
                await event.edit(
                    name=f"[TERMINÉ] {team1} {score} {team2}",
                    description=new_description
                )
            else:
                # Si l'événement a déjà commencé, on peut seulement mettre à jour la description
                await event.edit(description=new_description)
                
            print(f"✅ (RESULTS) Événement mis à jour avec le score: {score}")
            
        except Exception as e:
            print(f"❌ (RESULTS) Erreur mise à jour événement: {e}")

    @check_rss_loop.before_loop
    @check_matches_loop.before_loop
    @check_results_loop.before_loop
    async def before_loops(self):
        """Attend que le bot soit prêt avant de démarrer les boucles."""
        await self.bot.wait_until_ready()

    # === COMMANDES ===
    @commands.command(name='handball')
    async def handball_command(self, ctx):
        """Affiche les prochains matchs de handball."""
        guild = ctx.guild
        if not guild:
            return
            
        # Récupérer les événements programmés
        upcoming_events = []
        now_utc = datetime.now(timezone.utc)
        
        for event in guild.scheduled_events:
            if (event.creator == self.bot.user and 
                event.status == discord.EventStatus.scheduled and
                "vs" in event.name):
                upcoming_events.append(event)
        
        if not upcoming_events:
            await ctx.send("Aucun match programmé pour le moment.", ephemeral=True)
            return
        
        # Trier par date
        upcoming_events.sort(key=lambda e: e.start_time)
        
        # Créer l'embed
        embed = discord.Embed(
            title="🏐 Prochains matchs de Starligue",
            description=f"**{len(upcoming_events)} matchs** à venir",
            color=discord.Color.orange(),
            timestamp=now_utc
        )
        
        # Afficher les 10 prochains matchs
        for event in upcoming_events[:10]:
            time_paris = event.start_time.astimezone(pytz.timezone('Europe/Paris'))
            time_until = event.start_time - now_utc
            
            if time_until.days > 0:
                time_str = f"Dans {time_until.days} jour{'s' if time_until.days > 1 else ''}"
            elif time_until.total_seconds() > 3600:
                hours = int(time_until.total_seconds() // 3600)
                time_str = f"Dans {hours} heure{'s' if hours > 1 else ''}"
            else:
                minutes = int(time_until.total_seconds() // 60)
                time_str = f"Dans {minutes} minute{'s' if minutes > 1 else ''}"
            
            embed.add_field(
                name=event.name,
                value=f"📅 {time_paris.strftime('%d/%m à %H:%M')}\n⏰ {time_str}",
                inline=True
            )
        
        embed.set_footer(text="Cliquez sur 'Intéressé' sur un événement pour recevoir un rappel!")
        
        await ctx.send(embed=embed, ephemeral=True)

    @commands.command(name='forcesync')
    @commands.has_permissions(administrator=True)
    async def force_sync_command(self, ctx):
        """Force la synchronisation des matchs (admin uniquement)."""
        await ctx.send("🔄 Synchronisation forcée des matchs en cours...", ephemeral=True)
        
        # Forcer l'exécution de la vérification des matchs
        try:
            await self.check_matches_loop()
            await ctx.send("✅ Synchronisation des matchs terminée!", ephemeral=True)
        except Exception as e:
            await ctx.send(f"❌ Erreur lors de la synchronisation: {e}", ephemeral=True)

    @commands.command(name='checkresults')
    @commands.has_permissions(administrator=True)
    async def check_results_command(self, ctx):
        """Force la vérification des résultats (admin uniquement)."""
        await ctx.send("🔄 Vérification forcée des résultats en cours...", ephemeral=True)
        
        try:
            await self.check_results_loop()
            await ctx.send("✅ Vérification des résultats terminée!", ephemeral=True)
        except Exception as e:
            await ctx.send(f"❌ Erreur lors de la vérification: {e}", ephemeral=True)

async def setup(bot):
    """Fonction requise par discord.py pour charger le Cog."""
    await bot.add_cog(EventsCog(bot))
