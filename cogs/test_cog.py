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

# On importe les nouvelles constantes pour que les tests restent synchronisés
from cogs.events_cog import LIVESCORE_URL, RSS_URL, BROWSERLESS_API_TOKEN, BROWSERLESS_SCRAPE_API_URL

class TestCog(commands.Cog):
    """Cog pour tester toutes les fonctionnalités du bot."""
    
    def __init__(self, bot):
        self.bot = bot
        self.test_results = []
        self.test_messages = []
        
    def log_test(self, test_name, success, message=""):
        """Enregistre le résultat d'un test."""
        emoji = "✅" if success else "❌"
        # S'assure que le message n'est pas trop long pour l'affichage final
        if len(message) > 150:
            message = message[:147] + "..."
        result = f"{emoji} **{test_name}**: {message}"
        self.test_results.append(result)
        
    async def clean_test_messages(self):
        """Nettoie tous les messages de test."""
        for msg in self.test_messages:
            try:
                await msg.delete()
                await asyncio.sleep(0.5) # Léger délai pour éviter les rate-limits
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
            ("`!test scraping`", "Teste le scraping via Browserless.io."),
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
        
        # Nettoyage des anciens messages avant de commencer
        await self.clean_test_messages()
        
        start_message = await ctx.send("🧪 **Lancement de la suite de tests complète...**")
        self.test_messages.append(start_message)
        
        # Exécution séquentielle pour un rapport clair
        await self.test_permissions(ctx, silent=True)
        await self.test_scraping(ctx, silent=True)
        await self.test_db(ctx, silent=True)
        await self.test_collection(ctx, silent=True)
        await self.test_pronostics(ctx, silent=True)
        await self.test_events(ctx, silent=True)
        await self.test_rss(ctx, silent=True)
        
        # Envoi du résumé
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
            "Envoyer des messages": permissions.send_messages,
            "Intégrer des liens": permissions.embed_links,
            "Ajouter des réactions": permissions.add_reactions,
            "Lire l'historique": permissions.read_message_history,
            "Gérer les événements": permissions.manage_events,
            "Gérer les messages": permissions.manage_messages,
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
        """Teste le scraping via Browserless.io et l'analyse de la réponse."""
        if not silent:
            msg = await ctx.send(f"🌐 **Test du scraping avec Browserless.io sur `{LIVESCORE_URL}`...**")
            self.test_messages.append(msg)

        if not BROWSERLESS_API_TOKEN:
            self.log_test("Configuration Browserless", False, "La variable d'environnement BROWSERLESS_API_TOKEN est manquante.")
            if not silent:
                await ctx.send("❌ **Échec de la configuration :** Le token API de Browserless (BROWSERLESS_API_TOKEN) est introuvable dans les variables d'environnement.")
            return

        # Payload simple pour le test : on veut juste vérifier que Browserless peut trouver
        # les conteneurs de matchs, ce qui valide que la page est chargée correctement.
        payload = {
            "url": LIVESCORE_URL,
            "elements": [{"selector": "div.event__match--scheduled"}]
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(BROWSERLESS_SCRAPE_API_URL, json=payload, timeout=45) as response:
                    status = response.status
                    self.log_test("API Browserless Connexion", status == 200, f"Status: {status}")

                    if status != 200:
                        error_text = await response.text()
                        self.log_test("Analyse Réponse JSON", False, f"Erreur HTTP: {error_text[:100]}")
                        if not silent:
                            await ctx.send(f"❌ **Échec de la connexion à l'API Browserless (Status: {status})**\n`{error_text}`")
                        return

                    data = await response.json()
            
            # Vérifier la structure de la réponse de Browserless
            if not isinstance(data, dict) or 'data' not in data or not data['data'] or 'results' not in data['data'][0]:
                self.log_test("Analyse Réponse JSON", False, "La structure de la réponse JSON est invalide ou vide.")
                if not silent:
                    await ctx.send(f"❌ **Échec de l'analyse :** La réponse de Browserless est mal formée.\nContenu: ` {str(data)[:500]} `")
                return

            results = data['data'][0]['results']
            match_count = len(results)
            self.log_test("Analyse Réponse JSON", True, f"{match_count} conteneurs de match trouvés.")
            
            if not silent:
                embed = discord.Embed(
                    title="✅ Test de Scraping Browserless Réussi",
                    description=f"L'API a répondu correctement et a trouvé **{match_count} matchs programmés** sur la page.",
                    color=discord.Color.green()
                )
                if match_count == 0:
                     embed.set_footer(text="Note: 0 match trouvé peut être normal s'il n'y a pas de rencontre à venir.")
                await ctx.send(embed=embed)

        except asyncio.TimeoutError:
            self.log_test("API Browserless Connexion", False, "Timeout (délai dépassé)")
            if not silent:
                await ctx.send("❌ **Erreur de scraping :** L'API Browserless a mis trop de temps à répondre. Le service est peut-être lent.")
        except aiohttp.ClientConnectorError as e:
            self.log_test("API Browserless Connexion", False, "Erreur de connexion (DNS/Réseau)")
            if not silent:
                await ctx.send(f"❌ **Erreur de connexion :** Impossible de contacter l'API Browserless. Problème de DNS ou de réseau.\n`{e}`")
        except Exception as e:
            self.log_test("Scraping Browserless", False, str(e))
            if not silent:
                await ctx.send(f"❌ **Erreur inattendue lors du scraping :**\n`{type(e).__name__}: {e}`")

    @test_group.command(name='db')
    async def test_db(self, ctx, silent=False):
        """Teste les opérations de base de données."""
        if not silent:
            msg = await ctx.send("🗄️ **Test de la base de données...**")
            self.test_messages.append(msg)
            
        try:
            test_user_id = self.bot.user.id
            
            # 1. Création / Lecture
            database.check_user(test_user_id)
            user_data_before = database.get_user_data(test_user_id)
            self.log_test("DB: Lecture utilisateur", user_data_before is not None, "OK" if user_data_before else "Échec")

            # 2. Écriture
            database.update_points(test_user_id, 50)
            user_data_after = database.get_user_data(test_user_id)
            success = user_data_after['points'] == user_data_before['points'] + 50
            self.log_test("DB: Écriture points", success, "OK" if success else "Échec")
            
            # 3. Rétablir l'état initial
            database.update_points(test_user_id, -50)
            
            if not silent:
                await ctx.send("✅ **Base de données fonctionnelle** (lecture et écriture testées avec succès).")
            
        except Exception as e:
            self.log_test("Base de données", False, str(e))
            if not silent:
                await ctx.send(f"❌ **Erreur Base de Données :** `{str(e)}`")

    @test_group.command(name='collection')
    async def test_collection(self, ctx, silent=False):
        """Teste le système de collection de cartes."""
        if not silent:
            msg = await ctx.send("🎴 **Test du système de collection...**")
            self.test_messages.append(msg)
            
        try:
            # Vérifier que cards.json existe et est valide
            try:
                with open('cards.json', 'r', encoding='utf-8') as f:
                    cards_data = json.load(f)
                if not isinstance(cards_data, list) or len(cards_data) == 0:
                    raise ValueError("Le fichier JSON est vide ou mal formaté.")
                self.log_test("Fichier cards.json", True, f"{len(cards_data)} cartes chargées")
            except Exception as e:
                self.log_test("Fichier cards.json", False, str(e))
                if not silent:
                    await ctx.send(f"❌ **Fichier `cards.json` illisible ou introuvable :** `{e}`")
                return
            
            # Test d'affichage d'une carte
            card = cards_data[0]
            embed = discord.Embed(
                title=f"**{card.get('nom', 'N/A')}**",
                description=f"**Rareté :** {card.get('rarete', 'N/A')}\n**Club :** {card.get('club', 'N/A')}",
                color=discord.Color.blue()
            )
            if 'image_url' in card:
                embed.set_image(url=card['image_url'])
            embed.set_footer(text="Test d'affichage de carte")
            
            if not silent:
                msg = await ctx.send(embed=embed)
                self.test_messages.append(msg)
            self.log_test("Affichage carte", True, "L'embed de la première carte a été généré.")
            
        except Exception as e:
            self.log_test("Collection", False, str(e))
            if not silent:
                await ctx.send(f"❌ **Erreur inattendue dans le module de collection :** `{str(e)}`")

    @test_group.command(name='pronostics')
    async def test_pronostics(self, ctx, silent=False):
        """Teste le système de pronostics."""
        if not silent:
            msg = await ctx.send("🎯 **Test du système de pronostics...**")
            self.test_messages.append(msg)
            
        match_time = datetime.now(timezone.utc) + timedelta(hours=24)
        
        embed = discord.Embed(
            title="🏐 [TEST] Équipe A vs Équipe B",
            description=(
                "Cliquez sur une réaction pour pronostiquer :\n"
                "1️⃣ = Victoire **Équipe A**\n"
                "❌ = Match nul\n"
                "2️⃣ = Victoire **Équipe B**"
            ),
            color=discord.Color.blue(),
            timestamp=match_time
        ).set_footer(text="⚠️ CECI EST UN TEST. Les réactions sont fonctionnelles.")
        
        try:
            msg = await ctx.send(embed=embed)
            self.test_messages.append(msg)
            
            for emoji in ["1️⃣", "❌", "2️⃣"]:
                await msg.add_reaction(emoji)
                await asyncio.sleep(0.3)
            self.log_test("Réactions pronostics", True, "Message créé et réactions ajoutées")
            if not silent:
                await ctx.send("✅ **Message de pronostic créé avec succès.**", delete_after=10)
        except Exception as e:
            self.log_test("Réactions pronostics", False, str(e))
            if not silent:
                await ctx.send(f"❌ **Erreur lors de la création du message de test de pronostic :** `{e}`")

    @test_group.command(name='events')
    async def test_events(self, ctx, silent=False):
        """Teste la création d'événements Discord."""
        if not silent:
            msg = await ctx.send("📅 **Test de création d'événement...**")
            self.test_messages.append(msg)
        
        try:
            start_time = datetime.now(timezone.utc) + timedelta(minutes=2)
            
            event = await ctx.guild.create_scheduled_event(
                name="[TEST] Événement (Suppression auto)",
                description="Ceci est un événement de test.",
                start_time=start_time,
                end_time=start_time + timedelta(hours=1),
                entity_type=discord.EntityType.external,
                location="Test Arena"
            )
            
            self.log_test("Création événement", True, f"ID: {event.id}")
            if not silent:
                msg = await ctx.send(f"✅ **Événement créé.** Il sera supprimé dans 15 secondes.")
                self.test_messages.append(msg)
            
            await asyncio.sleep(15)
            await event.delete()
            if not silent:
                msg = await ctx.send("🗑️ Événement de test supprimé.", delete_after=10)
                self.test_messages.append(msg)
            
        except discord.Forbidden:
            self.log_test("Création événement", False, "Permissions insuffisantes")
            if not silent:
                await ctx.send("❌ **Permissions insuffisantes** pour créer des événements. Vérifiez les rôles du bot.")
        except Exception as e:
            self.log_test("Création événement", False, str(e))
            if not silent:
                await ctx.send(f"❌ **Erreur lors de la création de l'événement :** `{str(e)}`")

    @test_group.command(name='rss')
    async def test_rss(self, ctx, silent=False):
        """Teste le flux RSS."""
        if not silent:
            msg = await ctx.send(f"📰 **Test du flux RSS sur `{RSS_URL}`...**")
            self.test_messages.append(msg)
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(RSS_URL, timeout=15) as response:
                    if response.status != 200:
                        self.log_test("Flux RSS", False, f"Erreur HTTP {response.status}")
                        if not silent:
                            await ctx.send(f"❌ **Erreur HTTP {response.status}** en accédant au flux RSS.")
                        return

                    feed_text = await response.text()
                    # feedparser n'est pas asynchrone, on peut l'exécuter directement
                    import feedparser
                    feed = feedparser.parse(feed_text)
            
            if feed.bozo:
                exception = feed.bozo_exception
                self.log_test("Flux RSS", False, f"Flux invalide: {exception}")
                if not silent:
                    await ctx.send(f"❌ **Flux RSS invalide ou mal formé.** Erreur: `{exception}`")
            else:
                count = len(feed.entries)
                self.log_test("Flux RSS", True, f"{count} articles trouvés.")
                if not silent:
                    embed = discord.Embed(
                        title="✅ Flux RSS Fonctionnel",
                        description=f"**{count} articles** trouvés dans le flux.",
                        color=0xe8874f
                    )
                    if feed.entries:
                        entry = feed.entries[0]
                        embed.add_field(name="Dernier article trouvé", value=f"[{entry.title}]({entry.link})")
                    
                    msg = await ctx.send(embed=embed)
                    self.test_messages.append(msg)
                
        except asyncio.TimeoutError:
            self.log_test("Flux RSS", False, "Timeout (délai dépassé)")
            if not silent:
                await ctx.send("❌ **Erreur de test RSS :** Le site a mis trop de temps à répondre.")
        except Exception as e:
            self.log_test("Flux RSS", False, str(e))
            if not silent:
                await ctx.send(f"❌ **Erreur inattendue lors du test RSS :** `{str(e)}`")

    @test_group.command(name='clean')
    @commands.has_permissions(manage_messages=True)
    async def test_clean(self, ctx):
        """Nettoie tous les messages de test."""
        count = len(self.test_messages)
        await self.clean_test_messages()
        await ctx.send(f"🧹 **{count} message(s) de test supprimé(s).**", delete_after=10, ephemeral=True)

    async def send_test_summary(self, ctx):
        """Envoie un résumé de tous les tests exécutés."""
        if not self.test_results:
            await ctx.send("Aucun test n'a été exécuté pour générer un résumé.")
            return

        success_count = sum(1 for r in self.test_results if r.startswith("✅"))
        total_count = len(self.test_results)
        
        color = discord.Color.green() if success_count == total_count else (discord.Color.orange() if success_count > 0 else discord.Color.red())
        
        embed = discord.Embed(
            title="📊 Résumé de la Suite de Tests",
            description=f"**{success_count} / {total_count}** tests réussis.",
            color=color,
            timestamp=datetime.now(timezone.utc)
        )
        
        results_text = "\n".join(self.test_results)
        # Diviser les résultats en plusieurs champs pour ne pas dépasser la limite de 1024 caractères par champ
        chunks = [results_text[i:i + 1024] for i in range(0, len(results_text), 1024)]
        
        for i, chunk in enumerate(chunks):
            embed.add_field(name=f"Résultats Détaillés ({i+1}/{len(chunks)})", value=chunk, inline=False)
        
        if success_count < total_count:
            embed.set_footer(text="⚠️ Certains tests ont échoué. Vérifiez les détails ci-dessus.")
        else:
            embed.set_footer(text="🎉 Tous les tests sont passés avec succès !")
            
        await ctx.send(embed=embed)

async def setup(bot):
    """Charge le cog de tests."""
    await bot.add_cog(TestCog(bot))
