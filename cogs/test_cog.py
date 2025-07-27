import discord
from discord.ext import commands
import asyncio
import json
from datetime import datetime, timedelta, timezone
import pytz
import database
from playwright.async_api import async_playwright
import feedparser # Assurez-vous que feedparser est importé pour le test RSS

class TestCog(commands.Cog):
    """Cog pour tester toutes les fonctionnalités du bot."""
    
    def __init__(self, bot):
        self.bot = bot
        self.test_results = []
        self.test_messages = []
        
    def log_test(self, test_name, success, message=""):
        """Enregistre le résultat d'un test."""
        emoji = "✅" if success else "❌"
        result = f"{emoji} **{test_name}**: {message}"
        self.test_results.append(result)
        
    async def clean_test_messages(self):
        """Nettoie tous les messages de test."""
        for msg in self.test_messages:
            try:
                await msg.delete()
                await asyncio.sleep(0.5)
            except:
                pass
        self.test_messages.clear()

    @commands.group(name='test', invoke_without_command=True)
    @commands.has_permissions(administrator=True)
    async def test_group(self, ctx):
        """Groupe de commandes de test."""
        embed = discord.Embed(
            title="🧪 Tests du Bot Handnews",
            description="Utilisez les sous-commandes pour tester différents composants.",
            color=discord.Color.blue()
        )
        
        tests = [
            ("`!test all`", "Lance tous les tests"),
            ("`!test permissions`", "Vérifie les permissions du bot"),
            ("`!test api`", "Teste le scraping via Playwright"),
            ("`!test db`", "Teste la base de données"),
            ("`!test collection`", "Teste le système de cartes"),
            ("`!test pronostics`", "Teste le système de pronostics"),
            ("`!test events`", "Teste la création d'événements"),
            ("`!test rss`", "Teste le flux RSS"),
            ("`!test clean`", "Nettoie les messages de test")
        ]
        
        for cmd, desc in tests:
            embed.add_field(name=cmd, value=desc, inline=False)
            
        await ctx.send(embed=embed)

    @test_group.command(name='all')
    async def test_all(self, ctx):
        """Lance tous les tests disponibles."""
        self.test_results = []
        
        await ctx.send("🧪 **Lancement de tous les tests...**")
        
        # 1. Permissions
        await self.test_permissions(ctx)
        
        # 2. API (maintenant Playwright)
        await self.test_api(ctx)
        
        # 3. Base de données
        await self.test_db(ctx)
        
        # 4. Collection
        await self.test_collection(ctx)
        
        # 5. Pronostics
        await self.test_pronostics(ctx)
        
        # 6. Events
        await self.test_events(ctx)
        
        # 7. RSS
        await self.test_rss(ctx)
        
        # Résumé
        await self.send_test_summary(ctx)

    @test_group.command(name='permissions')
    async def test_permissions(self, ctx):
        """Vérifie toutes les permissions nécessaires."""
        embed = discord.Embed(
            title="🔐 Test des Permissions",
            description="Vérification des permissions du bot",
            color=discord.Color.blue()
        )
        
        permissions = ctx.guild.me.guild_permissions
        
        # Liste complète des permissions nécessaires
        required_perms = {
            "view_channel": ("Voir les salons", permissions.view_channel),
            "send_messages": ("Envoyer des messages", permissions.send_messages),
            "send_messages_in_threads": ("Messages dans les fils", permissions.send_messages_in_threads),
            "embed_links": ("Intégrer des liens", permissions.embed_links),
            "attach_files": ("Joindre des fichiers", permissions.attach_files),
            "read_message_history": ("Lire l'historique", permissions.read_message_history),
            "add_reactions": ("Ajouter des réactions", permissions.add_reactions),
            "use_external_emojis": ("Emojis externes", permissions.use_external_emojis),
            "manage_events": ("Gérer les événements", permissions.manage_events),
            "manage_messages": ("Gérer les messages", permissions.manage_messages),
            "manage_roles": ("Gérer les rôles", permissions.manage_roles),
            "connect": ("Se connecter (vocal)", permissions.connect),
            "speak": ("Parler (vocal)", permissions.speak),
            "mention_everyone": ("Mentionner @everyone", permissions.mention_everyone),
            "use_slash_commands": ("Commandes slash", permissions.use_application_commands),
        }
        
        critical_perms = ["view_channel", "send_messages", "embed_links", "add_reactions", 
                         "read_message_history", "manage_events"]
        
        all_critical_ok = True
        field_value = ""
        
        for perm_name, (display_name, has_perm) in required_perms.items():
            emoji = "✅" if has_perm else "❌"
            is_critical = perm_name in critical_perms
            
            if is_critical and not has_perm:
                all_critical_ok = False
                field_value += f"{emoji} **{display_name}** ⚠️ CRITIQUE\n"
            else:
                field_value += f"{emoji} {display_name}\n"
            
            self.log_test(f"Permission {display_name}", has_perm)
        
        embed.add_field(name="Permissions", value=field_value[:1024], inline=False)
        
        if all_critical_ok:
            embed.color = discord.Color.green()
            embed.set_footer(text="✅ Toutes les permissions critiques sont accordées")
        else:
            embed.color = discord.Color.red()
            embed.set_footer(text="❌ Des permissions critiques manquent")
        
        await ctx.send(embed=embed)

    # --- VERSION CORRIGÉE DE test_api ---
    @test_group.command(name='api')
    async def test_api(self, ctx):
        """Teste le scraping via Playwright sur livescore.in."""
        await ctx.send("🔍 **Test du scraping Playwright...**")
        url = "https://www.livescore.in/fr/handball/france/starligue/"
        async with async_playwright() as p:
            browser = None
            try:
                browser = await p.chromium.launch()
                page = await browser.new_page()
                await page.goto(url, timeout=30000)
                
                # Gérer le bouton des cookies
                cookie_button = page.locator("#onetrust-accept-btn-handler")
                if await cookie_button.is_visible(timeout=5000):
                    await cookie_button.click()

                # Attendre un sélecteur qui contient les matchs
                await page.wait_for_selector(".event__match", timeout=15000)
                
                # Récupérer les matchs programmés
                matches_scheduled = await page.locator(".event__match--scheduled").all()
                
                if matches_scheduled:
                    match_count = len(matches_scheduled)
                    self.log_test("Scraping Livescore", True, f"{match_count} matchs trouvés")
                    
                    embed = discord.Embed(
                        title="✅ Scraping Playwright Fonctionnel",
                        description=f"**{match_count} matchs programmés** trouvés sur [la page Starligue]({url})",
                        color=discord.Color.green()
                    )
                    
                    # Extraire les détails du premier match pour l'exemple
                    first_match = matches_scheduled[0]
                    team1 = await first_match.locator(".event__participant--home").inner_text()
                    team2 = await first_match.locator(".event__participant--away").inner_text()
                    time = await first_match.locator(".event__time").inner_text()
                    
                    embed.add_field(
                        name="Exemple de match trouvé",
                        value=f"🕒 {time} - {team1} vs {team2}",
                        inline=False
                    )
                    await ctx.send(embed=embed)
                else:
                    self.log_test("Scraping Livescore", False, "Aucun match programmé trouvé sur la page")
                    await ctx.send("⚠️ Scraping Livescore : Aucun match programmé n'a été trouvé sur la page.")
                    
            except Exception as e:
                self.log_test("Scraping Livescore", False, str(e))
                await ctx.send(f"❌ Erreur Playwright : {str(e)}")
            finally:
                if browser:
                    await browser.close()
    
    @test_group.command(name='db')
    async def test_db(self, ctx):
        """Teste les opérations de base de données."""
        await ctx.send("🗄️ **Test de la base de données...**")
        
        try:
            test_user_id = self.bot.user.id
            database.check_user(test_user_id)
            user_data_before = database.get_user_data(test_user_id)
            self.log_test("DB: Création/Lecture utilisateur", user_data_before is not None)

            database.update_points(test_user_id, 100)
            user_data_after = database.get_user_data(test_user_id)
            self.log_test("DB: Ajout points", user_data_after['points'] == user_data_before['points'] + 100)
            
            # Revenir à l'état initial
            database.update_points(test_user_id, -100)
            
            await ctx.send("✅ **Base de données fonctionnelle** (lecture, écriture testées).")
            
        except Exception as e:
            self.log_test("Base de données", False, str(e))
            await ctx.send(f"❌ Erreur DB : {str(e)}")

    @test_group.command(name='collection')
    async def test_collection(self, ctx):
        """Teste le système de collection de cartes."""
        await ctx.send("🎴 **Test du système de collection...**")
        
        try:
            # Vérifier que cards.json existe
            try:
                with open('cards.json', 'r', encoding='utf-8') as f:
                    cards_data = json.load(f)
                self.log_test("Fichier cards.json", True, f"{len(cards_data)} cartes")
            except FileNotFoundError:
                self.log_test("Fichier cards.json", False, "Fichier manquant")
                await ctx.send("❌ Fichier `cards.json` introuvable.")
                return
            
            # Test d'affichage d'une carte
            if cards_data:
                card = cards_data[0]
                embed = discord.Embed(
                    title=f"**{card['nom']}**",
                    description=f"**Rareté :** {card['rarete']}\n**Club :** {card['club']}",
                    color=discord.Color.blue()
                )
                if 'image_url' in card:
                    embed.set_image(url=card['image_url'])
                embed.set_footer(text="Test d'affichage de carte")
                
                msg = await ctx.send("**Test d'affichage de carte :**", embed=embed)
                self.test_messages.append(msg)
                self.log_test("Affichage carte", True)
            
            await ctx.send("✅ **Système de collection fonctionnel** (chargement et affichage OK).")
            
        except Exception as e:
            self.log_test("Collection", False, str(e))
            await ctx.send(f"❌ Erreur collection : {str(e)}")

    @test_group.command(name='pronostics')
    async def test_pronostics(self, ctx):
        """Teste le système de pronostics."""
        await ctx.send("🎯 **Test du système de pronostics...**")
        
        match_time = datetime.now(timezone.utc) + timedelta(hours=24)
        match_time_paris = match_time.astimezone(pytz.timezone('Europe/Paris'))
        
        embed = discord.Embed(
            title="🏐 [TEST] Montpellier vs PSG",
            description=(
                f"**Date :** {match_time_paris.strftime('%d/%m/%Y à %H:%M')}\n"
                f"**Journée :** TEST\n\n"
                "Cliquez sur une réaction pour pronostiquer :\n"
                "1️⃣ = Victoire **Montpellier**\n"
                "❌ = Match nul\n"
                "2️⃣ = Victoire **PSG**"
            ),
            color=discord.Color.blue(),
            timestamp=match_time
        )
        embed.set_footer(text="⚠️ CECI EST UN TEST")
        
        msg = await ctx.send(embed=embed)
        self.test_messages.append(msg)
        
        try:
            for emoji in ["1️⃣", "❌", "2️⃣"]:
                await msg.add_reaction(emoji)
                await asyncio.sleep(0.3)
            self.log_test("Réactions pronostics", True)
            await ctx.send("✅ **Message de pronostic créé avec succès** (réactions ajoutées).")
        except Exception as e:
            self.log_test("Réactions pronostics", False, str(e))
            await ctx.send(f"❌ Erreur lors de l'ajout des réactions : {e}")

    @test_group.command(name='events')
    async def test_events(self, ctx):
        """Teste la création d'événements Discord."""
        await ctx.send("📅 **Test de création d'événement...**")
        
        try:
            start_time = datetime.now(timezone.utc) + timedelta(minutes=2)
            end_time = start_time + timedelta(hours=2)
            
            event = await ctx.guild.create_scheduled_event(
                name="[TEST] Match de handball (Suppression auto)",
                description="Ceci est un événement de test.",
                start_time=start_time,
                end_time=end_time,
                entity_type=discord.EntityType.external,
                location="Test Arena",
                privacy_level=discord.PrivacyLevel.guild_only
            )
            
            self.log_test("Création événement", True, f"ID: {event.id}")
            await ctx.send(f"✅ **Événement créé** (ID: {event.id}). Il sera supprimé dans 15 secondes.")
            
            await asyncio.sleep(15)
            await event.delete()
            self.test_messages.append(await ctx.send("🗑️ Événement de test supprimé."))
            
        except discord.Forbidden:
            self.log_test("Création événement", False, "Permissions insuffisantes")
            await ctx.send("❌ Permissions insuffisantes pour créer des événements.")
        except Exception as e:
            self.log_test("Création événement", False, str(e))
            await ctx.send(f"❌ Erreur lors de la création de l'événement : {str(e)}")

    @test_group.command(name='rss')
    async def test_rss(self, ctx):
        """Teste le flux RSS."""
        await ctx.send("📰 **Test du flux RSS...**")
        
        try:
            feed = feedparser.parse("https://handnews.fr/feed")
            
            if not feed.bozo:
                entry_count = len(feed.entries)
                self.log_test("Flux RSS", True, f"{entry_count} articles")
                
                if feed.entries:
                    entry = feed.entries[0]
                    embed = discord.Embed(
                        title=f"Test du dernier article RSS : {entry.title[:150]}",
                        url=entry.link,
                        description=(entry.summary[:200] + "..." if hasattr(entry, 'summary') and len(entry.summary) > 200 else "Pas de résumé."),
                        color=0xe8874f
                    )
                    embed.set_footer(text="Test RSS Handnews")
                    
                    msg = await ctx.send(embed=embed)
                    self.test_messages.append(msg)
                    
                await ctx.send(f"✅ **RSS fonctionnel** - {entry_count} articles trouvés.")
            else:
                self.log_test("Flux RSS", False, "Flux invalide ou erreur de parsing")
                await ctx.send("❌ Flux RSS invalide ou erreur de parsing.")
                
        except Exception as e:
            self.log_test("Flux RSS", False, str(e))
            await ctx.send(f"❌ Erreur lors du test RSS : {str(e)}")

    @test_group.command(name='clean')
    async def test_clean(self, ctx):
        """Nettoie tous les messages de test."""
        count = len(self.test_messages)
        await self.clean_test_messages()
        await ctx.send(f"🧹 **{count} messages de test supprimés**", delete_after=5)

    async def send_test_summary(self, ctx):
        """Envoie un résumé des tests."""
        success_count = sum(1 for r in self.test_results if r.startswith("✅"))
        total_count = len(self.test_results)
        
        color = discord.Color.green() if success_count == total_count else discord.Color.orange()
        embed = discord.Embed(
            title="📊 Résumé des Tests",
            description=f"**{success_count}/{total_count}** tests réussis.",
            color=color
        )
        
        results_text = "\n".join(self.test_results)
        if len(results_text) > 1024:
            results_text = results_text[:1020] + "\n..."
            
        embed.add_field(name="Résultats Détaillés", value=results_text, inline=False)
        
        if success_count < total_count:
            embed.set_footer(text="⚠️ Certains tests ont échoué. Vérifiez les logs.")
        else:
            embed.set_footer(text="🎉 Tous les tests sont passés avec succès !")
            
        await ctx.send(embed=embed)

async def setup(bot):
    """Charge le cog de tests."""
    await bot.add_cog(TestCog(bot))
