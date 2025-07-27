import discord
from discord.ext import commands, tasks
import feedparser
import asyncio
import os
from datetime import datetime, timezone, timedelta, date
import re
import pytz
import database
import aiohttp
import json
from bs4 import BeautifulSoup # Ajout nécessaire pour l'analyse HTML

# --- Configuration ---
RSS_URL = "https://handnews.fr/feed"
CHANNEL_ID = int(os.getenv('CHANNEL_ID')) if os.getenv('CHANNEL_ID') else None
CHECK_INTERVAL = 1800

# --- Configuration Livescore avec Browserless ---
LIVESCORE_URL = "https://www.livescore.in/fr/handball/france/starligue/"
MATCH_CHECK_INTERVAL = 3600
RESULTS_CHECK_INTERVAL = 1800

# --- CONFIGURATION BROWSERLESS CORRIGÉE ---
BROWSERLESS_API_TOKEN = os.getenv('BROWSERLESS_API_TOKEN')
# ON UTILISE MAINTENANT L'ENDPOINT /content QUI RETOURNE LE HTML COMPLET
BROWSERLESS_CONTENT_API_URL = f"https://production-sfo.browserless.io/content?token={BROWSERLESS_API_TOKEN}"


class EventsCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.seen_articles = set()
        self.first_check = True
        self.created_matches = {}
        
        if not BROWSERLESS_API_TOKEN:
            print("❌ (BROWSERLESS) ATTENTION: Le token BROWSERLESS_API_TOKEN n'est pas configuré. Le scraping ne fonctionnera pas.")

        self.check_rss_loop.start()
        self.check_matches_loop.start()
        self.check_results_loop.start()
        
    def cog_unload(self):
        self.check_rss_loop.cancel()
        self.check_matches_loop.cancel()
        self.check_results_loop.cancel()

    @tasks.loop(seconds=CHECK_INTERVAL)
    async def check_rss_loop(self):
        if CHANNEL_ID is None: 
            return
            
        channel = self.bot.get_channel(CHANNEL_ID)
        if not channel:
            print(f"❌ (RSS) Canal {CHANNEL_ID} introuvable!")
            return
            
        try:
            print("🔍 (RSS) Vérification du flux RSS...")
            async with aiohttp.ClientSession() as session:
                async with session.get(RSS_URL) as response:
                    if response.status == 200:
                        content = await response.text()
                        feed = feedparser.parse(content)
                    else:
                        print(f"⚠️ (RSS) Erreur HTTP {response.status}")
                        return

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
                print(f"⚠️ (RSS) Flux RSS invalide : {feed.bozo_exception}")
                
        except Exception as e:
            print(f"❌ (RSS) Erreur lors de la vérification : {e}")

    async def send_article_to_discord(self, channel, entry):
        try:
            embed = discord.Embed(
                title=entry.title[:256], url=entry.link, color=0xe8874f,
                timestamp=datetime.now(timezone.utc)
            )
            if hasattr(entry, 'summary'):
                description = re.sub('<[^<]+?>', '', entry.summary)
                embed.description = description[:2045] + "..." if len(description) > 2048 else description
            
            image_url = None
            if 'media_content' in entry and entry.media_content:
                image_url = entry.media_content[0]['url']
            
            if image_url:
                embed.set_image(url=image_url)
            
            embed.set_author(name="📰 Handnews.fr", icon_url="https://handnews.fr/favicon.ico", url="https://handnews.fr")
            
            if hasattr(entry, 'published'):
                try:
                    pub_date = datetime.strptime(entry.published, "%a, %d %b %Y %H:%M:%S %z")
                    embed.set_footer(text=f"Publié le {pub_date.strftime('%d/%m/%Y à %H:%M')}")
                except ValueError:
                    embed.set_footer(text=f"Publié le • {entry.published}")
                    
            await channel.send(embed=embed)
        except Exception as e:
            print(f"❌ (RSS) Erreur d'envoi de l'article : {e}")

    async def scrape_livescore_matches(self):
        """Récupère les matchs à venir via Browserless /content et les parse avec BeautifulSoup."""
        if not BROWSERLESS_API_TOKEN:
            print("❌ (BROWSERLESS) Scraping des matchs annulé, token manquant.")
            return []

        matches = []
        paris_tz = pytz.timezone('Europe/Paris')
        # La payload pour /content est beaucoup plus simple
        payload = {"url": LIVESCORE_URL}

        try:
            print("🌐 (BROWSERLESS) Lancement du scraping via /content...")
            async with aiohttp.ClientSession() as session:
                async with session.post(BROWSERLESS_CONTENT_API_URL, json=payload, timeout=45) as response:
                    if response.status != 200:
                        error_text = await response.text()
                        print(f"⚠️ (BROWSERLESS) Erreur HTTP {response.status} lors de la récupération du contenu : {error_text}")
                        return matches
                    # /content retourne directement le HTML de la page rendue
                    html = await response.text()
            
            # On parse le HTML fiable obtenu de Browserless avec BeautifulSoup
            soup = BeautifulSoup(html, 'html.parser')
            print("🔍 (BS4) Parsing du HTML reçu de Browserless...")
            
            now = datetime.now(paris_tz)
            
            # On trouve les conteneurs de chaque journée pour associer correctement les dates aux matchs
            day_containers = soup.select("div.sportName > div.event__round--static")
            
            for day_container in day_containers:
                date_text = day_container.get_text(strip=True).upper()
                match_date = None
                
                # Parser la date depuis l'en-tête de la journée
                if "AUJOURD'HUI" in date_text:
                    match_date = now.date()
                elif "DEMAIN" in date_text:
                    match_date = (now + timedelta(days=1)).date()
                else:
                    day_month_search = re.search(r'(\d{2})\.(\d{2})\.', date_text)
                    if day_month_search:
                        day, month = map(int, day_month_search.groups())
                        year = now.year
                        match_date = date(year, month, day)
                        if match_date < now.date():
                            match_date = match_date.replace(year=year + 1)
                
                if not match_date:
                    continue

                # Trouver les matchs qui sont les "frères" suivants de ce conteneur de date
                for match_container in day_container.find_next_siblings('div', class_=re.compile(r'event__match--scheduled')):
                    try:
                        home_elem = match_container.find(class_=re.compile(r'event__participant--home'))
                        away_elem = match_container.find(class_=re.compile(r'event__participant--away'))
                        time_elem = match_container.find(class_=re.compile(r'event__time'))
                        
                        if not all([home_elem, away_elem, time_elem]):
                            continue
                        
                        team1 = home_elem.get_text(strip=True)
                        team2 = away_elem.get_text(strip=True)
                        time_text = time_elem.get_text(strip=True)
                        match_id_raw = match_container.get('id', '')

                        if not all([team1, team2, time_text, match_id_raw]):
                            continue

                        match_id = match_id_raw.replace('g_4_', '')
                        hour, minute = map(int, time_text.split(':'))
                        
                        match_datetime = datetime.combine(match_date, datetime.min.time()).replace(hour=hour, minute=minute)
                        match_datetime_paris = paris_tz.localize(match_datetime)
                        match_datetime_utc = match_datetime_paris.astimezone(timezone.utc)

                        limit_date_utc = datetime.now(timezone.utc) + timedelta(days=5)
                        if now.astimezone(timezone.utc) < match_datetime_utc < limit_date_utc:
                            matches.append({
                                "team1": team1, "team2": team2,
                                "start_time_utc": match_datetime_utc,
                                "event_id": match_id
                            })
                    except Exception as e:
                        print(f"⚠️ (BS4) Erreur de parsing d'un match : {e}")
                        continue
            
            print(f"📊 (BS4) {len(matches)} matchs valides trouvés pour la création d'événements.")
            return matches

        except asyncio.TimeoutError:
            print("❌ (BROWSERLESS) La requête de contenu a expiré (timeout).")
        except Exception as e:
            print(f"❌ (BROWSERLESS) Erreur générale lors de la récupération du contenu: {type(e).__name__}: {str(e)}")
        
        return []

    @tasks.loop(seconds=MATCH_CHECK_INTERVAL)
    async def check_matches_loop(self):
        guild = self.bot.get_guild(self.bot.guild_id)
        if not guild:
            print(f"❌ (MATCHES) Serveur {self.bot.guild_id} introuvable!")
            return
        
        print("🔍 (MATCHES) Vérification des matchs de Starligue...")
        
        try:
            scraped_matches = await self.scrape_livescore_matches()
            
            if not scraped_matches:
                print("ℹ️ (MATCHES) Aucun nouveau match trouvé via le scraping.")
                return
            
            print(f"🏐 (MATCHES) {len(scraped_matches)} matchs trouvés dans la période J+5")
            
            journee_id, journee_numero = database.determine_journee_from_matches(scraped_matches)
            if not journee_id:
                print("⚠️ (MATCHES) Impossible de déterminer la journée pour les matchs trouvés.")
                return

            existing_events = guild.scheduled_events
            existing_event_names = {event.name for event in existing_events}
            
            pronos_cog = self.bot.get_cog('PronosticsCog')
            new_matches_for_pronos = []
            
            for match in scraped_matches:
                event_name = f"{match['team1']} vs {match['team2']}"
                if not database.get_match_by_event_id(match['event_id']) and event_name not in existing_event_names:
                    print(f"✨ (MATCHES) Création de l'événement: {event_name}")
                    try:
                        event = await guild.create_scheduled_event(
                            name=event_name,
                            description=f"Match de Starligue - Journée {journee_numero}\n\n📊 Faites vos pronostics dans le canal dédié !",
                            start_time=match['start_time_utc'],
                            end_time=match['start_time_utc'] + timedelta(hours=2),
                            entity_type=discord.EntityType.external,
                            location="Starligue Handball"
                        )
                        match_id = database.create_match(
                            journee_id, match['event_id'], event.id, match['team1'], match['team2'], match['start_time_utc']
                        )
                        if match_id:
                            new_matches_for_pronos.append({
                                'id': match_id, 'equipe1': match['team1'], 'equipe2': match['team2'],
                                'date_match': match['start_time_utc'], 'journee_numero': journee_numero
                            })
                    except discord.Forbidden:
                        print("❌ (MATCHES) Permission refusée pour créer un événement.")
                    except Exception as ex:
                        print(f"❌ (MATCHES) Erreur lors de la création de l'événement Discord : {ex}")

            if new_matches_for_pronos and pronos_cog:
                await pronos_cog.create_pronostic_messages_for_matches(new_matches_for_pronos)
            
        except Exception as e:
            print(f"❌ (MATCHES) Erreur critique dans la boucle des matchs: {e}")

    @tasks.loop(seconds=RESULTS_CHECK_INTERVAL)
    async def check_results_loop(self):
        print("🔍 (RESULTS) Vérification des résultats...")
        now_utc = datetime.now(timezone.utc)
        matches_to_check = database.get_matches_to_check_results(now_utc - timedelta(hours=2))
        
        if not matches_to_check:
            print("ℹ️ (RESULTS) Aucun match terminé à vérifier.")
            return

        pronos_cog = self.bot.get_cog('PronosticsCog')
        
        for match_row in matches_to_check:
            match = dict(match_row)
            try:
                result = await self.get_match_result(match['event_id'])
                if result and pronos_cog:
                    print(f"✅ (RESULTS) Résultat trouvé: {match['equipe1']} {result} {match['equipe2']}")
                    pronos_cog.process_match_result(match['id'], result)
                    await self.update_discord_event_with_result(
                        match['discord_event_id'], match['equipe1'], match['equipe2'], result
                    )
                    await asyncio.sleep(2)
            except Exception as e:
                print(f"❌ (RESULTS) Erreur vérification résultat pour match {match['id']}: {e}")

    async def get_match_result(self, event_id):
        if not BROWSERLESS_API_TOKEN:
            return None

        match_url = f"https://www.livescore.in/fr/match/{event_id}/"
        payload = {"url": match_url}
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(BROWSERLESS_CONTENT_API_URL, json=payload, timeout=30) as response:
                    if response.status != 200:
                        return None
                    html = await response.text()
            
            soup = BeautifulSoup(html, 'html.parser')
            
            status_elem = soup.find(class_=re.compile(r'fixedHeaderDuel__detailStatus'))
            if status_elem and "Terminé" in status_elem.get_text():
                home_score_elem = soup.select_one('.detailScore__wrapper > span:first-of-type')
                away_score_elem = soup.select_one('.detailScore__wrapper > span:last-of-type')

                if home_score_elem and away_score_elem:
                    score1 = home_score_elem.get_text(strip=True)
                    score2 = away_score_elem.get_text(strip=True)
                    
                    if score1.isdigit() and score2.isdigit():
                        return f"{score1}-{score2}"
                        
        except asyncio.TimeoutError:
            print(f"❌ (RESULTS) Timeout pour le match {event_id}")
        except Exception as e:
            print(f"❌ (RESULTS) Erreur scraping résultat pour {event_id}: {type(e).__name__}")
            
        return None

    async def update_discord_event_with_result(self, discord_event_id, team1, team2, score):
        try:
            guild = self.bot.get_guild(self.bot.guild_id)
            if not guild or not discord_event_id: return
            
            event = guild.get_scheduled_event(int(discord_event_id))
            if not event: return

            new_description = f"🏐 RÉSULTAT FINAL : {score}\n\n{event.description}"
            new_name = f"[TERMINÉ] {team1} {score} {team2}"

            if event.status == discord.EventStatus.scheduled:
                await event.edit(name=new_name, description=new_description)
            elif event.status in [discord.EventStatus.active, discord.EventStatus.completed]:
                await event.edit(description=new_description)

        except discord.NotFound:
             print(f"⚠️ (RESULTS) Événement Discord {discord_event_id} non trouvé.")
        except Exception as e:
            print(f"❌ (RESULTS) Erreur mise à jour événement {discord_event_id}: {e}")

    @check_rss_loop.before_loop
    @check_matches_loop.before_loop
    @check_results_loop.before_loop
    async def before_loops(self):
        await self.bot.wait_until_ready()

    @commands.command(name='handball')
    async def handball_command(self, ctx):
        guild = ctx.guild
        if not guild: return
        
        upcoming_events = [e for e in guild.scheduled_events if e.creator == self.bot.user and e.status == discord.EventStatus.scheduled and "vs" in e.name]
        
        if not upcoming_events:
            await ctx.send("Aucun match programmé pour le moment.", ephemeral=True)
            return
        
        upcoming_events.sort(key=lambda e: e.start_time)
        embed = discord.Embed(title="🏐 Prochains matchs de Starligue", color=discord.Color.orange())
        
        for event in upcoming_events[:10]:
            time_paris = event.start_time.astimezone(pytz.timezone('Europe/Paris'))
            embed.add_field(name=event.name, value=f"📅 {time_paris.strftime('%d/%m à %H:%M')}", inline=False)
        
        await ctx.send(embed=embed, ephemeral=True)

    @commands.command(name='forcesync')
    @commands.has_permissions(administrator=True)
    async def force_sync_command(self, ctx):
        await ctx.send("🔄 Synchronisation forcée des matchs en cours...", ephemeral=True, delete_after=5)
        async with ctx.typing():
            await self.check_matches_loop.coro(self)
        await ctx.send("✅ Synchronisation des matchs terminée!", ephemeral=True)

    @commands.command(name='checkresults')
    @commands.has_permissions(administrator=True)
    async def check_results_command(self, ctx):
        await ctx.send("🔄 Vérification forcée des résultats en cours...", ephemeral=True, delete_after=5)
        async with ctx.typing():
            await self.check_results_loop.coro(self)
        await ctx.send("✅ Vérification des résultats terminée!", ephemeral=True)

    @commands.command(name='debughtml')
    @commands.has_permissions(administrator=True)
    async def debug_html_command(self, ctx):
        await ctx.send("🐛 Commande dépréciée. Le scraping utilise maintenant l'API Browserless.", ephemeral=True)
        
async def setup(bot):
    await bot.add_cog(EventsCog(bot))
