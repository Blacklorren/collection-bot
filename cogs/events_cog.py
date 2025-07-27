import discord
from discord.ext import commands, tasks
import feedparser
import asyncio
import os
from datetime import datetime, timezone, timedelta, date
import re
from playwright.async_api import async_playwright
import pytz
import database
import aiohttp
from bs4 import BeautifulSoup
import json

# Configuration
RSS_URL = "https://handnews.fr/feed"
CHANNEL_ID = int(os.getenv('CHANNEL_ID')) if os.getenv('CHANNEL_ID') else None
CHECK_INTERVAL = 1800  # 30 minutes pour RSS

# Configuration Livescore
LIVESCORE_URL = "https://www.livescore.in/fr/handball/france/starligue/"
MATCH_CHECK_INTERVAL = 3600  # Vérifier les matchs toutes les heures
RESULTS_CHECK_INTERVAL = 1800  # Vérifier les résultats toutes les 30 minutes

# Détection de l'environnement
IS_RAILWAY = os.getenv('RAILWAY_ENVIRONMENT') is not None
USE_PLAYWRIGHT = os.getenv('USE_PLAYWRIGHT', 'true').lower() == 'true' and not IS_RAILWAY

class EventsCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.seen_articles = set()
        self.first_check = True
        self.created_matches = {}
        self.matches_cache = None
        self.cache_timestamp = None
        self.CACHE_DURATION = 300  # 5 minutes
        
        # Démarrer les tâches
        self.check_rss_loop.start()
        self.check_matches_loop.start()
        self.check_results_loop.start()
        
        # Log de l'environnement
        if IS_RAILWAY:
            print("🚂 (ENV) Détection Railway - Mode scraping alternatif activé")
        else:
            print("💻 (ENV) Environnement local - Playwright activé")
        
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

    # === MÉTHODE DE SCRAPING ALTERNATIVE POUR RAILWAY ===
    async def scrape_livescore_api(self):
        """Méthode alternative de scraping via API/HTML parsing pour Railway."""
        matches = []
        paris_tz = pytz.timezone('Europe/Paris')
        
        try:
            print("🌐 (API) Tentative de scraping via méthode alternative...")
            
            # Headers pour simuler un navigateur
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                'Accept-Language': 'fr-FR,fr;q=0.9,en;q=0.8',
                'Accept-Encoding': 'gzip, deflate, br',
                'DNT': '1',
                'Connection': 'keep-alive',
                'Upgrade-Insecure-Requests': '1',
                'Cache-Control': 'max-age=0'
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.get(LIVESCORE_URL, headers=headers) as response:
                    if response.status != 200:
                        print(f"⚠️ (API) Status HTTP {response.status}")
                        return matches
                    
                    html = await response.text()
                    
                    # Recherche de données JSON intégrées
                    json_pattern = r'window\.__data\s*=\s*({.*?});'
                    json_match = re.search(json_pattern, html, re.DOTALL)
                    
                    if json_match:
                        print("✅ (API) Données JSON trouvées dans la page")
                        # Traitement des données JSON si trouvées
                        # Note: Cette partie dépend de la structure exacte du site
                        return matches
                    
                    # Fallback: parsing HTML basique avec BeautifulSoup
                    soup = BeautifulSoup(html, 'html.parser')
                    
                    # Tentative de récupération basique d'informations
                    # Note: Ces sélecteurs peuvent nécessiter des ajustements
                    match_elements = soup.find_all('div', class_='event__match')
                    
                    if match_elements:
                        print(f"📊 (API) {len(match_elements)} éléments de match trouvés")
                    else:
                        print("⚠️ (API) Aucun match trouvé via parsing HTML")
                        
        except Exception as e:
            print(f"❌ (API) Erreur lors du scraping alternatif: {type(e).__name__}: {str(e)}")
            
        return matches

    # === GESTION DES MATCHS - VERSION HYBRIDE ===
    async def scrape_livescore_matches(self):
        """Récupère les matchs à venir - méthode hybride."""
        # Utiliser le cache si disponible
        if self.matches_cache and self.cache_timestamp:
            if (datetime.now() - self.cache_timestamp).seconds < self.CACHE_DURATION:
                print("📦 (CACHE) Utilisation des matchs en cache")
                return self.matches_cache
        
        # Sur Railway, utiliser la méthode alternative
        if IS_RAILWAY or not USE_PLAYWRIGHT:
            matches = await self.scrape_livescore_api()
            if matches:
                self.matches_cache = matches
                self.cache_timestamp = datetime.now()
            return matches
        
        # Sinon, utiliser Playwright
        return await self.scrape_livescore_with_playwright()

    async def scrape_livescore_with_playwright(self):
        """Méthode originale avec Playwright (pour environnement local)."""
        matches = []
        paris_tz = pytz.timezone('Europe/Paris')
        
        async with async_playwright() as p:
            browser = None
            try:
                print("🎭 (PLAYWRIGHT) Lancement du navigateur...")
                browser = await p.firefox.launch(
                    headless=True,
                    args=['--no-sandbox', '--disable-setuid-sandbox']
                )
                page = await browser.new_page()
                
                await page.goto(LIVESCORE_URL, wait_until='domcontentloaded', timeout=30000)
                
                # Gestion des cookies
                try:
                    if await page.locator("#onetrust-accept-btn-handler").is_visible(timeout=5000):
                        await page.click("#onetrust-accept-btn-handler")
                except:
                    pass
                
                await page.wait_for_selector(".event__match--scheduled", timeout=15000)
                
                all_days = await page.locator(".sportName.handball > div > div").all()
                
                now = datetime.now(paris_tz)
                
                for day_container in all_days:
                    date_text_raw = await day_container.locator(".event__round").inner_text()
                    date_text = date_text_raw.strip().upper()
                    
                    current_match_date = None
                    today = now.date()

                    if "AUJOURD'HUI" in date_text:
                        current_match_date = today
                    elif "DEMAIN" in date_text:
                        current_match_date = today + timedelta(days=1)
                    else:
                        day_str, month_str = date_text.split('.')[:2]
                        m_date = date(now.year, int(month_str), int(day_str))
                        if m_date < today:
                            m_date = m_date.replace(year=now.year + 1)
                        current_match_date = m_date
                        
                    if not current_match_date: continue

                    event_elements = await day_container.locator(".event__match--scheduled").all()
                    for event_element in event_elements:
                        time_text = await event_element.locator(".event__time").inner_text()
                        team1 = await event_element.locator(".event__participant--home").inner_text()
                        team2 = await event_element.locator(".event__participant--away").inner_text()
                        event_id_full = await event_element.get_attribute("id")

                        if not all([time_text, team1, team2, event_id_full]): continue

                        event_id = event_id_full[4:]
                        hour, minute = map(int, time_text.split(':'))
                        
                        match_time_paris = datetime.combine(current_match_date, datetime.min.time()).replace(hour=hour, minute=minute)
                        match_time_paris = paris_tz.localize(match_time_paris)
                        match_time_utc = match_time_paris.astimezone(timezone.utc)
                        
                        limit_date = datetime.now(timezone.utc) + timedelta(days=5)
                        if now.astimezone(timezone.utc) < match_time_utc < limit_date:
                            matches.append({
                                "team1": team1.strip(), "team2": team2.strip(),
                                "start_time_utc": match_time_utc, "event_id": event_id
                            })
                            
                print(f"✅ (PLAYWRIGHT) {len(matches)} matchs trouvés")
                
            except asyncio.TimeoutError:
                print("⚠️ (PLAYWRIGHT) Timeout lors du chargement")
            except Exception as e:
                print(f"❌ (PLAYWRIGHT) Erreur: {type(e).__name__}: {str(e)}")
            finally:
                if browser:
                    await browser.close()
                    
        # Mise en cache
        if matches:
            self.matches_cache = matches
            self.cache_timestamp = datetime.now()
            
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
                if IS_RAILWAY:
                    print("💡 (MATCHES) Sur Railway, le scraping JavaScript nécessite une API ou une solution headless spécifique.")
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
        """Récupère le résultat d'un match spécifique."""
        if IS_RAILWAY or not USE_PLAYWRIGHT:
            # Méthode alternative pour Railway
            return await self.get_match_result_api(event_id)
        else:
            # Méthode Playwright pour environnement local
            return await self.get_match_result_playwright(event_id)

    async def get_match_result_api(self, event_id):
        """Méthode alternative pour récupérer les résultats (Railway)."""
        match_url = f"https://www.livescore.in/fr/match/{event_id}/"
        
        try:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.get(match_url, headers=headers) as response:
                    if response.status != 200:
                        return None
                    
                    html = await response.text()
                    # Note: Parsing basique, peut nécessiter des ajustements
                    if "Terminé" in html:
                        # Tentative d'extraction du score via regex
                        score_pattern = r'<span class="detailScore__wrapper">(\d+)</span>.*?<span class="detailScore__wrapper">(\d+)</span>'
                        score_match = re.search(score_pattern, html, re.DOTALL)
                        if score_match:
                            return f"{score_match.group(1)}-{score_match.group(2)}"
                            
        except Exception as e:
            print(f"❌ (RESULTS API) Erreur: {type(e).__name__}")
            
        return None

    async def get_match_result_playwright(self, event_id):
        """Récupère le résultat via Playwright (environnement local)."""
        match_url = f"https://www.livescore.in/fr/match/{event_id}/"
        
        async with async_playwright() as p:
            browser = None
            try:
                browser = await p.firefox.launch(headless=True)
                page = await browser.new_page()
                
                await page.goto(match_url, timeout=20000)
                
                try:
                    if await page.locator("#onetrust-accept-btn-handler").is_visible(timeout=3000):
                        await page.click("#onetrust-accept-btn-handler")
                except:
                    pass

                status_text = await page.locator(".fixedHeaderDuel__detailStatus").inner_text()
                if "Terminé" not in status_text:
                    return None

                score1 = await page.locator(".detailScore__wrapper span").nth(0).inner_text()
                score2 = await page.locator(".detailScore__wrapper span").nth(2).inner_text()
                
                if score1.isdigit() and score2.isdigit():
                    return f"{score1}-{score2}"
                    
            except Exception as e:
                print(f"❌ (RESULTS PLAYWRIGHT) Erreur: {type(e).__name__}")
            finally:
                if browser:
                    await browser.close()
                    
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

async def setup(bot):
    await bot.add_cog(EventsCog(bot))
