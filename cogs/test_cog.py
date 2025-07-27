import discord
from discord.ext import commands
import asyncio
import requests
from datetime import datetime, timedelta, timezone
import pytz
import database
import json
from playwright.async_api import async_playwright

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
            ("`!test api`", "Teste l'API Livescore"),
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
        
        # 2. API
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
            # Permissions de base
            "view_channel": ("Voir les salons", permissions.view_channel),
            "send_messages": ("Envoyer des messages", permissions.send_messages),
            "send_messages_in_threads": ("Messages dans les fils", permissions.send_messages_in_threads),
            "embed_links": ("Intégrer des liens", permissions.embed_links),
            "attach_files": ("Joindre des fichiers", permissions.attach_files),
            "read_message_history": ("Lire l'historique", permissions.read_message_history),
            "add_reactions": ("Ajouter des réactions", permissions.add_reactions),
            "use_external_emojis": ("Emojis externes", permissions.use_external_emojis),
            
            # Permissions pour les événements
            "manage_events": ("Gérer les événements", permissions.manage_events),
            
            # Permissions pour la modération
            "manage_messages": ("Gérer les messages", permissions.manage_messages),
            "manage_roles": ("Gérer les rôles", permissions.manage_roles),
            
            # Permissions vocales (optionnel)
            "connect": ("Se connecter (vocal)", permissions.connect),
            "speak": ("Parler (vocal)", permissions.speak),
            
            # Permissions avancées
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

    @test_group.command(name='api')
    async def test_api(self, ctx):
        """Teste l'API Livescore."""
        await ctx.send("🔍 **Test de l'API Livescore...**")
        
        try:
            # Essayons différentes URLs possibles pour l'API
            urls_to_try = [
                "https://prod-public-api.livescore.com/v1/api/app/stage/soccer/france/ligue-1/3?locale=fr",
                "https://prod-public-api.livescore.com/v1/api/app/stage/handball/france/starligue/3?locale=fr",
                "https://api.livescore.com/v1/competitions/handball/france/starligue?locale=fr"
            ]
            
            data = None
            working_url = None
            
            for url in urls_to_try:
                try:
                    response = requests.get(url, headers=headers, timeout=10)
                    if response.status_code == 200:
                        data = response.json()
                        working_url = url
                        break
                except:
                    continue
            
            if not data:
                # Si aucune URL ne fonctionne, essayons une approche différente
                url = "https://www.livescore.in/fr/handball/france/starligue/"
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                'Accept': 'application/json'
            }
            
            response = requests.get(url, headers=headers, timeout=10)
            response.raise_for_status()
            data = response.json()
            
            if 'Stages' in data:
                match_count = sum(len(stage.get('Events', [])) for stage in data.get('Stages', []))
                self.log_test("API Livescore", True, f"{match_count} matchs trouvés")
                
                # Afficher un exemple
                embed = discord.Embed(
                    title="✅ API Livescore Fonctionnelle",
                    description=f"**{match_count} matchs** trouvés dans l'API",
                    color=discord.Color.green()
                )
                
                # Exemple de match
                for stage in data.get('Stages', []):
                    for event in stage.get('Events', [])[:1]:  # Premier match seulement
                        t1 = event.get('T1', [{}])[0].get('Nm', 'Inconnu')
                        t2 = event.get('T2', [{}])[0].get('Nm', 'Inconnu')
                        date = event.get('Esd', 'Date inconnue')
                        embed.add_field(
                            name="Exemple de match",
                            value=f"{t1} vs {t2}\n{date}",
                            inline=False
                        )
                        break
                    break
                
                await ctx.send(embed=embed)
            else:
                self.log_test("API Livescore", False, "Structure inattendue")
                await ctx.send("❌ API Livescore : Structure de données inattendue")
                
        except Exception as e:
            self.log_test("API Livescore", False, str(e))
            await ctx.send(f"❌ Erreur API : {str(e)}")

    @test_group.command(name='db')
    async def test_db(self, ctx):
        """Teste les opérations de base de données."""
        await ctx.send("🗄️ **Test de la base de données...**")
        
        try:
            # Test utilisateur
            test_user_id = 999999999
            database.check_user(test_user_id)
            user_data = database.get_user_data(test_user_id)
            self.log_test("Création utilisateur", user_data is not None)
            
            # Test points
            database.update_points(test_user_id, 100)
            user_data = database.get_user_data(test_user_id)
            self.log_test("Ajout points", user_data['points'] >= 100)
            
            # Test journée
            now = datetime.now(timezone.utc)
            journee_id = database.create_or_update_journee(999, now, now + timedelta(days=7))
            self.log_test("Création journée", journee_id is not None)
            
            # Test match
            match_id = database.create_match(
                journee_id, "TEST999", 999999, "Test1", "Test2", now
            )
            self.log_test("Création match", match_id is not None)
            
            # Nettoyage
            with database.sqlite3.connect(database.DB_NAME) as con:
                cur = con.cursor()
                cur.execute("DELETE FROM matchs WHERE event_id = 'TEST999'")
                cur.execute("DELETE FROM journees WHERE numero = 999")
                cur.execute("DELETE FROM users WHERE user_id = ?", (test_user_id,))
                con.commit()
            
            await ctx.send("✅ **Base de données fonctionnelle**")
            
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
            except:
                self.log_test("Fichier cards.json", False, "Fichier manquant")
                await ctx.send("❌ Fichier cards.json introuvable")
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
            
            await ctx.send("✅ **Système de collection fonctionnel**")
            
        except Exception as e:
            self.log_test("Collection", False, str(e))
            await ctx.send(f"❌ Erreur collection : {str(e)}")

    @test_group.command(name='pronostics')
    async def test_pronostics(self, ctx):
        """Teste le système de pronostics."""
        await ctx.send("🎯 **Test du système de pronostics...**")
        
        # Message de pronostic
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
        
        # Ajouter les réactions
        try:
            for emoji in ["1️⃣", "❌", "2️⃣"]:
                await msg.add_reaction(emoji)
                await asyncio.sleep(0.3)
            self.log_test("Réactions pronostics", True)
        except:
            self.log_test("Réactions pronostics", False)
            
        await ctx.send("✅ **Message de pronostic créé avec succès**")

    @test_group.command(name='events')
    async def test_events(self, ctx):
        """Teste la création d'événements Discord."""
        await ctx.send("📅 **Test de création d'événement...**")
        
        try:
            start_time = datetime.now(timezone.utc) + timedelta(hours=2)
            end_time = start_time + timedelta(hours=2)
            
            event = await ctx.guild.create_scheduled_event(
                name="[TEST] Match de handball",
                description="Ceci est un événement de test",
                start_time=start_time,
                end_time=end_time,
                entity_type=discord.EntityType.external,
                location="Test Arena",
                privacy_level=discord.PrivacyLevel.guild_only
            )
            
            self.log_test("Création événement", True, f"ID: {event.id}")
            await ctx.send(f"✅ **Événement créé** (ID: {event.id})")
            
            # Supprimer après 10 secondes
            await asyncio.sleep(10)
            await event.delete()
            await ctx.send("🗑️ Événement de test supprimé")
            
        except discord.Forbidden:
            self.log_test("Création événement", False, "Permissions insuffisantes")
            await ctx.send("❌ Permissions insuffisantes pour créer des événements")
        except Exception as e:
            self.log_test("Création événement", False, str(e))
            await ctx.send(f"❌ Erreur : {str(e)}")

    @test_group.command(name='rss')
    async def test_rss(self, ctx):
        """Teste le flux RSS."""
        await ctx.send("📰 **Test du flux RSS...**")
        
        try:
            import feedparser
            feed = feedparser.parse("https://handnews.fr/feed")
            
            if not feed.bozo:
                self.log_test("Flux RSS", True, f"{len(feed.entries)} articles")
                
                # Afficher le dernier article
                if feed.entries:
                    entry = feed.entries[0]
                    embed = discord.Embed(
                        title=entry.title[:256],
                        url=entry.link,
                        description=entry.summary[:500] if hasattr(entry, 'summary') else "Pas de résumé",
                        color=0xe8874f
                    )
                    embed.set_footer(text="Test RSS Handnews")
                    
                    msg = await ctx.send(embed=embed)
                    self.test_messages.append(msg)
                    
                await ctx.send(f"✅ **RSS fonctionnel** - {len(feed.entries)} articles trouvés")
            else:
                self.log_test("Flux RSS", False, "Flux invalide")
                await ctx.send("❌ Flux RSS invalide")
                
        except Exception as e:
            self.log_test("Flux RSS", False, str(e))
            await ctx.send(f"❌ Erreur RSS : {str(e)}")

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
        
        embed = discord.Embed(
            title="📊 Résumé des Tests",
            description=f"**{success_count}/{total_count}** tests réussis",
            color=discord.Color.green() if success_count == total_count else discord.Color.orange()
        )
        
        # Grouper les résultats
        results_text = "\n".join(self.test_results[:20])  # Limiter à 20 pour l'embed
        if len(self.test_results) > 20:
            results_text += f"\n... et {len(self.test_results) - 20} autres"
            
        embed.add_field(name="Résultats", value=results_text, inline=False)
        
        if success_count == total_count:
            embed.set_footer(text="🎉 Tous les tests sont passés !")
        else:
            embed.set_footer(text="⚠️ Certains tests ont échoué")
            
        await ctx.send(embed=embed)

async def setup(bot):
    """Charge le cog de tests."""
    await bot.add_cog(TestCog(bot))
