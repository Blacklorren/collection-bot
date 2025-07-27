import discord
from discord.ext import commands
import asyncio
import json
from datetime import datetime, timedelta, timezone, date
import pytz
import database
import aiohttp
import re
import os
from bs4 import BeautifulSoup
import feedparser

# On importe les constantes depuis le cog d'événements pour rester synchronisé
from cogs.events_cog import LIVESCORE_URL, RSS_URL, BROWSERLESS_API_TOKEN, BROWSERLESS_CONTENT_API_URL
# On importe les emojis depuis le cog de pronostics pour être cohérent
from cogs.pronostics_cog import PRONO_CHANNEL_ID, PRONO_EMOJIS

class TestCog(commands.Cog):
    """Cog pour tester toutes les fonctionnalités du bot."""
    
    def __init__(self, bot):
        self.bot = bot
        self.test_results = []
        self.test_messages = []
        
    def log_test(self, test_name, success, message=""):
        emoji = "✅" if success else "❌"
        if len(message) > 150: message = message[:147] + "..."
        result = f"{emoji} **{test_name}**: {message}"
        self.test_results.append(result)
        
    async def clean_test_messages(self):
        for msg in self.test_messages:
            try:
                await msg.delete()
                await asyncio.sleep(0.5)
            except (discord.NotFound, discord.Forbidden): pass
        self.test_messages.clear()

    @commands.group(name='test', invoke_without_command=True)
    @commands.has_permissions(administrator=True)
    async def test_group(self, ctx):
        embed = discord.Embed(title="🧪 Suite de Tests du Bot Handnews", description="Utilisez les sous-commandes.", color=discord.Color.dark_blue())
        tests = [
            ("`!test all`", "Lance l'ensemble des tests."),
            ("`!test permissions`", "Vérifie les permissions critiques."),
            ("`!test scraping`", "Teste le scraping et affiche les matchs trouvés."),
            ("`!test pronos`", "Lance un test de pronostic interactif et fonctionnel dans le bon salon (30s)."),
            ("`!test savehtml`", "Sauvegarde le HTML reçu de Browserless."),
            ("`!test db`", "Vérifie la connexion et les opérations de base."),
            ("`!test collection`", "Teste le chargement des cartes."),
            ("`!test events`", "Teste la création d'événements Discord."),
            ("`!test rss`", "Teste la lecture du flux RSS."),
            ("`!test clean`", "Nettoie tous les messages générés par les tests.")
        ]
        for cmd, desc in tests:
            embed.add_field(name=cmd, value=desc, inline=False)
        await ctx.send(embed=embed)

    # --- TEST DE PRONOSTIC D'INTÉGRATION COMPLET ---
    @test_group.command(name='pronos')
    async def test_pronostics_integration(self, ctx):
        """Scrape le prochain match réel et lance un test fonctionnel dans le salon de pronostics."""
        
        # --- VÉRIFICATION PRÉALABLE ---
        if not PRONO_CHANNEL_ID:
            await ctx.send("❌ **Test annulé :** `PRONO_CHANNEL_ID` n'est pas configuré dans `pronostics_cog.py`.", ephemeral=True)
            return
            
        prono_channel = self.bot.get_channel(PRONO_CHANNEL_ID)
        if not prono_channel:
            await ctx.send(f"❌ **Test annulé :** Impossible de trouver le salon de pronostics (ID: {PRONO_CHANNEL_ID}).", ephemeral=True)
            return

        start_msg = await ctx.send(f"🎯 **Lancement du test d'intégration...** Le test va se dérouler dans le salon {prono_channel.mention}.")
        
        # --- ÉTAPE 1: SCRAPING ---
        parsed_matches = []
        try:
            if not BROWSERLESS_API_TOKEN:
                await start_msg.edit(content="❌ **Erreur de configuration :** Token Browserless manquant."); return
            
            payload = {"url": LIVESCORE_URL}; 
            async with aiohttp.ClientSession() as session:
                async with session.post(BROWSERLESS_CONTENT_API_URL, json=payload, timeout=45) as response:
                    if response.status != 200:
                        await start_msg.edit(content=f"❌ **Erreur Scraping :** L'API a retourné le status {response.status}."); return
                    html = await response.text()
            
            soup = BeautifulSoup(html, 'html.parser'); paris_tz = pytz.timezone('Europe/Paris'); now_paris = datetime.now(paris_tz)
            for container in soup.select("div.event__match--scheduled"):
                try:
                    time_elem = container.find(class_="event__time"); time_text = time_elem.get_text(strip=True); date_part, time_part = time_text.split(' ')
                    day, month = map(int, date_part.split('.')[:2]); hour, minute = map(int, time_part.split(':'))
                    year = now_paris.year; match_date = date(year, month, day)
                    if match_date < now_paris.date(): match_date = match_date.replace(year=year + 1)
                    dt = datetime.combine(match_date, datetime.min.time()).replace(hour=hour, minute=minute)
                    team1 = container.find(class_="event__participant--home").get_text(strip=True); team2 = container.find(class_="event__participant--away").get_text(strip=True)
                    parsed_matches.append({"team1": team1, "team2": team2, "datetime_paris": paris_tz.localize(dt)})
                except: continue
        except Exception as e:
            await start_msg.edit(content=f"❌ **Erreur durant le scraping :** `{e}`"); return

        if not parsed_matches:
            await start_msg.edit(content="⚠️ **Test annulé :** Aucun match programmé n'a été trouvé sur Livescore pour servir de test."); return
        
        target_match = parsed_matches[0]
        team1, team2, match_date_utc = target_match['team1'], target_match['team2'], target_match['datetime_paris'].astimezone(timezone.utc)
        
        # --- ÉTAPE 2: CRÉATION DU TEST DANS LE BON SALON ---
        test_message = None; match_id = None; fake_event_id = f"test_{int(datetime.now().timestamp())}"
        try:
            with database.sqlite3.connect(database.DB_NAME) as con:
                cur = con.cursor(); cur.execute("INSERT INTO matchs (journee_id, event_id, equipe1, equipe2, date_match) VALUES (?, ?, ?, ?, ?)", (999, fake_event_id, team1, team2, match_date_utc.isoformat())); match_id = cur.lastrowid; con.commit()

            if not match_id:
                await start_msg.edit(content="❌ **Erreur DB :** Impossible de créer le match de test."); return

            embed = discord.Embed(title=f"⚔️ [TEST D'INTÉGRATION] {team1} vs {team2}", description="**Réagissez ci-dessous.** Le bot doit gérer vos réactions. Ce test s'arrêtera dans 30 secondes.", color=discord.Color.yellow()).set_footer(text=f"ID de test : {match_id}")
            
            # On envoie le message dans le canal de pronostics
            test_message = await prono_channel.send(embed=embed)
            
            database.save_prono_message(match_id, test_message.id, prono_channel.id)
            for emoji in PRONO_EMOJIS:
                await test_message.add_reaction(emoji)
            
            await asyncio.sleep(30)
        except Exception as e:
            await ctx.send(f"❌ **Une erreur est survenue pendant le test :** `{e}`")
        finally:
            # --- ÉTAPE 3: NETTOYAGE ---
            await prono_channel.send("🧹 **Fin du test, nettoyage en cours...**", delete_after=5)
            if match_id:
                with database.sqlite3.connect(database.DB_NAME) as con:
                    cur = con.cursor(); cur.execute("DELETE FROM prono_messages WHERE match_id = ?", (match_id,)); cur.execute("DELETE FROM pronostics WHERE match_id = ?", (match_id,)); cur.execute("DELETE FROM matchs WHERE id = ?", (match_id,)); con.commit()
            if test_message:
                try: await test_message.delete()
                except discord.NotFound: pass
            print(f"✅ (TEST) Nettoyage du match d'intégration {match_id} terminé.")
    
    # --- LES AUTRES COMMANDES RESTENT INCHANGÉES ---
    
    @test_group.command(name='savehtml')
    async def save_html_command(self, ctx):
        await ctx.send(f"📄 **Récupération du HTML depuis `{LIVESCORE_URL}`...**")
        if not BROWSERLESS_API_TOKEN:
            await ctx.send("❌ **Échec config :** Token Browserless manquant."); return
        payload = {"url": LIVESCORE_URL}
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(BROWSERLESS_CONTENT_API_URL, json=payload, timeout=45) as response:
                    if response.status != 200:
                        await ctx.send(f"❌ **Échec de l'API (Status: {response.status})**"); return
                    html = await response.text()
            filename = "debug_page.html"
            with open(filename, "w", encoding="utf-8") as f: f.write(html)
            await ctx.send(content="✅ **Voici le fichier HTML brut reçu.**", file=discord.File(filename))
            os.remove(filename)
        except Exception as e:
            await ctx.send(f"❌ **Erreur :** `{type(e).__name__}: {e}`")

    @test_group.command(name='all')
    async def test_all(self, ctx):
        self.test_results = []; await self.clean_test_messages()
        msg = await ctx.send("🧪 **Lancement de la suite de tests complète...**"); self.test_messages.append(msg)
        await self.test_permissions(ctx, silent=True)
        await self.test_scraping(ctx, silent=True)
        await self.test_db(ctx, silent=True)
        await self.test_collection(ctx, silent=True)
        await self.test_pronostics_integration(ctx) # On pourrait utiliser celui-ci, mais il est interactif
        await self.test_events(ctx, silent=True)
        await self.test_rss(ctx, silent=True)
        await self.send_test_summary(ctx)

    @test_group.command(name='permissions')
    async def test_permissions(self, ctx, silent=False):
        if not silent: msg = await ctx.send("🔐 **Test des permissions...**"); self.test_messages.append(msg)
        embed = discord.Embed(title="🔐 Test des Permissions", description=f"Vérification dans `{ctx.channel.name}`", color=discord.Color.blue())
        p = ctx.channel.permissions_for(ctx.guild.me)
        perms = {"Envoyer des messages": p.send_messages, "Intégrer des liens": p.embed_links, "Ajouter des réactions": p.add_reactions, "Lire l'historique": p.read_message_history, "Gérer les événements": p.manage_events, "Gérer les messages": p.manage_messages}
        all_ok = all(perms.values())
        field = "\n".join([f"{'✅' if has else '❌'} {name}" for name, has in perms.items()])
        embed.add_field(name="Permissions Critiques", value=field); embed.color = discord.Color.green() if all_ok else discord.Color.red()
        if not silent: await ctx.send(embed=embed)
        self.log_test("Permissions", all_ok, "OK" if all_ok else "Manquantes")

    @test_group.command(name='scraping')
    async def test_scraping(self, ctx, silent=False):
        if not silent:
            msg = await ctx.send(f"🌐 **Test du scraping via Browserless sur `{LIVESCORE_URL}`...**"); self.test_messages.append(msg)
        if not BROWSERLESS_API_TOKEN:
            self.log_test("Configuration", False, "Token Browserless manquant."); await ctx.send("❌ **Échec config :** Token API introuvable."); return
        payload = {"url": LIVESCORE_URL}
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(BROWSERLESS_CONTENT_API_URL, json=payload, timeout=45) as response:
                    status = response.status; self.log_test("API Connexion", status == 200, f"Status: {status}")
                    if status != 200:
                        if not silent: await ctx.send(f"❌ **Échec connexion API (Status: {status})**"); return
                    html = await response.text()
            soup = BeautifulSoup(html, 'html.parser'); parsed_matches = []
            if soup.select("div.event__match--scheduled"):
                self.log_test("Analyse HTML", True, f"{len(soup.select('div.event__match--scheduled'))} matchs trouvés.")
                if not silent: await ctx.send(f"✅ **Scraping et analyse OK.** {len(soup.select('div.event__match--scheduled'))} matchs trouvés.")
            else:
                self.log_test("Analyse HTML", False, "Aucun match parsé.")
                if not silent: await ctx.send("⚠️ **Scraping réussi mais aucun match parsé.** La structure a sûrement changé.")
        except Exception as e:
            self.log_test("Scraping", False, f"{type(e).__name__}: {e}"); await ctx.send(f"❌ **Erreur scraping :** `{type(e).__name__}: {e}`")

    @test_group.command(name='db')
    async def test_db(self, ctx, silent=False):
        if not silent: msg = await ctx.send("🗄️ **Test DB...**"); self.test_messages.append(msg)
        try:
            uid = self.bot.user.id; database.check_user(uid)
            d_b = database.get_user_data(uid); self.log_test("DB: Lecture", d_b is not None)
            database.update_points(uid, 50); d_a = database.get_user_data(uid)
            s = d_a['points'] == d_b['points'] + 50; self.log_test("DB: Écriture", s)
            database.update_points(uid, -50)
            if not silent: await ctx.send("✅ **DB fonctionnelle**.")
        except Exception as e: self.log_test("DB", False, str(e)); await ctx.send(f"❌ **Erreur DB :** `{str(e)}`")

    @test_group.command(name='collection')
    async def test_collection(self, ctx, silent=False):
        if not silent: msg = await ctx.send("🎴 **Test collection...**"); self.test_messages.append(msg)
        try:
            with open('cards.json', 'r', encoding='utf-8') as f: data = json.load(f)
            if not isinstance(data, list) or not data: raise ValueError("JSON vide")
            self.log_test("cards.json", True, f"{len(data)} cartes.")
            card = data[0]; embed = discord.Embed(title=f"**{card['nom']}**", color=discord.Color.blue); embed.set_image(url=card['image_url'])
            if not silent: msg = await ctx.send(embed=embed); self.test_messages.append(msg)
            self.log_test("Affichage carte", True)
        except Exception as e: self.log_test("Collection", False, str(e)); await ctx.send(f"❌ **Erreur collection :** `{str(e)}`")

    @test_group.command(name='events')
    async def test_events(self, ctx, silent=False):
        if not silent: msg = await ctx.send("📅 **Test events...**"); self.test_messages.append(msg)
        try:
            st = datetime.now(timezone.utc) + timedelta(minutes=2)
            event = await ctx.guild.create_scheduled_event(name="[TEST] Event", start_time=st, end_time=st + timedelta(hours=1), location="Test")
            self.log_test("Création event", True)
            if not silent: msg = await ctx.send(f"✅ **Event créé.** Suppr. dans 15s."); self.test_messages.append(msg)
            await asyncio.sleep(15); await event.delete()
            if not silent: msg = await ctx.send("🗑️ Event test supprimé.", delete_after=10); self.test_messages.append(msg)
        except Exception as e: self.log_test("Création event", False, str(e)); await ctx.send(f"❌ **Erreur event :** `{str(e)}`")

    @test_group.command(name='rss')
    async def test_rss(self, ctx, silent=False):
        if not silent: msg = await ctx.send(f"📰 **Test RSS sur `{RSS_URL}`...**"); self.test_messages.append(msg)
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(RSS_URL, timeout=15) as resp:
                    if resp.status != 200: self.log_test("RSS", False, f"HTTP {resp.status}"); await ctx.send(f"❌ Erreur HTTP {resp.status}."); return
                    feed = feedparser.parse(await resp.text())
            if feed.bozo: self.log_test("RSS", False, "Flux invalide"); await ctx.send(f"❌ Flux invalide: `{feed.bozo_exception}`")
            else: self.log_test("RSS", True, f"{len(feed.entries)} articles."); await ctx.send(f"✅ RSS OK ({len(feed.entries)} articles).")
        except Exception as e: self.log_test("RSS", False, str(e)); await ctx.send(f"❌ **Erreur RSS :** `{str(e)}`")

    @test_group.command(name='clean')
    @commands.has_permissions(manage_messages=True)
    async def test_clean(self, ctx):
        count = len(self.test_messages)
        await self.clean_test_messages()
        await ctx.send(f"🧹 **{count} message(s) de test supprimé(s).**", delete_after=10, ephemeral=True)

    async def send_test_summary(self, ctx):
        if not self.test_results: return
        s_count = sum(1 for r in self.test_results if r.startswith("✅")); t_count = len(self.test_results)
        color = discord.Color.green() if s_count == t_count else discord.Color.red()
        embed = discord.Embed(title="📊 Résumé des Tests", description=f"**{s_count}/{t_count}** réussis.", color=color)
        txt = "\n".join(self.test_results)
        chunks = [txt[i:i + 1024] for i in range(0, len(txt), 1024)]
        for i, chunk in enumerate(chunks): embed.add_field(name=f"Détails ({i+1}/{len(chunks)})", value=chunk, inline=False)
        await ctx.send(embed=embed)

async def setup(bot):
    await bot.add_cog(TestCog(bot))
