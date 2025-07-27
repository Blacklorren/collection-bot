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

# On importe les constantes depuis le cog d'événements pour rester synchronisé
from cogs.events_cog import LIVESCORE_URL, RSS_URL, BROWSERLESS_API_TOKEN, BROWSERLESS_CONTENT_API_URL

class TestCog(commands.Cog):
    """Cog pour tester toutes les fonctionnalités du bot."""
    
    def __init__(self, bot):
        self.bot = bot
        self.test_results = []
        self.test_messages = []
        
    def log_test(self, test_name, success, message=""):
        """Enregistre le résultat d'un test."""
        emoji = "✅" if success else "❌"
        if len(message) > 150:
            message = message[:147] + "..."
        result = f"{emoji} **{test_name}**: {message}"
        self.test_results.append(result)
        
    async def clean_test_messages(self):
        """Nettoie tous les messages de test."""
        for msg in self.test_messages:
            try:
                await msg.delete()
                await asyncio.sleep(0.5)
            except (discord.NotFound, discord.Forbidden):
                pass
        self.test_messages.clear()

    @commands.group(name='test', invoke_without_command=True)
    @commands.has_permissions(administrator=True)
    async def test_group(self, ctx):
        """Groupe de commandes de test."""
        embed = discord.Embed(
            title="🧪 Suite de Tests du Bot Handnews",
            description="Utilisez les sous-commandes pour tester les différents modules du bot.",
            color=discord.Color.dark_blue()
        )
        
        tests = [
            ("`!test all`", "Lance l'ensemble des tests."),
            ("`!test permissions`", "Vérifie les permissions critiques du bot."),
            ("`!test scraping`", "Teste le scraping et affiche les matchs et leur HTML."),
            ("`!test db`", "Vérifie la connexion et les opérations de base."),
            ("`!test collection`", "Teste le chargement des cartes."),
            ("`!test pronostics`", "Teste la création d'un message de pronostic."),
            ("`!test events`", "Teste la création d'événements Discord."),
            ("`!test rss`", "Teste la lecture du flux RSS."),
            ("`!test clean`", "Nettoie tous les messages générés par les tests.")
        ]
        
        for cmd, desc in tests:
            embed.add_field(name=cmd, value=desc, inline=False)
            
        await ctx.send(embed=embed)

    @test_group.command(name='all')
    async def test_all(self, ctx):
        """Lance tous les tests disponibles."""
        self.test_results = []
        await self.clean_test_messages()
        start_message = await ctx.send("🧪 **Lancement de la suite de tests complète...**")
        self.test_messages.append(start_message)
        
        await self.test_permissions(ctx, silent=True)
        await self.test_scraping(ctx, silent=True)
        await self.test_db(ctx, silent=True)
        await self.test_collection(ctx, silent=True)
        await self.test_pronostics(ctx, silent=True)
        await self.test_events(ctx, silent=True)
        await self.test_rss(ctx, silent=True)
        
        await self.send_test_summary(ctx)

    @test_group.command(name='permissions')
    async def test_permissions(self, ctx, silent=False):
        """Vérifie toutes les permissions nécessaires."""
        if not silent:
            msg = await ctx.send("🔐 **Test des permissions du bot...**")
            self.test_messages.append(msg)
            
        embed = discord.Embed(title="🔐 Test des Permissions", description=f"Vérification dans `{ctx.channel.name}`", color=discord.Color.blue())
        permissions = ctx.channel.permissions_for(ctx.guild.me)
        required_perms = {
            "Envoyer des messages": permissions.send_messages, "Intégrer des liens": permissions.embed_links,
            "Ajouter des réactions": permissions.add_reactions, "Lire l'historique": permissions.read_message_history,
            "Gérer les événements": permissions.manage_events, "Gérer les messages": permissions.manage_messages,
        }
        all_ok = all(required_perms.values())
        field_value = "\n".join([f"{'✅' if has else '❌'} {name}" for name, has in required_perms.items()])
        embed.add_field(name="Permissions Critiques", value=field_value)
        embed.color = discord.Color.green() if all_ok else discord.Color.red()
        embed.set_footer(text="Toutes les permissions sont OK." if all_ok else "Des permissions manquent !")
        if not silent: await ctx.send(embed=embed)
        self.log_test("Permissions", all_ok, "Toutes OK" if all_ok else "Manquantes")

    @test_group.command(name='scraping')
    async def test_scraping(self, ctx, silent=False):
        """Teste le scraping, l'analyse, et affiche le HTML des matchs trouvés."""
        if not silent:
            msg = await ctx.send(f"🌐 **Test du scraping via Browserless sur `{LIVESCORE_URL}`...**")
            self.test_messages.append(msg)

        if not BROWSERLESS_API_TOKEN:
            self.log_test("Configuration Browserless", False, "Variable BROWSERLESS_API_TOKEN manquante.")
            if not silent: await ctx.send("❌ **Échec config :** Le token API de Browserless est introuvable.")
            return

        payload = {"url": LIVESCORE_URL}
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(BROWSERLESS_CONTENT_API_URL, json=payload, timeout=45) as response:
                    status = response.status
                    self.log_test("API Browserless Connexion", status == 200, f"Status: {status}")
                    if status != 200:
                        if not silent: await ctx.send(f"❌ **Échec connexion API (Status: {status})**")
                        return
                    html = await response.text()
            
            # --- Logique de parsing (réplique de events_cog) ---
            soup = BeautifulSoup(html, 'html.parser')
            parsed_matches = []
            match_containers_html = [] # Liste pour stocker les conteneurs HTML
            paris_tz = pytz.timezone('Europe/Paris')
            now_paris = datetime.now(paris_tz)
            day_containers = soup.select("div.sportName > div.event__round--static")

            for day_container in day_containers:
                date_text = day_container.get_text(strip=True).upper()
                match_date = None
                if "AUJOURD'HUI" in date_text: match_date = now_paris.date()
                elif "DEMAIN" in date_text: match_date = (now_paris + timedelta(days=1)).date()
                else:
                    day_month_search = re.search(r'(\d{2})\.(\d{2})\.', date_text)
                    if day_month_search:
                        day, month = map(int, day_month_search.groups())
                        year = now_paris.year
                        match_date = date(year, month, day)
                        if match_date < now_paris.date(): match_date = match_date.replace(year=year + 1)
                if not match_date: continue

                for match_container in day_container.find_next_siblings('div', class_=re.compile(r'event__match--scheduled')):
                    try:
                        team1 = match_container.find(class_=re.compile(r'participant--home')).get_text(strip=True)
                        team2 = match_container.find(class_=re.compile(r'participant--away')).get_text(strip=True)
                        time_text = match_container.find(class_=re.compile(r'event__time')).get_text(strip=True)
                        hour, minute = map(int, time_text.split(':'))
                        dt = datetime.combine(match_date, datetime.min.time()).replace(hour=hour, minute=minute)
                        
                        # On stocke les données parsées ET le conteneur HTML
                        parsed_matches.append({"team1": team1, "team2": team2, "datetime_paris": paris_tz.localize(dt)})
                        match_containers_html.append(match_container)
                    except: continue
            
            # --- Affichage du résultat ---
            if parsed_matches:
                self.log_test("Analyse HTML", True, f"{len(parsed_matches)} matchs parsés.")
                if not silent:
                    embed = discord.Embed(title="✅ Test de Scraping et Parsing Réussi",
                                          description=f"**{len(parsed_matches)} matchs programmés** trouvés et analysés.",
                                          color=discord.Color.green())
                    
                    # 1. Afficher les données parsées
                    parsed_value = ""
                    for match in parsed_matches[:5]:
                        dt = match['datetime_paris']
                        parsed_value += f"• **{match['team1']}** vs **{match['team2']}** - {dt.strftime('%d/%m à %H:%M')}\n"
                    embed.add_field(name="📅 Prochains Matchs Trouvés (5 max)", value=parsed_value or "Aucun", inline=False)
                    
                    # 2. Afficher le HTML brut des conteneurs
                    for i, container in enumerate(match_containers_html[:5]):
                        match_data = parsed_matches[i]
                        # Utiliser prettify() pour un affichage plus propre et lisible
                        html_content = container.prettify(formatter="html")
                        # Tronquer pour éviter de dépasser les limites de Discord
                        truncated_html = (html_content[:950] + '...') if len(html_content) > 950 else html_content
                        
                        embed.add_field(
                            name=f"📄 HTML Match {i+1}: {match_data['team1']} vs {match_data['team2']}",
                            value=f"```html\n{truncated_html}\n```",
                            inline=False
                        )
                    await ctx.send(embed=embed)
            else:
                self.log_test("Analyse HTML", False, "Aucun match n'a pu être parsé.")
                if not silent:
                    await ctx.send("⚠️ **Scraping réussi mais aucun match n'a pu être parsé.** La structure de la page a peut-être changé ou il n'y a pas de matchs programmés.")

        except Exception as e:
            self.log_test("Scraping", False, f"{type(e).__name__}: {e}")
            if not silent: await ctx.send(f"❌ **Erreur scraping :** `{type(e).__name__}: {e}`")
    
    @test_group.command(name='db')
    async def test_db(self, ctx, silent=False):
        if not silent:
            msg = await ctx.send("🗄️ **Test de la base de données...**"); self.test_messages.append(msg)
        try:
            test_user_id = self.bot.user.id; database.check_user(test_user_id)
            d_before = database.get_user_data(test_user_id); self.log_test("DB: Lecture", d_before is not None)
            database.update_points(test_user_id, 50); d_after = database.get_user_data(test_user_id)
            success = d_after['points'] == d_before['points'] + 50; self.log_test("DB: Écriture", success)
            database.update_points(test_user_id, -50)
            if not silent: await ctx.send("✅ **Base de données fonctionnelle**.")
        except Exception as e:
            self.log_test("Base de données", False, str(e)); await ctx.send(f"❌ **Erreur DB :** `{str(e)}`")

    @test_group.command(name='collection')
    async def test_collection(self, ctx, silent=False):
        if not silent:
            msg = await ctx.send("🎴 **Test du système de collection...**"); self.test_messages.append(msg)
        try:
            with open('cards.json', 'r', encoding='utf-8') as f: cards_data = json.load(f)
            if not isinstance(cards_data, list) or not cards_data: raise ValueError("JSON vide")
            self.log_test("Fichier cards.json", True, f"{len(cards_data)} cartes.")
            card = cards_data[0]
            embed = discord.Embed(title=f"**{card['nom']}**", color=discord.Color.blue); embed.set_image(url=card['image_url'])
            if not silent:
                msg = await ctx.send(embed=embed); self.test_messages.append(msg)
            self.log_test("Affichage carte", True)
        except Exception as e:
            self.log_test("Collection", False, str(e)); await ctx.send(f"❌ **Erreur collection :** `{str(e)}`")

    @test_group.command(name='pronostics')
    async def test_pronostics(self, ctx, silent=False):
        if not silent:
            msg = await ctx.send("🎯 **Test du système de pronostics...**"); self.test_messages.append(msg)
        embed = discord.Embed(title="🏐 [TEST] A vs B", description="Réactions:", color=discord.Color.blue())
        try:
            msg = await ctx.send(embed=embed); self.test_messages.append(msg)
            for emoji in ["1️⃣", "❌", "2️⃣"]: await msg.add_reaction(emoji)
            self.log_test("Réactions pronostics", True)
            if not silent: await ctx.send("✅ **Message de prono créé.**", delete_after=10)
        except Exception as e:
            self.log_test("Réactions pronostics", False, str(e)); await ctx.send(f"❌ **Erreur prono :** `{e}`")

    @test_group.command(name='events')
    async def test_events(self, ctx, silent=False):
        if not silent:
            msg = await ctx.send("📅 **Test de création d'événement...**"); self.test_messages.append(msg)
        try:
            start_time = datetime.now(timezone.utc) + timedelta(minutes=2)
            event = await ctx.guild.create_scheduled_event(name="[TEST] Event", start_time=start_time, end_time=start_time + timedelta(hours=1), location="Test")
            self.log_test("Création événement", True)
            if not silent:
                msg = await ctx.send(f"✅ **Événement créé.** Suppression dans 15s."); self.test_messages.append(msg)
            await asyncio.sleep(15); await event.delete()
            if not silent:
                msg = await ctx.send("🗑️ Événement test supprimé.", delete_after=10); self.test_messages.append(msg)
        except Exception as e:
            self.log_test("Création événement", False, str(e)); await ctx.send(f"❌ **Erreur event :** `{str(e)}`")

    @test_group.command(name='rss')
    async def test_rss(self, ctx, silent=False):
        if not silent:
            msg = await ctx.send(f"📰 **Test du flux RSS sur `{RSS_URL}`...**"); self.test_messages.append(msg)
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(RSS_URL, timeout=15) as response:
                    if response.status != 200:
                        self.log_test("Flux RSS", False, f"HTTP {response.status}"); await ctx.send(f"❌ Erreur HTTP {response.status}.")
                        return
                    feed = feedparser.parse(await response.text())
            if feed.bozo:
                self.log_test("Flux RSS", False, "Flux invalide"); await ctx.send(f"❌ Flux invalide: `{feed.bozo_exception}`")
            else:
                self.log_test("Flux RSS", True, f"{len(feed.entries)} articles."); await ctx.send(f"✅ Flux RSS OK ({len(feed.entries)} articles).")
        except Exception as e:
            self.log_test("Flux RSS", False, str(e)); await ctx.send(f"❌ **Erreur RSS :** `{str(e)}`")

    @test_group.command(name='clean')
    @commands.has_permissions(manage_messages=True)
    async def test_clean(self, ctx):
        count = len(self.test_messages)
        await self.clean_test_messages()
        await ctx.send(f"🧹 **{count} message(s) de test supprimé(s).**", delete_after=10, ephemeral=True)

    async def send_test_summary(self, ctx):
        if not self.test_results: return
        success_count = sum(1 for r in self.test_results if r.startswith("✅"))
        total_count = len(self.test_results)
        color = discord.Color.green() if success_count == total_count else discord.Color.red()
        embed = discord.Embed(title="📊 Résumé des Tests", description=f"**{success_count}/{total_count}** réussis.", color=color)
        results_text = "\n".join(self.test_results)
        chunks = [results_text[i:i + 1024] for i in range(0, len(results_text), 1024)]
        for i, chunk in enumerate(chunks): embed.add_field(name=f"Détails ({i+1}/{len(chunks)})", value=chunk, inline=False)
        await ctx.send(embed=embed)

async def setup(bot):
    await bot.add_cog(TestCog(bot))
