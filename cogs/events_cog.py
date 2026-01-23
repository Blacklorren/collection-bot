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

# --- CORRECTION : Variable remise pour éviter l'erreur dans test_cog ---
LIVESCORE_URL = "https://www.livescore.in/fr/handball/france/starligue/"

# --- Configuration ---
RSS_URL = "https://handnews.fr/feed"
CHANNEL_ID = int(os.getenv('CHANNEL_ID')) if os.getenv('CHANNEL_ID') else None
CHECK_INTERVAL = 1800 

# --- CONFIGURATION SLACK ---
SLACK_WEBHOOK_URL = os.getenv('SLACK_WEBHOOK_URL')

# --- FRÉQUENCES ---
MATCH_CHECK_INTERVAL = 86400 # 24 heures
RESULTS_CHECK_INTERVAL = 7200 # 2 heures

# --- Browserless ---
BROWSERLESS_API_TOKEN = os.getenv('BROWSERLESS_API_TOKEN')
BROWSERLESS_CONTENT_API_URL = f"https://production-sfo.browserless.io/content?token={BROWSERLESS_API_TOKEN}"

class EventsCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.seen_articles = set()
        self.first_check = True
        
        # 1. LISTE COMPLÈTE
        self.COMPETITIONS = [
            {"name": "Starligue", "url": "https://www.livescore.in/fr/handball/france/starligue/"},
            {"name": "Euro", "url": "https://www.livescore.in/fr/handball/europe/ehf-euro/"},
            {"name": "LFH", "url": "https://www.livescore.in/fr/handball/france/division-1-femmes/"},
            {"name": "Euro Féminin", "url": "https://www.livescore.in/fr/handball/europe/ehf-euro-women/", "teams_filter": ["france"]},
            {"name": "Mondial Masculin", "url": "https://www.livescore.in/fr/handball/monde/championnat-du-monde/", "teams_filter": ["france"]},
            {"name": "Mondial Féminin", "url": "https://www.livescore.in/fr/handball/europe/ehf-euro/", "teams_filter": ["france"]},
            {"name": "Ligue des Champions Masculine", "url": "https://www.livescore.in/fr/handball/europe/ligue-des-champions", "teams_filter": ["paris", "nantes"]},
            {"name": "Ligue des Champions Féminine", "url": "https://www.livescore.in/fr/handball/europe/ligue-des-champions-femmes/", "teams_filter": ["metz", "brest-bretagne"]},
            {"name": "Ligue Européene Masculine", "url": "https://www.livescore.in/fr/handball/europe/ligue-europeenne/", "teams_filter": ["montpellier"]},
            {"name": "Ligue Européenne Féminine", "url": "https://www.livescore.in/fr/handball/europe/ligue-europeenne-femmes/", "teams_filter": ["chambray-touraine", "dijon"]}
        ]
        
        # 2. FILTRE DISCORD
        self.DISCORD_COMPETITIONS_NAMES = ["Starligue", "Euro"]
        
        if not BROWSERLESS_API_TOKEN: print("❌ Token Browserless manquant")
        if not SLACK_WEBHOOK_URL: print("⚠️ Webhook Slack manquant")

        self.check_rss_loop.start()
        self.check_matches_loop.start()
        self.check_results_loop.start()
        
    def cog_unload(self):
        self.check_rss_loop.cancel()
        self.check_matches_loop.cancel()
        self.check_results_loop.cancel()

    # --- RSS ---
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
                for entry in reversed(feed.entries):
                    if entry.link not in self.seen_articles:
                        if not self.first_check: await self.send_article_to_discord(channel, entry)
                        self.seen_articles.add(entry.link)
                if self.first_check: self.first_check = False
        except Exception: pass

    async def send_article_to_discord(self, channel, entry):
        try:
            embed = discord.Embed(title=entry.title[:256], url=entry.link, color=0xe8874f, timestamp=datetime.now(timezone.utc))
            if hasattr(entry, 'summary'):
                desc = re.sub('<[^<]+?>', '', entry.summary)
                embed.description = desc[:2045] + "..." if len(desc) > 2048 else desc
            if 'media_content' in entry and entry.media_content:
                embed.set_image(url=entry.media_content[0]['url'])
            embed.set_author(name="📰 Handnews.fr", icon_url="https://handnews.fr/favicon.ico")
            await channel.send(embed=embed)
        except Exception: pass

    # --- SCRAPING ---

    async def scrape_livescore_matches(self, url, competition_name, teams_filter=None):
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
                        match_found = False
                        for filter_name in teams_filter:
                            if filter_name.lower() in team1.lower() or filter_name.lower() in team2.lower():
                                match_found = True
                                break
                        if not match_found: continue         
                            
                    match_id_raw = container.get('id', '')
                    match_id = match_id_raw.replace('g_7_', '')

                    if not all([team1, team2, match_id]): continue

                    limit_date_utc = datetime.now(timezone.utc) + timedelta(days=7)
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
        if not SLACK_WEBHOOK_URL: return
        paris_tz = pytz.timezone('Europe/Paris')
        local_time = match_data['start_time_utc'].astimezone(paris_tz)
        
        jours_fr = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]
        nom_jour = jours_fr[local_time.weekday()]
        
        date_str = local_time.strftime("%d/%m")
        heure_str = local_time.strftime("%H:%M")
        date_complete = f"{nom_jour} {date_str}"
        
        payload = {
            "blocks": [
                {
                    "type": "header",
                    "text": {"type": "plain_text", "text": f"🏐 {match_data['team1']} vs {match_data['team2']}", "emoji": True}
                },
                {
                    "type": "section",
                    "fields": [
                        {"type": "mrkdwn", "text": f"*Compétition :*\n{match_data['competition']}"},
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
                    if response.status != 200: print(f"⚠️ (SLACK) Erreur {response.status}")
        except Exception: pass

    # --- GESTION MISE À JOUR DISCORD ---
    async def update_discord_event_time(self, guild, discord_event_id, new_start_time):
        """Met à jour l'horaire d'un événement Discord existant."""
        try:
            event = guild.get_scheduled_event(int(discord_event_id))
            if event:
                await event.edit(
                    start_time=new_start_time,
                    end_time=new_start_time + timedelta(hours=2)
                )
                print(f"⏰ (DISCORD) Horaire mis à jour pour l'événement {event.name}")
        except Exception as e:
            print(f"❌ (DISCORD) Erreur mise à jour horaire : {e}")

    @tasks.loop(seconds=MATCH_CHECK_INTERVAL)
    async def check_matches_loop(self):
        guild = self.bot.get_guild(self.bot.guild_id)
        if not guild: return
        
        print(f"🔍 (MATCHES) Vérification (Mises à jour + Nouveautés)...")
        
        try:
            all_scraped_matches = []
            for comp in self.COMPETITIONS:
                t_filter = comp.get("teams_filter")
                matches = await self.scrape_livescore_matches(comp["url"], comp["name"], teams_filter=t_filter)
                all_scraped_matches.extend(matches)
                await asyncio.sleep(2)
            
            if not all_scraped_matches: return
            
            pronos_cog = self.bot.get_cog('PronosticsCog')
            new_matches_for_pronos = []
            discord_deadline = datetime.now(timezone.utc) + timedelta(days=5)
            
            for match in all_scraped_matches:
                existing_match = database.get_match_by_event_id(match['event_id'])
                
                # --- CAS 1 : NOUVEAU MATCH ---
                if not existing_match:
                    print(f"✨ (NOUVEAU) {match['competition']} : {match['team1']} vs {match['team2']}")
                    await self.send_match_to_slack_list(match)
                    
                    # (Logique de création existante...)
                    discord_event_id = None
                    should_create_discord = False
                    if match['competition'] in self.DISCORD_COMPETITIONS_NAMES:
                        if match['start_time_utc'] <= discord_deadline:
                            should_create_discord = True
                    
                    if should_create_discord:
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
                            print(f"✅ (DISCORD) Event créé (J-5).")
                        except Exception as ex: print(f"❌ (DISCORD) Création : {ex}")

                    match_db_id = database.create_match(
                        None, match['event_id'], discord_event_id,
                        match['team1'], match['team2'], match['start_time_utc'],
                        competition=match['competition']
                    )
                    if discord_event_id and match_db_id:
                        new_matches_for_pronos.append({
                            'id': match_db_id, 'equipe1': match['team1'], 'equipe2': match['team2'],
                            'date_match': match['start_time_utc']
                        })

                # --- CAS 2 : MATCH EXISTANT -> VÉRIFICATION HORAIRE ET J+5 ---
                else:
                    # A. Vérification Changement d'Horaire
                    try:
                        # On récupère l'heure en base
                        db_date = datetime.fromisoformat(existing_match['date_match'])
                        if db_date.tzinfo is None: db_date = db_date.replace(tzinfo=timezone.utc)
                        
                        # Comparaison (avec tolérance de 60s)
                        time_diff = abs((db_date - match['start_time_utc']).total_seconds())
                        
                        if time_diff > 300: # Plus de 5 minutes de différence
                            print(f"⚠️ (UPDATE) Changement horaire détecté pour {match['team1']} vs {match['team2']}")
                            
                            # 1. Update DB
                            database.update_match_time(existing_match['id'], match['start_time_utc'])
                            
                            # 2. Update Discord Event (si existe)
                            if existing_match['discord_event_id']:
                                await self.update_discord_event_time(guild, existing_match['discord_event_id'], match['start_time_utc'])
                                
                    except Exception as e:
                        print(f"❌ Erreur check horaire: {e}")

                    # B. Logique "Late Activation" pour Discord (J+5)
                    # Si le match n'a PAS d'event Discord, mais qu'il vient d'entrer dans la zone J+5
                    if not existing_match['discord_event_id'] and match['competition'] in self.DISCORD_COMPETITIONS_NAMES:
                        if match['start_time_utc'] <= discord_deadline:
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
                                database.update_match_discord_event_id(existing_match['id'], event.id)
                                new_matches_for_pronos.append({
                                    'id': existing_match['id'], 'equipe1': match['team1'], 'equipe2': match['team2'],
                                    'date_match': match['start_time_utc']
                                })
                                print(f"✅ (DISCORD) Event créé tardivement (J-5 atteint).")
                            except Exception as ex: print(f"❌ (DISCORD) Création tardive : {ex}")

            if new_matches_for_pronos and pronos_cog:
                await pronos_cog.create_pronostic_messages_for_matches(new_matches_for_pronos)
            
        except Exception as e:
            print(f"❌ (MATCHES) Erreur boucle : {e}")

    # --- RESULTATS ---
    async def get_match_result(self, event_id):
        match_url = f"https://www.livescore.in/fr/match/{event_id}/"
        payload = {"url": match_url}
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(BROWSERLESS_CONTENT_API_URL, json=payload, timeout=30) as resp:
                    if resp.status != 200: return None
                    html = await resp.text()
            soup = BeautifulSoup(html, 'html.parser')
            status = soup.find('span', class_='fixedHeaderDuel__detailStatus')
            if not status or "Terminé" not in status.get_text(): return None
            wrapper = soup.find('div', class_='detailScore__wrapper')
            if not wrapper: return None
            scores = [s.get_text(strip=True) for s in wrapper.find_all('span') if s.get_text(strip=True).isdigit()]
            if len(scores) < 2: return None
            return f"{scores[0]}-{scores[1]}"
        except Exception: return None

    async def update_discord_event_with_result(self, discord_event_id, team1, team2, score):
        if not discord_event_id: return
        try:
            guild = self.bot.get_guild(self.bot.guild_id)
            event = guild.get_scheduled_event(int(discord_event_id))
            if event:
                new_desc = f"🏐 RÉSULTAT FINAL : {score}\n\n{event.description}"
                new_name = f"[TERMINÉ] {score} - {event.name}"
                if len(new_name) > 100: new_name = new_name[:97] + "..."
                if event.status == discord.EventStatus.scheduled: await event.edit(name=new_name, description=new_desc)
                elif event.status in [discord.EventStatus.active, discord.EventStatus.completed]: await event.edit(description=new_desc)
        except Exception: pass

    async def _internal_check_and_process_results(self):
        now_utc = datetime.now(timezone.utc)
        matches = database.get_matches_to_check_results(now_utc - timedelta(days=10))
        if not matches: return 0
        pronos_cog = self.bot.get_cog('PronosticsCog')
        count = 0
        for m in matches:
            match = dict(m)
            try:
                res = await self.get_match_result(match['event_id'])
                if res:
                    database.update_match_result(match['id'], res, res)
                    if pronos_cog and match['discord_event_id']: pronos_cog.process_match_result(match['id'], res)
                    await self.update_discord_event_with_result(match['discord_event_id'], match['equipe1'], match['equipe2'], res)
                    count += 1
                    await asyncio.sleep(5)
            except Exception: pass
        return count

    @tasks.loop(seconds=RESULTS_CHECK_INTERVAL)
    async def check_results_loop(self):
        paris = pytz.timezone('Europe/Paris')
        if 16 <= datetime.now(paris).hour < 24: await self._internal_check_and_process_results()

    @check_rss_loop.before_loop
    @check_matches_loop.before_loop
    @check_results_loop.before_loop
    async def before_loops(self): await self.bot.wait_until_ready()

    @commands.command(name='forcesync', aliases=['fs'])
    @commands.has_permissions(administrator=True)
    async def force_sync_command(self, ctx):
        await ctx.send("🔄 Synchro...", ephemeral=True)
        await self.check_matches_loop.coro(self)
        await ctx.send("✅ Fait.", ephemeral=True)

    @commands.command(name='checkresults', aliases=['cr'])
    @commands.has_permissions(administrator=True)
    async def check_results_command(self, ctx):
        await ctx.send("🔄 Résultats...", ephemeral=True)
        c = await self._internal_check_and_process_results()
        await ctx.send(f"✅ {c} traités.", ephemeral=True)

async def setup(bot):
    await bot.add_cog(EventsCog(bot))
