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

LIVESCORE_URL = "https://www.livescore.in/fr/handball/france/starligue/"

# --- Configuration ---
RSS_URL = "https://handnews.fr/feed"
CHANNEL_ID = int(os.getenv('CHANNEL_ID')) if os.getenv('CHANNEL_ID') else None
CHECK_INTERVAL = 1800  # 30 minutes pour RSS

# --- CONFIGURATION SLACK ---
# URL du Webhook Incoming (Format: https://hooks.slack.com/services/...)
SLACK_WEBHOOK_URL = os.getenv('SLACK_WEBHOOK_URL')

# --- FRÉQUENCES OPTIMISÉES ---
MATCH_CHECK_INTERVAL = 86400 # 24 heures
RESULTS_CHECK_INTERVAL = 7200 # 2 heures

# --- Configuration Browserless ---
BROWSERLESS_API_TOKEN = os.getenv('BROWSERLESS_API_TOKEN')
BROWSERLESS_CONTENT_API_URL = f"https://production-sfo.browserless.io/content?token={BROWSERLESS_API_TOKEN}"

class EventsCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.seen_articles = set()
        self.first_check = True
        
        # 1. LISTE COMPLÈTE (Sources pour SLACK + DISCORD)
        # Mettez ici TOUS les liens Livescore que vous voulez suivre
        self.COMPETITIONS = [
            {"name": "Starligue", "url": "https://www.livescore.in/fr/handball/france/starligue/"},
            {"name": "Euro", "url": "https://www.livescore.in/fr/handball/europe/ehf-euro/"},
        # --- Discord Uniquement ---
            {"name": "LFH", "url": "https://www.livescore.in/fr/handball/france/division-1-femmes/"},
            {"name": "Euro Féminin", "url": "https://www.livescore.in/fr/handball/europe/ehf-euro-women/", "teams_filter": ["france"]},
            {"name": "Mondial Masculin", "url": "https://www.livescore.in/fr/handball/monde/championnat-du-monde/", "teams_filter": ["france"]},
            {"name": "Mondial Féminin", "url": "https://www.livescore.in/fr/handball/europe/ehf-euro/", "teams_filter": ["france"]},
            {"name": "Ligue des Champions Masculine", "url": "https://www.livescore.in/fr/handball/europe/ligue-des-champions", "teams_filter": ["paris", "nantes"]},
            {"name": "Ligue des Champions Féminine", "url": "https://www.livescore.in/fr/handball/europe/ligue-des-champions-femmes/", "teams_filter": ["metz", "brest"]},
            {"name": "Ligue Européene Masculine", "url": "https://www.livescore.in/fr/handball/europe/ligue-europeenne/", "teams_filter": ["montpellier"]},
            {"name": "Ligue Européenne Féminine", "url": "https://www.livescore.in/fr/handball/europe/ligue-europeenne-femmes/", "teams_filter": ["chambray", "dijon"]}
        ]
        
        # 2. FILTRE DISCORD
        # Seules ces compétitions généreront un Event Discord et des Pronostics
        self.DISCORD_COMPETITIONS_NAMES = ["Starligue", "Euro"]
        
        if not BROWSERLESS_API_TOKEN:
            print("❌ (BROWSERLESS) ATTENTION: Le token BROWSERLESS_API_TOKEN n'est pas configuré.")
        
        if not SLACK_WEBHOOK_URL:
             print("⚠️ (SLACK) ATTENTION: SLACK_WEBHOOK_URL non configuré. L'envoi vers Slack est désactivé.")

        # Démarrage des boucles
        self.check_rss_loop.start()
        self.check_matches_loop.start()
        self.check_results_loop.start()
        
    def cog_unload(self):
        self.check_rss_loop.cancel()
        self.check_matches_loop.cancel()
        self.check_results_loop.cancel()

    # --- PARTIE 1 : FLUX RSS (Inchangé) ---
    @tasks.loop(seconds=CHECK_INTERVAL)
    async def check_rss_loop(self):
        if CHANNEL_ID is None: return
        channel = self.bot.get_channel(CHANNEL_ID)
        if not channel: return
            
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(RSS_URL) as response:
                    if response.status == 200:
                        content = await response.text()
                        feed = feedparser.parse(content)
                    else: return

            if not feed.bozo:
                new_articles = 0
                for entry in reversed(feed.entries):
                    if entry.link not in self.seen_articles:
                        if not self.first_check:
                            await self.send_article_to_discord(channel, entry)
                            new_articles += 1
                        self.seen_articles.add(entry.link)
                if self.first_check: self.first_check = False
        except Exception as e:
            print(f"❌ (RSS) Erreur : {e}")

    async def send_article_to_discord(self, channel, entry):
        try:
            embed = discord.Embed(title=entry.title[:256], url=entry.link, color=0xe8874f, timestamp=datetime.now(timezone.utc))
            if hasattr(entry, 'summary'):
                description = re.sub('<[^<]+?>', '', entry.summary)
                embed.description = description[:2045] + "..." if len(description) > 2048 else description
            if 'media_content' in entry and entry.media_content:
                embed.set_image(url=entry.media_content[0]['url'])
            embed.set_author(name="📰 Handnews.fr", icon_url="https://handnews.fr/favicon.ico")
            await channel.send(embed=embed)
        except Exception: pass

    # --- PARTIE 2 : SCRAPING ET LOGIQUE HYBRIDE ---

    async def scrape_livescore_matches(self, url, competition_name, teams_filter=None):
        """Récupère les matchs via Browserless avec filtrage optionnel."""
        if not BROWSERLESS_API_TOKEN: return []
        matches = []
        paris_tz = pytz.timezone('Europe/Paris')
        payload = {"url": url}

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(BROWSERLESS_CONTENT_API_URL, json=payload, timeout=45) as response:
                    if response.status != 200: return matches
                    html = await response.text()
            
            soup = BeautifulSoup(html, 'html.parser')
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
                    if match_date < now.date(): match_date = match_date.replace(year=year + 1)
                        
                    match_datetime_naive = datetime.combine(match_date, datetime.min.time()).replace(hour=hour, minute=minute)
                    match_datetime_utc = match_datetime_naive.replace(tzinfo=timezone.utc)
                    
                    team1 = container.find(class_="event__participant--home").get_text(strip=True)
                    team2 = container.find(class_="event__participant--away").get_text(strip=True)
                    
                    if teams_filter:
                        # On vérifie si team1 OU team2 contient l'un des mots-clés du filtre
                        # On utilise 'in' pour que "Paris" marche avec "Paris SG"
                        match_found = False
                        for filter_name in teams_filter:
                            if filter_name.lower() in team1.lower() or filter_name.lower() in team2.lower():
                                match_found = True
                                break
                        
                        if not match_found:
                            # Si aucune équipe ne correspond, on ignore ce match
                            continue         
                            
                    match_id_raw = container.get('id', '')
                    match_id = match_id_raw.replace('g_7_', '')

                    if not all([team1, team2, match_id]): continue

                    limit_date_utc = datetime.now(timezone.utc) + timedelta(days=5)
                    if now.astimezone(timezone.utc) < match_datetime_utc < limit_date_utc:
                        matches.append({
                            "team1": team1, "team2": team2,
                            "start_time_utc": match_datetime_utc,
                            "event_id": match_id,
                            "competition": competition_name
                        })
                except Exception: continue
            return matches
        except Exception as e:
            print(f"❌ (SCRAPING) Erreur {competition_name}: {e}")
            return []

    async def send_match_to_slack_list(self, match_data):
        """Envoie une 'Carte Match' dans le canal Slack via Webhook (Block Kit)."""
        if not SLACK_WEBHOOK_URL: return

        # Formatage Date Paris
        paris_tz = pytz.timezone('Europe/Paris')
        local_time = match_data['start_time_utc'].astimezone(paris_tz)
        
        # --- AJOUT : Traduction manuelle du jour en Français ---
        jours_fr = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]
        nom_jour = jours_fr[local_time.weekday()] # 0 = Lundi, 6 = Dimanche
        
        date_str = local_time.strftime("%d/%m")
        heure_str = local_time.strftime("%H:%M")
        
        # Construction de la date complète (ex: Samedi 25/01)
        date_complete = f"{nom_jour} {date_str}"
        
        # Design du message Slack
        payload = {
            "blocks": [
                {
                    "type": "header",
                    "text": {
                        "type": "plain_text",
                        "text": f"🏐 {match_data['team1']} vs {match_data['team2']}",
                        "emoji": True
                    }
                },
                {
                    "type": "section",
                    "fields": [
                        {"type": "mrkdwn", "text": f"*Compétition :*\n{match_data['competition']}"},
                        # MODIFICATION ICI : On utilise date_complete
                        {"type": "mrkdwn", "text": f"*Date :*\n{date_complete} à {heure_str}"}
                    ]
                },
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": "Qui prend le match ? Réagissez avec ✅ ou répondez en thread 🧵"}
                },
                {"type": "divider"}
            ]
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(SLACK_WEBHOOK_URL, json=payload) as response:
                    if response.status == 200:
                        print(f"✅ (SLACK) Carte envoyée : {match_data['team1']} vs {match_data['team2']}")
                    else:
                        print(f"⚠️ (SLACK) Erreur {response.status}: {await response.text()}")
        except Exception as e:
            print(f"❌ (SLACK) Erreur d'envoi : {e}")

    @tasks.loop(seconds=MATCH_CHECK_INTERVAL)
    async def check_matches_loop(self):
        """Boucle principale : Scrape tout -> Envoie Slack -> Filtre Discord -> Sauvegarde DB."""
        guild = self.bot.get_guild(self.bot.guild_id)
        if not guild: return
        
        print(f"🔍 (MATCHES) Vérification des nouveaux matchs...")
        
        try:
            all_scraped_matches = []
            
            # 1. Scraping de TOUTES les compétitions
            for comp in self.COMPETITIONS:
                # On récupère le filtre s'il existe (sinon None)
                t_filter = comp.get("teams_filter")
                
                # On passe le filtre à la fonction
                matches = await self.scrape_livescore_matches(comp["url"], comp["name"], teams_filter=t_filter)
                
                all_scraped_matches.extend(matches)
                await asyncio.sleep(2)
            
            if not all_scraped_matches:
                print("ℹ️ (MATCHES) Aucun match trouvé.")
                return
            
            pronos_cog = self.bot.get_cog('PronosticsCog')
            new_matches_for_pronos = []
            
            for match in all_scraped_matches:
                # Vérification Base de Données
                existing_match = database.get_match_by_event_id(match['event_id'])
                
                # SI LE MATCH EST NOUVEAU
                if not existing_match:
                    print(f"✨ (NOUVEAU) {match['competition']} : {match['team1']} vs {match['team2']}")

                    # --- A. SLACK (Tout le monde) ---
                    await self.send_match_to_slack_list(match)

                    # --- B. DISCORD (Filtrage) ---
                    discord_event_id = None
                    # On vérifie si la compétition est dans la liste "VIP" pour Discord
                    is_discord_eligible = match['competition'] in self.DISCORD_COMPETITIONS_NAMES
                    
                    if is_discord_eligible:
                        try:
                            event = await guild.create_scheduled_event(
                                name=f"[{match['competition']}] {match['team1']} vs {match['team2']}",
                                description=f"Match de {match['competition']}\n📊 Faites vos pronos !",
                                start_time=match['start_time_utc'],
                                end_time=match['start_time_utc'] + timedelta(hours=2),
                                entity_type=discord.EntityType.external,
                                location=f"{match['competition']} Handball",
                                privacy_level=discord.PrivacyLevel.guild_only
                            )
                            discord_event_id = event.id
                            print(f"✅ (DISCORD) Event créé.")
                        except Exception as ex:
                            print(f"❌ (DISCORD) Erreur création event : {ex}")

                    # --- C. DATABASE (Tout le monde) ---
                    try:
                        match_id = database.create_match(
                            None, # Pas de journee_id obligatoire
                            match['event_id'], 
                            discord_event_id, # Sera None si pas sur Discord
                            match['team1'], 
                            match['team2'], 
                            match['start_time_utc'],
                            competition=match['competition']
                        )
                        
                        # --- D. PRONOS (Seulement Discord) ---
                        if is_discord_eligible and discord_event_id and match_id:
                             new_matches_for_pronos.append({
                                'id': match_id, 'equipe1': match['team1'], 'equipe2': match['team2'],
                                'date_match': match['start_time_utc']
                            })
                            
                    except Exception as db_ex:
                        print(f"❌ (DB) Erreur sauvegarde : {db_ex}")

            # Envoi des pronos
            if new_matches_for_pronos and pronos_cog:
                await pronos_cog.create_pronostic_messages_for_matches(new_matches_for_pronos)
            
        except Exception as e:
            print(f"❌ (MATCHES) Erreur critique boucle: {e}")

    # --- PARTIE 3 : RÉSULTATS (Avec sécurité pour Slack-Only) ---

    async def get_match_result(self, event_id):
        """Récupère le résultat final d'un match."""
        match_url = f"https://www.livescore.in/fr/match/{event_id}/"
        payload = {"url": match_url}
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(BROWSERLESS_CONTENT_API_URL, json=payload, timeout=30) as response:
                    if response.status != 200: return None
                    html = await response.text()
            
            soup = BeautifulSoup(html, 'html.parser')
            status_elem = soup.find('span', class_='fixedHeaderDuel__detailStatus')
            if not status_elem or "Terminé" not in status_elem.get_text(): return None
            
            score_wrapper = soup.find('div', class_='detailScore__wrapper')
            if not score_wrapper: return None

            scores = [span.get_text(strip=True) for span in score_wrapper.find_all('span') if span.get_text(strip=True).isdigit()]
            if len(scores) < 2: return None

            return f"{scores[0]}-{scores[1]}"
        except Exception: return None

    async def update_discord_event_with_result(self, discord_event_id, team1, team2, score):
        """Met à jour l'event Discord (si existant)."""
        # SÉCURITÉ : Si c'est un match Slack uniquement, discord_event_id est None
        if not discord_event_id: 
            return

        try:
            guild = self.bot.get_guild(self.bot.guild_id)
            event = guild.get_scheduled_event(int(discord_event_id))
            if not event: return

            new_description = f"🏐 RÉSULTAT FINAL : {score}\n\n{event.description}"
            new_name = f"[TERMINÉ] {score} - {event.name}"
            if len(new_name) > 100: new_name = new_name[:97] + "..."

            if event.status == discord.EventStatus.scheduled:
                await event.edit(name=new_name, description=new_description)
            elif event.status in [discord.EventStatus.active, discord.EventStatus.completed]:
                await event.edit(description=new_description)
        except Exception: pass

    async def _internal_check_and_process_results(self):
        """Logique de vérification des résultats."""
        print("🔍 (RESULTS) Vérification des résultats...")
        now_utc = datetime.now(timezone.utc)
        matches_to_check = database.get_matches_to_check_results(now_utc - timedelta(days=10))
        
        if not matches_to_check: return 0

        pronos_cog = self.bot.get_cog('PronosticsCog')
        processed_count = 0
        
        for match_row in matches_to_check:
            match = dict(match_row)
            try:
                result = await self.get_match_result(match['event_id'])
                if result:
                    print(f"✅ Résultat trouvé : {match['equipe1']} {result} {match['equipe2']}")
                    
                    # 1. Update DB (Pour tout le monde)
                    database.update_match_result(match['id'], result, result) # on passe result comme score
                    
                    # 2. Pronos (Si match Discord)
                    if pronos_cog and match['discord_event_id']:
                         pronos_cog.process_match_result(match['id'], result)
                    
                    # 3. Event Discord (Si match Discord)
                    await self.update_discord_event_with_result(
                        match['discord_event_id'], match['equipe1'], match['equipe2'], result
                    )
                    
                    processed_count += 1
                    await asyncio.sleep(5) 
            except Exception: pass
        
        return processed_count

    @tasks.loop(seconds=RESULTS_CHECK_INTERVAL)
    async def check_results_loop(self):
        """Vérifie les résultats pendant les heures de match."""
        paris_tz = pytz.timezone('Europe/Paris')
        now_paris = datetime.now(paris_tz)
        if 16 <= now_paris.hour < 24:
            await self._internal_check_and_process_results()

    @check_rss_loop.before_loop
    @check_matches_loop.before_loop
    @check_results_loop.before_loop
    async def before_loops(self):
        await self.bot.wait_until_ready()

    # --- COMMANDES ADMIN ---

    @commands.command(name='forcesync', aliases=['fs'])
    @commands.has_permissions(administrator=True)
    async def force_sync_command(self, ctx):
        await ctx.send("🔄 Synchronisation forcée (Slack + Discord)...", ephemeral=True)
        await self.check_matches_loop.coro(self)
        await ctx.send("✅ Terminé!", ephemeral=True)

    @commands.command(name='checkresults', aliases=['cr'])
    @commands.has_permissions(administrator=True)
    async def check_results_command(self, ctx):
        await ctx.send("🔄 Vérification résultats...", ephemeral=True)
        count = await self._internal_check_and_process_results()
        await ctx.send(f"✅ {count} résultats traités.", ephemeral=True)

async def setup(bot):
    await bot.add_cog(EventsCog(bot))
