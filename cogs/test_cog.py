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
from bs4 import BeautifulSoup # Ajout nécessaire pour le test de la nouvelle méthode

# On importe les constantes avec le nom correct
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
            ("`!test scraping`", "Teste le scraping via Browserless et l'analyse HTML."),
            ("`!test db`", "Vérifie la connexion et les opérations de base."),
            ("`!test collection`", "Teste le chargement des cartes et leur affichage."),
            ("`!test pronostics`", "Teste la création d'un message de pronostic."),
            ("`!test events`", "Teste la création d'événements Discord planifiés."),
            ("`!test rss`", "Teste la lecture du flux RSS de Handnews."),
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
            
        embed = discord.Embed(
            title="🔐 Test des Permissions",
            description=f"Vérification des permissions dans le salon `{ctx.channel.name}`",
            color=discord.Color.blue()
        )
        
        permissions = ctx.channel.permissions_for(ctx.guild.me)
        required_perms = {
            "Envoyer des messages": permissions.send_messages, "Intégrer des liens": permissions.embed_links,
            "Ajouter des réactions": permissions.add_reactions, "Lire l'historique": permissions.read_message_history,
            "Gérer les événements": permissions.manage_events, "Gérer les messages": permissions.manage_messages,
        }
        
        all_critical_ok = True
        field_value = ""
        for name, has_perm in required_perms.items():
            emoji = "✅" if has_perm else "❌"
            if not has_perm:
                all_critical_ok = False
            field_value += f"{emoji} {name}\n"
            self.log_test(f"Permission: {name}", has_perm)
        
        embed.add_field(name="Permissions Critiques", value=field_value)
        
        if all_critical_ok:
            embed.color = discord.Color.green()
            embed.set_footer(text="✅ Toutes les permissions critiques sont accordées.")
        else:
            embed.color = discord.Color.red()
            embed.set_footer(text="❌ Des permissions critiques manquent !")
        
        if not silent:
            await ctx.send(embed=embed)

    @test_group.command(name='scraping')
    async def test_scraping(self, ctx, silent=False):
        """Teste le scraping via l'endpoint /content de Browserless et l'analyse avec BeautifulSoup."""
        if not silent:
            msg = await ctx.send(f"🌐 **Test du scraping avec Browserless (méthode /content) sur `{LIVESCORE_URL}`...**")
            self.test_messages.append(msg)

        if not BROWSERLESS_API_TOKEN:
            self.log_test("Configuration Browserless", False, "Variable BROWSERLESS_API_TOKEN manquante.")
            if not silent:
                await ctx.send("❌ **Échec de la configuration :** Le token API de Browserless est introuvable.")
            return

        payload = {"url": LIVESCORE_URL}

        try:
            async with aiohttp.ClientSession() as session:
                # On utilise la nouvelle URL d'API importée
                async with session.post(BROWSERLESS_CONTENT_API_URL, json=payload, timeout=45) as response:
                    status = response.status
                    self.log_test("API Browserless Connexion", status == 200, f"Status: {status}")

                    if status != 200:
                        error_text = await response.text()
                        if not silent:
                            await ctx.send(f"❌ **Échec de la connexion à l'API Browserless (Status: {status})**\n`{error_text}`")
                        return

                    html = await response.text()
            
            # Test d'analyse du HTML reçu
            soup = BeautifulSoup(html, 'html.parser')
            # On cherche un sélecteur clé pour vérifier que la page est bien celle attendue
            match_containers = soup.select("div.event__match") 
            match_count = len(match_containers)

            if match_count > 0:
                self.log_test("Analyse HTML", True, f"{match_count} conteneurs de match trouvés.")
                if not silent:
                    embed = discord.Embed(
                        title="✅ Test de Scraping Complet Réussi",
                        description=f"1. L'API Browserless a répondu avec succès (Status 200).\n2. L'analyse du HTML a permis de trouver **{match_count} conteneurs de match**.",
                        color=discord.Color.green()
                    )
                    await ctx.send(embed=embed)
            else:
                self.log_test("Analyse HTML", False, "Aucun conteneur de match ('div.event__match') trouvé.")
                if not silent:
                    await ctx.send("⚠️ **Scraping réussi mais analyse HTML échouée.** La structure de la page a peut-être changé (sélecteur `div.event__match` non trouvé).")

        except asyncio.TimeoutError:
            self.log_test("API Browserless Connexion", False, "Timeout")
            if not silent:
                await ctx.send("❌ **Erreur de scraping :** L'API Browserless a mis trop de temps à répondre.")
        except Exception as e:
            self.log_test("Scraping Browserless", False, str(e))
            if not silent:
                await ctx.send(f"❌ **Erreur inattendue lors du scraping :**\n`{type(e).__name__}: {e}`")

    @test_group.command(name='db')
    async def test_db(self, ctx, silent=False):
        if not silent:
            msg = await ctx.send("🗄️ **Test de la base de données...**")
            self.test_messages.append(msg)
        try:
            test_user_id = self.bot.user.id
            database.check_user(test_user_id)
            user_data_before = database.get_user_data(test_user_id)
            self.log_test("DB: Lecture", user_data_before is not None)
            database.update_points(test_user_id, 50)
            user_data_after = database.get_user_data(test_user_id)
            success = user_data_after['points'] == user_data_before['points'] + 50
            self.log_test("DB: Écriture", success)
            database.update_points(test_user_id, -50)
            if not silent:
                await ctx.send("✅ **Base de données fonctionnelle**.")
        except Exception as e:
            self.log_test("Base de données", False, str(e))
            if not silent:
                await ctx.send(f"❌ **Erreur DB :** `{str(e)}`")

    @test_group.command(name='collection')
    async def test_collection(self, ctx, silent=False):
        if not silent:
            msg = await ctx.send("🎴 **Test du système de collection...**")
            self.test_messages.append(msg)
        try:
            with open('cards.json', 'r', encoding='utf-8') as f:
                cards_data = json.load(f)
            if not isinstance(cards_data, list) or not cards_data:
                raise ValueError("JSON vide ou mal formaté.")
            self.log_test("Fichier cards.json", True, f"{len(cards_data)} cartes.")
            
            card = cards_data[0]
            embed = discord.Embed(title=f"**{card['nom']}**", description=f"**Rareté:** {card['rarete']}", color=discord.Color.blue())
            embed.set_image(url=card['image_url'])
            if not silent:
                msg = await ctx.send(embed=embed)
                self.test_messages.append(msg)
            self.log_test("Affichage carte", True)
        except Exception as e:
            self.log_test("Collection", False, str(e))
            if not silent: await ctx.send(f"❌ **Erreur collection :** `{str(e)}`")

    @test_group.command(name='pronostics')
    async def test_pronostics(self, ctx, silent=False):
        if not silent:
            msg = await ctx.send("🎯 **Test du système de pronostics...**")
            self.test_messages.append(msg)
        embed = discord.Embed(title="🏐 [TEST] A vs B", description="1️⃣, ❌, 2️⃣", color=discord.Color.blue())
        try:
            msg = await ctx.send(embed=embed)
            self.test_messages.append(msg)
            for emoji in ["1️⃣", "❌", "2️⃣"]:
                await msg.add_reaction(emoji)
            self.log_test("Réactions pronostics", True)
            if not silent: await ctx.send("✅ **Message de prono créé.**", delete_after=10)
        except Exception as e:
            self.log_test("Réactions pronostics", False, str(e))
            if not silent: await ctx.send(f"❌ **Erreur prono :** `{e}`")

    @test_group.command(name='events')
    async def test_events(self, ctx, silent=False):
        if not silent:
            msg = await ctx.send("📅 **Test de création d'événement...**")
            self.test_messages.append(msg)
        try:
            start_time = datetime.now(timezone.utc) + timedelta(minutes=2)
            event = await ctx.guild.create_scheduled_event(
                name="[TEST] Event", description="Test", start_time=start_time,
                end_time=start_time + timedelta(hours=1), entity_type=discord.EntityType.external, location="Test"
            )
            self.log_test("Création événement", True)
            if not silent:
                msg = await ctx.send(f"✅ **Événement créé.** Suppression dans 15s.")
                self.test_messages.append(msg)
            await asyncio.sleep(15)
            await event.delete()
            if not silent:
                msg = await ctx.send("🗑️ Événement de test supprimé.", delete_after=10)
                self.test_messages.append(msg)
        except Exception as e:
            self.log_test("Création événement", False, str(e))
            if not silent: await ctx.send(f"❌ **Erreur event :** `{str(e)}`")

    @test_group.command(name='rss')
    async def test_rss(self, ctx, silent=False):
        if not silent:
            msg = await ctx.send(f"📰 **Test du flux RSS sur `{RSS_URL}`...**")
            self.test_messages.append(msg)
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(RSS_URL, timeout=15) as response:
                    if response.status != 200:
                        self.log_test("Flux RSS", False, f"HTTP {response.status}")
                        if not silent: await ctx.send(f"❌ **Erreur HTTP {response.status}**.")
                        return
                    feed_text = await response.text()
                    feed = feedparser.parse(feed_text)
            if feed.bozo:
                self.log_test("Flux RSS", False, f"Flux invalide: {feed.bozo_exception}")
                if not silent: await ctx.send(f"❌ **Flux invalide.** Erreur: `{feed.bozo_exception}`")
            else:
                self.log_test("Flux RSS", True, f"{len(feed.entries)} articles.")
                if not silent:
                    await ctx.send(f"✅ **Flux RSS fonctionnel** ({len(feed.entries)} articles trouvés).")
        except Exception as e:
            self.log_test("Flux RSS", False, str(e))
            if not silent: await ctx.send(f"❌ **Erreur RSS :** `{str(e)}`")

    @test_group.command(name='clean')
    @commands.has_permissions(manage_messages=True)
    async def test_clean(self, ctx):
        count = len(self.test_messages)
        await self.clean_test_messages()
        await ctx.send(f"🧹 **{count} message(s) de test supprimé(s).**", delete_after=10, ephemeral=True)

    async def send_test_summary(self, ctx):
        if not self.test_results:
            return
        success_count = sum(1 for r in self.test_results if r.startswith("✅"))
        total_count = len(self.test_results)
        color = discord.Color.green() if success_count == total_count else discord.Color.red()
        embed = discord.Embed(title="📊 Résumé des Tests", description=f"**{success_count}/{total_count}** réussis.", color=color)
        results_text = "\n".join(self.test_results)
        chunks = [results_text[i:i + 1024] for i in range(0, len(results_text), 1024)]
        for i, chunk in enumerate(chunks):
            embed.add_field(name=f"Détails ({i+1}/{len(chunks)})", value=chunk, inline=False)
        await ctx.send(embed=embed)

async def setup(bot):
    await bot.add_cog(TestCog(bot))
