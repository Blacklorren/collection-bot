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
from bs4 import BeautifulSoup

# --- Configuration ---
RSS_URL = "https://handnews.fr/feed"
CHANNEL_ID = int(os.getenv('CHANNEL_ID')) if os.getenv('CHANNEL_ID') else None
CHECK_INTERVAL = 1800  # 30 minutes pour RSS

# --- FRÉQUENCES OPTIMISÉES ---
LIVESCORE_URL = "https://www.livescore.in/fr/handball/france/starligue/"
# Vérification des nouveaux matchs toutes les 6 heures
MATCH_CHECK_INTERVAL = 21600
# Lancement de la boucle de résultats toutes les 30 minutes (avec un filtre horaire à l'intérieur)
RESULTS_CHECK_INTERVAL = 1800

# --- Configuration Browserless ---
BROWSERLESS_API_TOKEN = os.getenv('BROWSERLESS_API_TOKEN')
BROWSERLESS_CONTENT_API_URL = f"https://production-sfo.browserless.io/content?token={BROWSERLESS_API_TOKEN}"


class EventsCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.seen_articles = set()
        self.first_check = True
        self.created_matches = {}
        
        if not BROWSERLESS_API_TOKEN:
            print("❌ (BROWSERLESS) ATTENTION: Le token BROWSERLESS_API_TOKEN n'est pas configuré.")

        self.check_rss_loop.start()
        self.check_matches_loop.start()
        self.check_results_loop.start()
        
    def cog_unload(self):
        self.check_rss_loop.cancel()
        self.check_matches_loop.cancel()
        self.check_results_loop.cancel()

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
        """Récupère les matchs à venir via Browserless et les parse avec BeautifulSoup."""
        if not BROWSERLESS_API_TOKEN:
            print("❌ (BROWSERLESS) Scraping annulé, token manquant.")
            return []

        matches = []
        paris_tz = pytz.timezone('Europe/Paris')
        payload = {"url": LIVESCORE_URL}

        try:
            print("🌐 (BROWSERLESS) Lancement du scraping via /content...")
            async with aiohttp.ClientSession() as session:
                async with session.post(BROWSERLESS_CONTENT_API_URL, json=payload, timeout=45) as response:
                    if response.status != 200:
                        error_text = await response.text()
                        print(f"⚠️ (BROWSERLESS) Erreur HTTP {response.status} lors de la récupération du contenu : {error_text}")
                        return matches
                    html = await response.text()
            
            soup = BeautifulSoup(html, 'html.parser')
            print("🔍 (BS4) Parsing du HTML reçu de Browserless...")
            
            now = datetime.now(paris_tz)
            
            match_containers = soup.select("div.event__match--scheduled")
            
            for container in match_containers:
                try:
                    time_elem = container.find(class_="event__time")
                    if not time_elem: continue
                    
                    time_text = time_elem.get_text(strip=True)
                    match_time_parts = time_text.split(' ')
                    if len(match_time_parts) != 2: continue
                    
                    date_part, time_part = match_time_parts
                    day, month = map(int, date_part.split('.')[:2])
                    hour, minute = map(int, time_part.split(':'))
                    
                    year = now.year
                    match_date = date(year, month, day)
                    if match_date < now.date():
                        match_date = match_date.replace(year=year + 1)
                        
                    match_datetime_naive = datetime.combine(match_date, datetime.min.time()).replace(hour=hour, minute=minute)
                    match_datetime_paris = paris_tz.localize(match_datetime_naive)
                    match_datetime_utc = match_datetime_paris.astimezone(timezone.utc)

                    team1 = container.find(class_="event__participant--home").get_text(strip=True)
                    team2 = container.find(class_="event__participant--away").get_text(strip=True)
                    match_id_raw = container.get('id', '')
                    match_id = match_id_raw.replace('g_7_', '')

                    if not all([team1, team2, match_id]):
                        continue

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
        """Vérifie et crée les événements Discord pour les nouveaux matchs."""
        guild = self.bot.get_guild(self.bot.guild_id)
        if not guild:
            print(f"❌ (MATCHES) Serveur {self.bot.guild_id} introuvable!")
            return
        
        print(f"🔍 (MATCHES) Vérification des nouveaux matchs (toutes les {MATCH_CHECK_INTERVAL / 3600} heures)...")
        
        try:
            scraped_matches = await self.scrape_livescore_matches()
            
            if not scraped_matches:
                print("ℹ️ (MATCHES) Aucun nouveau match trouvé via le scraping.")
                return
            
            print(f"🏐 (MATCHES) {len(scraped_matches)} matchs trouvés dans la période J+5")
                       
            existing_event_names = {event.name for event in guild.scheduled_events}
            
            pronos_cog = self.bot.get_cog('PronosticsCog')
            new_matches_for_pronos = []
            
            for match in scraped_matches:
                event_name = f"{match['team1']} vs {match['team2']}"
                if not database.get_match_by_event_id(match['event_id']) and event_name not in existing_event_names:
                    print(f"✨ (MATCHES) Création de l'événement: {event_name}")
                    try:
                        event = await guild.create_scheduled_event(
                            name=event_name,
                            description=f"Match de Starligue\n\n📊 Faites vos pronostics dans le canal dédié !",
                            start_time=match['start_time_utc'],
                            end_time=match['start_time_utc'] + timedelta(hours=2),
                            entity_type=discord.EntityType.external,
                            location="Starligue Handball",
                            privacy_level=discord.PrivacyLevel.guild_only
                        )
                        match_id = database.create_match(
                            None, match['event_id'], event.id, match['team1'], match['team2'], match['start_time_utc']
                        )
                        if match_id:
                            new_matches_for_pronos.append({
                                'id': match_id, 'equipe1': match['team1'], 'equipe2': match['team2'],
                                'date_match': match['start_time_utc']
                            })
                    except discord.Forbidden:
                        print("❌ (MATCHES) Permission refusée pour créer un événement.")
                    except Exception as ex:
                        print(f"❌ (MATCHES) Erreur lors de la création de l'événement Discord : {ex}")

            if new_matches_for_pronos and pronos_cog:
                await pronos_cog.create_pronostic_messages_for_matches(new_matches_for_pronos)
            
        except Exception as e:
            print(f"❌ (MATCHES) Erreur critique dans la boucle des matchs: {e}")

    async def get_match_result(self, event_id):
        """Récupère le résultat d'un match spécifique via Browserless avec des logs de débogage détaillés."""
        
        print(f"\n--- [DEBUG-SCRAPER] Début du traitement pour l'event_id : {event_id} ---")

        if not BROWSERLESS_API_TOKEN:
            print("[DEBUG-SCRAPER] ❌ ERREUR: Token Browserless manquant.")
            return None

        match_url = f"https://www.livescore.in/fr/match/{event_id}/"
        print(f"[DEBUG-SCRAPER] 🔗 URL cible : {match_url}")

        payload = {"url": match_url}
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(BROWSERLESS_CONTENT_API_URL, json=payload, timeout=30) as response:
                    print(f"[DEBUG-SCRAPER] STATUS HTTP: {response.status}")
                    if response.status != 200:
                        print(f"[DEBUG-SCRAPER] ❌ Échec du chargement de la page. L'ID '{event_id}' est peut-être incorrect.")
                        return None
                    html = await response.text()
            
            soup = BeautifulSoup(html, 'html.parser')
            
            # Étape 1: Chercher le statut "Terminé"
            print("[DEBUG-SCRAPER] 1. Recherche du statut 'Terminé' (div.detail-finished)...")
            status_elem = soup.find('div', class_='detail-finished')
            
            if not status_elem:
                print("[DEBUG-SCRAPER] ❌ Statut 'Terminé' NON trouvé. Le match n'est peut-être pas fini ou la structure a changé.")
                return None
            
            print("[DEBUG-SCRAPER] ✅ Statut 'Terminé' trouvé !")

            # Étape 2: Chercher les scores
            print("[DEBUG-SCRAPER] 2. Recherche des scores (div.duelParticipant__score)...")
            score_elems = soup.select('div.duelParticipant__score')
            print(f"[DEBUG-SCRAPER] 📊 {len(score_elems)} élément(s) de score trouvés.")

            if len(score_elems) < 2:
                print("[DEBUG-SCRAPER] ❌ Moins de 2 scores trouvés. Impossible de déterminer le résultat.")
                return None
            
            score1 = score_elems[0].get_text(strip=True)
            score2 = score_elems[1].get_text(strip=True)
            print(f"[DEBUG-SCRAPER] ✅ Scores extraits : Domicile='{score1}', Extérieur='{score2}'")

            if score1.isdigit() and score2.isdigit():
                final_score = f"{score1}-{score2}"
                print(f"[DEBUG-SCRAPER] ✅ Résultat final validé : {final_score}")
                print(f"--- [DEBUG-SCRAPER] Fin du traitement pour {event_id} ---")
                return final_score
            else:
                print(f"[DEBUG-SCRAPER] ❌ Les scores extraits ('{score1}', '{score2}') ne sont pas des nombres.")
                return None
                        
        except asyncio.TimeoutError:
            print(f"❌ (RESULTS) Timeout pour le match {event_id}")
        except Exception as e:
            print(f"❌ (RESULTS) Erreur scraping résultat pour {event_id}: {type(e).__name__}")
            
        print(f"--- [DEBUG-SCRAPER] Fin du traitement (sans succès) pour {event_id} ---")
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
            elif event.status in [discord.EventStatus.active, discord.EventStatus.completed]:
                await event.edit(description=new_description)

        except discord.NotFound:
             print(f"⚠️ (RESULTS) Événement Discord {discord_event_id} non trouvé.")
        except Exception as e:
            print(f"❌ (RESULTS) Erreur mise à jour événement {discord_event_id}: {e}")
    
    # --- DÉBUT DES MODIFICATIONS ---

    async def _internal_check_and_process_results(self):
        """
        La logique de base pour vérifier et traiter les résultats.
        Cette fonction est réutilisable par la boucle et la commande manuelle.
        """
        print("🔍 (RESULTS) Lancement de la vérification des résultats...")
        now_utc = datetime.now(timezone.utc)
        # On vérifie les matchs qui auraient dû se terminer dans les dernières 24h
        matches_to_check = database.get_matches_to_check_results(now_utc - timedelta(hours=24))
        
        if not matches_to_check:
            print("ℹ️ (RESULTS) Aucun match terminé à vérifier pour le moment.")
            return 0 # Retourne 0 match traité

        pronos_cog = self.bot.get_cog('PronosticsCog')
        processed_count = 0
        
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
                    processed_count += 1
                    await asyncio.sleep(2) # Garder une pause pour ne pas surcharger les APIs
            except Exception as e:
                print(f"❌ (RESULTS) Erreur vérification résultat pour match {match['id']}: {e}")
        
        print(f"📊 (RESULTS) Fin de la vérification. {processed_count} match(s) traité(s).")
        return processed_count

    @tasks.loop(seconds=RESULTS_CHECK_INTERVAL)
    async def check_results_loop(self):
        """Vérifie et met à jour les résultats des matchs terminés PENDANT les heures de match."""
        paris_tz = pytz.timezone('Europe/Paris')
        now_paris = datetime.now(paris_tz)
        
        # La contrainte horaire ne s'applique QU'À la boucle automatique
        if not (16 <= now_paris.hour < 24):
            # On ne logue plus pour éviter le spam
            # print(f"ℹ️ (RESULTS) Hors des heures de match ({now_paris.strftime('%H:%M')}). Vérification auto ignorée.")
            return

        await self._internal_check_and_process_results()

    @check_rss_loop.before_loop
    @check_matches_loop.before_loop
    @check_results_loop.before_loop
    async def before_loops(self):
        """Attend que le bot soit prêt avant de lancer les boucles."""
        await self.bot.wait_until_ready()

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
            embed.add_field(name=event.name, value=f"📅 {time_paris.strftime('%d/%m à %H:%M')}", inline=False)
        
        await ctx.send(embed=embed, ephemeral=True)

    @commands.command(name='forcesync', aliases=['fs'])
    @commands.has_permissions(administrator=True)
    async def force_sync_command(self, ctx):
        """Force la synchronisation des matchs (admin uniquement)."""
        await ctx.send("🔄 Synchronisation forcée des matchs en cours...", ephemeral=True, delete_after=5)
        async with ctx.typing():
            await self.check_matches_loop.coro(self)
        await ctx.send("✅ Synchronisation des matchs terminée!", ephemeral=True)

    @commands.command(name='checkresults', aliases=['cr'])
    @commands.has_permissions(administrator=True)
    async def check_results_command(self, ctx):
        """[Admin] Force la vérification des résultats, même en dehors des heures de match."""
        await ctx.send("🔄 Forçage de la vérification des résultats en cours...", ephemeral=True, delete_after=10)
        async with ctx.typing():
            # On appelle directement la logique interne, en ignorant la contrainte horaire
            count = await self._internal_check_and_process_results()
        await ctx.send(f"✅ Vérification terminée ! **{count}** match(s) ont été traités.", ephemeral=True)

    @commands.command(name='debughtml')
    @commands.has_permissions(administrator=True)
    async def debug_html_command(self, ctx):
        """Commande dépréciée."""
        await ctx.send("🐛 Commande dépréciée. Le scraping utilise maintenant l'API Browserless. Utilisez `!test savehtml`.", ephemeral=True)
        
async def setup(bot):
    await bot.add_cog(EventsCog(bot))
