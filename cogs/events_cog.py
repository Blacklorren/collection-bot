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
from bs4 import BeautifulSoup

# Configuration
RSS_URL = "https://handnews.fr/feed"
CHANNEL_ID = int(os.getenv('CHANNEL_ID')) if os.getenv('CHANNEL_ID') else None
CHECK_INTERVAL = 1800  # 30 minutes pour RSS

# Configuration Livescore
LIVESCORE_URL = "https://www.livescore.in/fr/handball/france/starligue/"
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

    # === GESTION RSS (INCHANGÉ) ===
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
            embed = discord.Embed(
                title=entry.title[:256], url=entry.link, color=0xe8874f,
                timestamp=datetime.now(timezone.utc)
            )
            if hasattr(entry, 'summary'):
                description = re.sub('<[^<]+?>', '', entry.summary)
                embed.description = description[:2045] + "..." if len(description) > 2048 else description
            image_url = None
            if 'media_content' in entry and entry.media_content: image_url = entry.media_content[0]['url']
            if image_url: embed.set_image(url=image_url)
            embed.set_author(name="📰 Handnews.fr", icon_url="https://handnews.fr/favicon.ico", url="https://handnews.fr")
            if hasattr(entry, 'published'):
                try:
                    pub_date = datetime.strptime(entry.published, "%a, %d %b %Y %H:%M:%S %z")
                    embed.set_footer(text=f"Publié le {pub_date.strftime('%d/%m/%Y à %H:%M')}")
                except ValueError:
                    embed.set_footer(text=f"Publié le • {entry.published}")
            await channel.send(embed=embed)
        except Exception as e:
            print(f"❌ (RSS) Erreur générale : {e}")

    # === GESTION DES MATCHS - VERSION SIMPLE HTML ===
    async def scrape_livescore_matches(self):
        """Récupère les matchs à venir via parsing HTML simple."""
        matches = []
        paris_tz = pytz.timezone('Europe/Paris')
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'fr-FR,fr;q=0.9',
            'Accept-Encoding': 'gzip, deflate',
            'Connection': 'keep-alive',
        }
        
        try:
            print("🌐 (HTML) Récupération de la page Livescore...")
            
            async with aiohttp.ClientSession() as session:
                async with session.get(LIVESCORE_URL, headers=headers) as response:
                    if response.status != 200:
                        print(f"⚠️ (HTML) Status HTTP {response.status}")
                        return matches
                    
                    html = await response.text()
                    soup = BeautifulSoup(html, 'html.parser')
                    
                    # Sauvegarde du HTML pour debug (optionnel)
                    if os.getenv('DEBUG_HTML', 'false').lower() == 'true':
                        with open('livescore_debug.html', 'w', encoding='utf-8') as f:
                            f.write(html)
                        print("📝 (DEBUG) HTML sauvegardé dans livescore_debug.html")
                    
                    # Stratégie 1: Chercher les conteneurs de match par classe
                    match_containers = soup.find_all('div', class_=re.compile(r'event__match'))
                    print(f"📊 (HTML) {len(match_containers)} conteneurs de match trouvés")
                    
                    if not match_containers:
                        # Stratégie 2: Chercher par structure
                        # Essayer de trouver des patterns dans le HTML
                        all_divs = soup.find_all('div')
                        for div in all_divs:
                            # Chercher des divs qui contiennent des patterns de match
                            text_content = div.get_text(strip=True)
                            if ' vs ' in text_content or ' - ' in text_content:
                                # Possiblement un match
                                pass
                    
                    # Parser les matchs trouvés
                    now = datetime.now(paris_tz)
                    for idx, container in enumerate(match_containers):
                        try:
                            # Extraire les classes pour identifier le type
                            classes = container.get('class', [])
                            
                            # Vérifier si c'est un match programmé
                            if 'event__match--scheduled' in ' '.join(classes):
                                # Extraire les équipes
                                home_elem = container.find(class_=re.compile(r'event__participant--home'))
                                away_elem = container.find(class_=re.compile(r'event__participant--away'))
                                time_elem = container.find(class_=re.compile(r'event__time'))
                                
                                if home_elem and away_elem and time_elem:
                                    team1 = home_elem.get_text(strip=True)
                                    team2 = away_elem.get_text(strip=True)
                                    time_text = time_elem.get_text(strip=True)
                                    
                                    # Extraire l'ID du match
                                    match_id = container.get('id', f'unknown_{idx}')
                                    if match_id.startswith('g_'):
                                        match_id = match_id[4:]  # Enlever le préfixe
                                    
                                    # Parser l'heure (format HH:MM)
                                    if ':' in time_text:
                                        hour, minute = map(int, time_text.split(':'))
                                        
                                        # Déterminer la date du match
                                        # Chercher le conteneur parent pour la date
                                        parent = container.find_parent('div', class_=re.compile(r'sportName'))
                                        if parent:
                                            date_elem = parent.find(class_=re.compile(r'event__round'))
                                            if date_elem:
                                                date_text = date_elem.get_text(strip=True).upper()
                                                
                                                if "AUJOURD'HUI" in date_text:
                                                    match_date = now.date()
                                                elif "DEMAIN" in date_text:
                                                    match_date = (now + timedelta(days=1)).date()
                                                else:
                                                    # Parser la date DD.MM
                                                    try:
                                                        day_month = date_text.split()[0]
                                                        if '.' in day_month:
                                                            day, month = day_month.split('.')[:2]
                                                            year = now.year
                                                            match_date = date(year, int(month), int(day))
                                                            # Ajuster l'année si nécessaire
                                                            if match_date < now.date():
                                                                match_date = match_date.replace(year=year + 1)
                                                    except:
                                                        continue
                                                
                                                # Créer le datetime complet
                                                match_datetime = datetime.combine(match_date, datetime.min.time())
                                                match_datetime = match_datetime.replace(hour=hour, minute=minute)
                                                match_datetime_paris = paris_tz.localize(match_datetime)
                                                match_datetime_utc = match_datetime_paris.astimezone(timezone.utc)
                                                
                                                # Vérifier si le match est dans les 5 prochains jours
                                                limit_date = datetime.now(timezone.utc) + timedelta(days=5)
                                                if now.astimezone(timezone.utc) < match_datetime_utc < limit_date:
                                                    matches.append({
                                                        "team1": team1,
                                                        "team2": team2,
                                                        "start_time_utc": match_datetime_utc,
                                                        "event_id": match_id
                                                    })
                                                    print(f"✅ Match trouvé: {team1} vs {team2} le {match_datetime_paris}")
                                
                        except Exception as e:
                            print(f"⚠️ (HTML) Erreur parsing match {idx}: {e}")
                            continue
                    
                    print(f"📊 (HTML) {len(matches)} matchs dans les 5 prochains jours")
                    
        except Exception as e:
            print(f"❌ (HTML) Erreur lors du scraping: {type(e).__name__}: {str(e)}")
            
        return matches

    @tasks.loop(seconds=MATCH_CHECK_INTERVAL)
    async def check_matches_loop(self):
        """Vérifie et crée les événements Discord pour les nouveaux matchs."""
        guild = self.bot.get_guild(self.bot.guild_id)
        if not guild:
            print(f"❌ (MATCHES) Serveur {self.bot.guild_id} introuvable!")
            return
        
        print("🔍 (MATCHES) Vérification des matchs de Starligue...")
        
        try:
            scraped_matches = await self.scrape_livescore_matches()
            
            if not scraped_matches:
                print("ℹ️ (MATCHES) Aucun match trouvé.")
                return
            
            print(f"🏐 (MATCHES) {len(scraped_matches)} matchs trouvés (J+5)")
            
            journee_id, journee_numero = database.determine_journee_from_matches(scraped_matches)
            existing_events = guild.scheduled_events
            existing_event_names = {event.name for event in existing_events}
            
            pronos_cog = self.bot.get_cog('PronosticsCog')
            new_matches_for_pronos = []
            
            for match in scraped_matches:
                event_name = f"{match['team1']} vs {match['team2']}"
                if not database.get_match_by_event_id(match['event_id']) and event_name not in existing_event_names:
                    print(f"✨ (MATCHES) Création événement: {event_name}")
                    event = await guild.create_scheduled_event(
                        name=event_name,
                        description=f"Match de Starligue - Journée {journee_numero}\n\n📊 Faites vos pronostics dans le canal dédié !",
                        start_time=match['start_time_utc'], end_time=match['start_time_utc'] + timedelta(hours=2),
                        entity_type=discord.EntityType.external, location="Starligue Handball"
                    )
                    match_id = database.create_match(
                        journee_id, match['event_id'], event.id, match['team1'], match['team2'], match['start_time_utc']
                    )
                    new_matches_for_pronos.append({
                        'id': match_id, 'equipe1': match['team1'], 'equipe2': match['team2'],
                        'date_match': match['start_time_utc'], 'journee_numero': journee_numero
                    })

            if new_matches_for_pronos and pronos_cog:
                await pronos_cog.create_pronostic_messages_for_matches(new_matches_for_pronos)
            
        except Exception as e:
            print(f"❌ (MATCHES) Erreur dans la boucle: {e}")

    @tasks.loop(seconds=RESULTS_CHECK_INTERVAL)
    async def check_results_loop(self):
        """Vérifie et met à jour les résultats des matchs terminés."""
        print("🔍 (RESULTS) Vérification des résultats...")
        now_utc = datetime.now(timezone.utc)
        matches_to_check = database.get_matches_to_check_results(now_utc - timedelta(hours=2))
        
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
            except Exception as e:
                print(f"❌ (RESULTS) Erreur pour le match {match['id']}: {e}")

    async def get_match_result(self, event_id):
        """Récupère le résultat d'un match spécifique via parsing HTML."""
        match_url = f"https://www.livescore.in/fr/match/{event_id}/"
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(match_url, headers=headers) as response:
                    if response.status != 200:
                        return None
                    
                    html = await response.text()
                    soup = BeautifulSoup(html, 'html.parser')
                    
                    # Chercher le statut du match
                    status_elem = soup.find(class_=re.compile(r'fixedHeaderDuel__detailStatus'))
                    if status_elem and "Terminé" in status_elem.get_text():
                        # Chercher les scores
                        score_elems = soup.find_all(class_=re.compile(r'detailScore__wrapper'))
                        if len(score_elems) >= 2:
                            score1 = score_elems[0].get_text(strip=True)
                            score2 = score_elems[1].get_text(strip=True)
                            
                            if score1.isdigit() and score2.isdigit():
                                return f"{score1}-{score2}"
                    
        except Exception as e:
            print(f"❌ (RESULTS) Erreur: {type(e).__name__}")
            
        return None

    async def update_discord_event_with_result(self, discord_event_id, team1, team2, score):
        """Met à jour l'événement Discord avec le résultat du match."""
        try:
            guild = self.bot.get_guild(self.bot.guild_id)
            if not guild or not discord_event_id: return
            
            event = guild.get_scheduled_event(int(discord_event_id))
            if not event: return

            new_description = f"🏐 RÉSULTAT FINAL : {score}\n\n{event.description}"
            new_name = f"[TERMINÉ] {team1} {score} {team2}"

            if event.status == discord.EventStatus.scheduled:
                await event.edit(name=new_name, description=new_description)
            else:
                await event.edit(description=new_description)
        except Exception as e:
            print(f"❌ (RESULTS) Erreur mise à jour événement Discord: {e}")

    @check_rss_loop.before_loop
    @check_matches_loop.before_loop
    @check_results_loop.before_loop
    async def before_loops(self):
        await self.bot.wait_until_ready()

    # === COMMANDES ===
    @commands.command(name='handball')
    async def handball_command(self, ctx):
        """Affiche les prochains matchs de handball."""
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
            embed.add_field(name=event.name, value=f"📅 {time_paris.strftime('%d/%m à %H:%M')}", inline=True)
        
        await ctx.send(embed=embed, ephemeral=True)

    @commands.command(name='forcesync')
    @commands.has_permissions(administrator=True)
    async def force_sync_command(self, ctx):
        """Force la synchronisation des matchs (admin uniquement)."""
        await ctx.send("🔄 Synchronisation forcée des matchs en cours...", ephemeral=True, delete_after=5)
        async with ctx.typing():
            await self.check_matches_loop.coro(self)
        await ctx.send("✅ Synchronisation des matchs terminée!", ephemeral=True)

    @commands.command(name='checkresults')
    @commands.has_permissions(administrator=True)
    async def check_results_command(self, ctx):
        """Force la vérification des résultats (admin uniquement)."""
        await ctx.send("🔄 Vérification forcée des résultats en cours...", ephemeral=True, delete_after=5)
        async with ctx.typing():
            await self.check_results_loop.coro(self)
        await ctx.send("✅ Vérification des résultats terminée!", ephemeral=True)

    @commands.command(name='debughtml')
    @commands.has_permissions(administrator=True)
    async def debug_html_command(self, ctx):
        """Active le mode debug HTML pour sauvegarder la page (admin uniquement)."""
        os.environ['DEBUG_HTML'] = 'true'
        await ctx.send("🐛 Mode debug HTML activé. Le prochain scraping sauvegardera le HTML.", ephemeral=True)
        
async def setup(bot):
    await bot.add_cog(EventsCog(bot))
