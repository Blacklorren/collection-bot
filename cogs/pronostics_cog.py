import discord
from discord.ext import commands, tasks
import database
from datetime import datetime, timezone, timedelta, date
import asyncio
import pytz

# Configuration (inchangée)
PRONO_CHANNEL_ID = 1398822619809185895
ANNONCE_CHANNEL_ID = 1398822619809185895
POINTS_BON_PRONO = 50

# Emojis (inchangés)
EMOJI_VICTOIRE_1 = "1️⃣"
EMOJI_NUL = "❌"
EMOJI_VICTOIRE_2 = "2️⃣"
PRONO_EMOJIS = [EMOJI_VICTOIRE_1, EMOJI_NUL, EMOJI_VICTOIRE_2]
PRONO_MAPPING = { EMOJI_VICTOIRE_1: "1", EMOJI_NUL: "N", EMOJI_VICTOIRE_2: "2" }

class PronosticsCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.check_pronos_closure.start()
        # On remplace les tâches de journée par une tâche hebdomadaire
        self.publish_weekly_summary.start()
        
    def cog_unload(self):
        self.check_pronos_closure.cancel()
        self.publish_weekly_summary.cancel()

    async def create_prono_message(self, channel, match_data):
        """Crée un message de pronostic pour un match (sans la notion de journée)."""
        match_time_paris = match_data['date_match'].astimezone(pytz.timezone('Europe/Paris'))
        
        embed = discord.Embed(
            title=f"🏐 {match_data['equipe1']} vs {match_data['equipe2']}",
            description=(
                f"**Date :** {match_time_paris.strftime('%d/%m/%Y à %H:%M')}\n\n" # Ligne "Journée" supprimée
                "Cliquez sur une réaction pour pronostiquer :\n"
                f"{EMOJI_VICTOIRE_1} = Victoire **{match_data['equipe1']}**\n"
                f"{EMOJI_NUL} = Match nul\n"
                f"{EMOJI_VICTOIRE_2} = Victoire **{match_data['equipe2']}**"
            ),
            color=discord.Color.blue(),
            timestamp=match_data['date_match']
        )
        embed.set_footer(text="Pronostics ouverts jusqu'à 5 min avant le match")
        message = await channel.send(embed=embed)
        for emoji in PRONO_EMOJIS:
            await message.add_reaction(emoji)
        database.save_prono_message(match_data['id'], message.id, channel.id)
        return message

    async def create_pronostic_messages_for_matches(self, matches):
        """Crée les messages de pronostics pour une liste de matchs."""
        channel = self.bot.get_channel(PRONO_CHANNEL_ID)
        if not channel:
            print(f"❌ (PRONOS) Canal de pronostics {PRONO_CHANNEL_ID} introuvable!")
            return
        
        for match in matches:
            if not database.get_prono_message(match['id']):
                await self.create_prono_message(channel, match)
                await asyncio.sleep(1)

    # on_raw_reaction_add et on_raw_reaction_remove restent INCHANGÉS

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload):
                    
        """Gère l'ajout de réactions pour les pronostics."""
        # Ignorer les réactions du bot
        if payload.user_id == self.bot.user.id:
            return
            
        # Vérifier si c'est dans le canal de pronostics
        if payload.channel_id != PRONO_CHANNEL_ID:
            return
                    
        # Si l'emoji n'est pas un emoji de pronostic, on le supprime
        if str(payload.emoji) not in PRONO_EMOJIS:
            try:
                channel = self.bot.get_channel(payload.channel_id)
                message = await channel.fetch_message(payload.message_id)
                # On vérifie que c'est bien un message de prono du bot avant de supprimer
                if message.author.id == self.bot.user.id and message.embeds:
                    await message.remove_reaction(payload.emoji, payload.member)
            except (discord.Forbidden, discord.NotFound):
                pass  
            return 
        
        # Récupérer le match associé au message
        with database.sqlite3.connect(database.DB_NAME) as con:
            cur = con.cursor()
            cur.execute("""
                SELECT m.* FROM matchs m
                JOIN prono_messages pm ON m.id = pm.match_id
                WHERE pm.message_id = ?
            """, (payload.message_id,))
            match = cur.fetchone()
        
        if not match:
            return
            
        match_dict = {
            'id': match[0],
            'pronos_fermes': match[9],
            'date_match': datetime.fromisoformat(match[6])
        }
        
        # Vérifier si les pronostics sont encore ouverts
        if match_dict['pronos_fermes']:
            channel = self.bot.get_channel(payload.channel_id)
            message = await channel.fetch_message(payload.message_id)
            await message.remove_reaction(payload.emoji, payload.member)
            return
        
        # Vérifier si on est à moins de 5 minutes du match
        now_utc = datetime.now(timezone.utc)
        if now_utc >= (match_dict['date_match'] - timedelta(minutes=5)):
            channel = self.bot.get_channel(payload.channel_id)
            message = await channel.fetch_message(payload.message_id)
            await message.remove_reaction(payload.emoji, payload.member)
            try:
                await payload.member.send("❌ Les pronostics sont fermés pour ce match (moins de 5 minutes avant le début).")
            except:
                pass
            return
        
        # Supprimer les autres réactions de l'utilisateur
        channel = self.bot.get_channel(payload.channel_id)
        message = await channel.fetch_message(payload.message_id)
        
        for emoji in PRONO_EMOJIS:
            if str(emoji) != str(payload.emoji):
                try:
                    await message.remove_reaction(emoji, payload.member)
                except:
                    pass
        
        # Enregistrer le pronostic
        pronostic_value = PRONO_MAPPING[str(payload.emoji)]
        database.save_or_update_pronostic(payload.user_id, match_dict['id'], pronostic_value)

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload):
        """Gère la suppression de réactions (annulation de pronostic)."""
        # Ignorer les actions du bot
        if payload.user_id == self.bot.user.id:
            return
            
        # Vérifier si c'est dans le canal de pronostics
        if payload.channel_id != PRONO_CHANNEL_ID:
            return
            
        # Vérifier si c'est un emoji de pronostic
        if str(payload.emoji) not in PRONO_EMOJIS:
            return

    # check_pronos_closure reste INCHANGÉ
    
    @tasks.loop(minutes=1)
    async def check_pronos_closure(self):
        """Vérifie et ferme les pronostics 5 minutes avant chaque match."""
        now_utc = datetime.now(timezone.utc)
        limit_time = now_utc + timedelta(minutes=5)
        
        # Récupérer les matchs à fermer
        with database.sqlite3.connect(database.DB_NAME) as con:
            con.row_factory = database.sqlite3.Row
            cur = con.cursor()
            cur.execute("""
                SELECT m.*, pm.message_id, pm.channel_id 
                FROM matchs m
                JOIN prono_messages pm ON m.id = pm.match_id
                WHERE m.pronos_fermes = 0 
                AND m.date_match <= ?
            """, (limit_time,))
            matches_to_close = cur.fetchall()
        
        for match in matches_to_close:
            try:
                # Fermer les pronostics dans la DB
                database.close_match_pronostics(match['id'])
                
                # Mettre à jour le message Discord
                channel = self.bot.get_channel(match['channel_id'])
                if channel:
                    message = await channel.fetch_message(match['message_id'])
                    
                    # Modifier l'embed
                    embed = message.embeds[0]
                    embed.color = discord.Color.red()
                    embed.set_footer(text="⛔ Pronostics fermés")
                    
                    await message.edit(embed=embed)
                    
                print(f"✅ (PRONOS) Pronostics fermés pour {match['equipe1']} vs {match['equipe2']}")
                
            except Exception as e:
                print(f"❌ (PRONOS) Erreur fermeture pronostics match {match['id']}: {e}")

    # NOUVELLE TÂCHE pour le résumé hebdomadaire
    @tasks.loop(hours=1)
    async def publish_weekly_summary(self):
        """Vérifie chaque heure si on est lundi matin pour publier les classements."""
        now = datetime.now(pytz.timezone('Europe/Paris'))
        # On exécute le lundi à 10h du matin
        if now.weekday() == 0 and now.hour == 10:
            print("📅 (PRONOS) Lundi 10h, génération des classements...")
            
            # --- Préparation de l'embed principal ---
            embed = discord.Embed(
                title="🏆 Bilan des Pronostics 🏆",
                description="Voici le résumé des performances de la semaine passée et le classement général à jour.",
                color=discord.Color.gold(),
                timestamp=datetime.now(timezone.utc)
            )

            # --- 1. CLASSEMENT HEBDOMADAIRE ---
            today = date.today()
            last_week_day = today - timedelta(days=7)
            start, end = database.get_week_dates(last_week_day)
            matches = database.get_matches_in_date_range(start, end)
            
            if not matches or not all(m['resultat'] for m in matches):
                embed.add_field(
                    name=f"Classement de la Semaine ({start.strftime('%d/%m')} au {end.strftime('%d/%m')})",
                    value="Aucun match terminé la semaine dernière pour établir un classement.",
                    inline=False
                )
            else:
                match_ids = [m['id'] for m in matches]
                weekly_leaderboard = database.get_leaderboard_for_matches(match_ids)
                
                weekly_text = "Aucun pronostic correct la semaine dernière."
                if weekly_leaderboard:
                    weekly_text = ""
                    for rank, row in enumerate(weekly_leaderboard[:10], 1):
                        user = self.bot.get_user(row['user_id'])
                        name = user.display_name if user else f"Utilisateur {row['user_id']}"
                        emoji = "🥇" if rank == 1 else "🥈" if rank == 2 else "🥉" if rank == 3 else f"**#{rank}**"
                        weekly_text += f"{emoji} **{name}** - {row['bons_pronos']}/{len(matches)} ({row['total_points']} pts)\n"
                
                embed.add_field(
                    name=f"🏆 Top 10 de la Semaine ({start.strftime('%d/%m')} - {end.strftime('%d/%m')})",
                    value=weekly_text,
                    inline=False
                )

            # --- 2. CLASSEMENT GÉNÉRAL ---
            general_leaderboard = database.get_general_leaderboard(POINTS_BON_PRONO, limit=10)
            
            general_text = "Aucun pronostic correct enregistré pour le moment."
            if general_leaderboard:
                general_text = ""
                for rank, row in enumerate(general_leaderboard, 1):
                    user = self.bot.get_user(row['user_id'])
                    name = user.display_name if user else f"Utilisateur {row['user_id']}"
                    emoji = "👑" if rank == 1 else "🥈" if rank == 2 else "🥉" if rank == 3 else f"**#{rank}**"
                    general_text += f"{emoji} **{name}** - {row['total_points']} pts ({row['bons_pronos']} corrects)\n"

            embed.add_field(
                name="👑 Classement Général (Top 10) 👑",
                value=general_text,
                inline=False
            )

            # --- Envoi final ---
            embed.set_footer(text="Handnews Pronostics - Rendez-vous la semaine prochaine !")
            channel = self.bot.get_channel(ANNONCE_CHANNEL_ID)
            if channel:
                await channel.send(embed=embed)
                print("✅ (PRONOS) Classements hebdomadaire et général publiés.")

    @check_pronos_closure.before_loop
    @publish_weekly_summary.before_loop
    async def before_loops(self):
        """Attend que le bot soit prêt avant de démarrer les boucles."""
        await self.bot.wait_until_ready()

    # process_match_result reste INCHANGÉ
    
    def process_match_result(self, match_id, score_str):
        """Traite le résultat d'un match et attribue les points."""
        try:
            # Parser le score (format: "25-23")
            score_parts = score_str.split('-')
            if len(score_parts) != 2:
                return None
                
            score1 = int(score_parts[0])
            score2 = int(score_parts[1])
            
            # Déterminer le résultat
            if score1 > score2:
                resultat = "1"
            elif score1 < score2:
                resultat = "2"
            else:
                resultat = "N"
            
            # Mettre à jour le match
            database.update_match_result(match_id, resultat, score_str)
            
            # Attribuer les points
            database.attribute_points_for_match(match_id, resultat, POINTS_BON_PRONO)
            
            return resultat
            
        except Exception as e:
            print(f"❌ (PRONOS) Erreur traitement résultat: {e}")
            return None

    # NOUVELLES COMMANDES basées sur la semaine
    
    @commands.command(name='pronos')
    @commands.has_permissions(manage_guild=True)
    async def pronos_stats(self, ctx):
        """Commande admin pour voir les statistiques des pronostics de la semaine en cours."""
        start, end = database.get_week_dates(date.today())
        matchs = database.get_matches_in_date_range(start, end)
        
        if not matchs:
            await ctx.send("Aucun match programmé pour la semaine en cours.", ephemeral=True)
            return

        embed = discord.Embed(
            title=f"📊 Statistiques - Semaine du {start.strftime('%d/%m')}",
            color=discord.Color.blue()
        )
        
        for match in matchs:
            pronos = database.get_match_pronostics(match['id'])
            pronos_count = {"1": 0, "N": 0, "2": 0}
            for _, prono in pronos:
                pronos_count[prono] += 1
            
            total = sum(pronos_count.values())
            
            # --- BLOC CORRIGÉ ---
            if total == 0:
                field_value = "Aucun pronostic pour ce match."
            else:
                # Calculer les pourcentages en amont pour plus de clarté
                p1 = (pronos_count['1'] / total) * 100
                pN = (pronos_count['N'] / total) * 100
                p2 = (pronos_count['2'] / total) * 100
                
                # Construire la chaîne de caractères complète
                field_value = (
                    f"Total: {total} pronostics\n"
                    f"{EMOJI_VICTOIRE_1} {match['equipe1']}: **{pronos_count['1']}** ({p1:.1f}%)\n"
                    f"{EMOJI_NUL} Nul: **{pronos_count['N']}** ({pN:.1f}%)\n"
                    f"{EMOJI_VICTOIRE_2} {match['equipe2']}: **{pronos_count['2']}** ({p2:.1f}%)"
                )
            
            embed.add_field(
                name=f"{match['equipe1']} vs {match['equipe2']}",
                value=field_value,
                inline=False
            )
        
        await ctx.send(embed=embed, ephemeral=True)

    @commands.command(name='classement')
    @commands.has_permissions(manage_guild=True)
    async def classement_command(self, ctx):
        """Affiche le classement provisoire de la semaine en cours."""
        start, end = database.get_week_dates(date.today())
        all_matches_this_week = database.get_matches_in_date_range(start, end)
        
        if not all_matches_this_week:
            await ctx.send("Aucun match programmé pour la semaine en cours.", ephemeral=True)
            return

        # --- CORRECTION APPLIQUÉE ICI ---
        # Filtrer uniquement les matchs qui ont un résultat en utilisant la bonne syntaxe
        finished_matches = [m for m in all_matches_this_week if m['resultat'] is not None]

        if not finished_matches:
            await ctx.send("Aucun match n'est encore terminé cette semaine pour établir un classement.", ephemeral=True)
            return

        # Utiliser uniquement les IDs des matchs terminés pour le classement
        match_ids = [m['id'] for m in finished_matches]
        leaderboard = database.get_leaderboard_for_matches(match_ids)
        
        if not leaderboard:
            await ctx.send("Aucun pronostic correct pour les matchs terminés de la semaine.", ephemeral=True)
            return

        embed = discord.Embed(
            title=f"🏆 Classement Provisoire de la Semaine",
            description=f"Basé sur les matchs du {start.strftime('%d/%m')} au {end.strftime('%d/%m')}",
            color=discord.Color.gold(),
            timestamp=datetime.now(timezone.utc)
        )
        
        classement_text = ""
        for rank, row in enumerate(leaderboard[:10], 1):
            user_id, bons_pronos, total_points = row['user_id'], row['bons_pronos'], row['total_points']
            member = ctx.guild.get_member(user_id)
            member_name = member.display_name if member else f"Utilisateur Inconnu ({user_id})"
            
            emoji = "🥇" if rank == 1 else "🥈" if rank == 2 else "🥉" if rank == 3 else f"**#{rank}**"
            # Correction : Utiliser la longueur de la liste des matchs terminés
            classement_text += f"{emoji} **{member_name}** - {bons_pronos}/{len(finished_matches)} ({total_points} pts)\n"
        
        embed.add_field(name="Top 10 Provisoire", value=classement_text, inline=False)
        await ctx.send(embed=embed)

    @commands.command(name='pendingmatches')
    @commands.has_permissions(manage_guild=True)
    async def pending_matches_command(self, ctx):
        """Affiche les matchs terminés qui n'ont pas encore de résultat enregistré."""
        # On regarde les matchs des 10 derniers jours pour ne pas surcharger
        since_date = datetime.now(timezone.utc) - timedelta(days=10)
        
        with database.sqlite3.connect(database.DB_NAME) as con:
            con.row_factory = database.sqlite3.Row
            cur = con.cursor()
            cur.execute("""
                SELECT id, equipe1, equipe2, date_match FROM matchs
                WHERE resultat IS NULL AND date_match >= ?
                ORDER BY date_match ASC
            """, (since_date.isoformat(),))
            matches_pending = cur.fetchall()

        if not matches_pending:
            await ctx.send("✅ Aucun match en attente de résultat dans les 10 derniers jours.", ephemeral=True)
            return

        description = "Voici les matchs dont le résultat n'a pas encore été traité. Utilisez `!setresult <ID> <score1>-<score2>`.\n\n"
        for match in matches_pending:
            match_time = datetime.fromisoformat(match['date_match']).astimezone(pytz.timezone('Europe/Paris'))
            description += f"**ID: {match['id']}** - {match['equipe1']} vs {match['equipe2']} _(le {match_time.strftime('%d/%m')})_\n"

        embed = discord.Embed(
            title="⏳ Matchs en Attente de Résultat",
            description=description,
            color=discord.Color.orange()
        )
        await ctx.send(embed=embed, ephemeral=True)

    @commands.command(name='setresult')
    @commands.has_permissions(manage_guild=True)
    async def set_result_command(self, ctx, match_id: int, score: str):
        """[Admin] Force le résultat d'un match et attribue les points."""
        try:
            # On réutilise la logique existante pour traiter le résultat !
            # C'est la meilleure façon de garantir la cohérence.
            resultat = self.process_match_result(match_id, score)
            
            if resultat:
                await ctx.send(f"✅ Résultat `{score}` enregistré pour le match ID `{match_id}`. Les points ont été attribués.", ephemeral=True)
                # On peut maintenant lancer une vérification du classement
                await self.classement_command.callback(self, ctx)
            else:
                await ctx.send(f"❌ Le format du score `{score}` est invalide. Utilisez le format `25-23`.", ephemeral=True)

        except Exception as e:
            await ctx.send(f"❌ Une erreur est survenue. L'ID de match `{match_id}` est-il correct ? Erreur: `{e}`", ephemeral=True)
            
    # --- COMMANDE CORRIGÉE ---
    @commands.command(name='userpronos')
    @commands.has_permissions(manage_guild=True)
    async def user_pronos_command(self, ctx, membre: discord.Member):
        """[Admin] Affiche les pronostics corrects d'un utilisateur et les points gagnés."""
        try:
            correct_pronos = database.get_user_correct_pronostics(membre.id)
            
            if not correct_pronos:
                await ctx.send(f"Cet utilisateur n'a aucun pronostic correct enregistré.", ephemeral=True)
                return
            
            # On calcule le total des points en se basant sur la constante
            total_points = len(correct_pronos) * POINTS_BON_PRONO
            
            embed = discord.Embed(
                title=f"✅ Pronostics Corrects pour {membre.display_name}",
                description=f"**Total :** {len(correct_pronos)} pronostics corrects / **{total_points}** points gagnés.",
                color=discord.Color.green()
            )
            
            description_text = ""
            # On limite l'affichage aux 15 plus récents pour éviter de dépasser les limites de Discord
            for prono in correct_pronos[:15]:
                match_time = datetime.fromisoformat(prono['date_match']).astimezone(pytz.timezone('Europe/Paris'))
                
                # Traduire le résultat en emoji pour la lisibilité
                result_emoji = "❓"
                if prono['resultat'] == '1': result_emoji = EMOJI_VICTOIRE_1
                elif prono['resultat'] == 'N': result_emoji = EMOJI_NUL
                elif prono['resultat'] == '2': result_emoji = EMOJI_VICTOIRE_2

                description_text += (
                    f"**{prono['equipe1']} vs {prono['equipe2']}** ({match_time.strftime('%d/%m')})\n"
                    # On affiche la constante de points au lieu de chercher dans la BDD
                    f"↳ Résultat: {result_emoji} | Points gagnés: **{POINTS_BON_PRONO}**\n"
                )
                
            embed.add_field(name="Détails des 15 derniers pronostics corrects", value=description_text, inline=False)
            
            await ctx.send(embed=embed, ephemeral=True)
            
        except Exception as e:
            await ctx.send(f"❌ Une erreur est survenue lors de la récupération des pronostics : `{e}`", ephemeral=True)

    @commands.command(name='classementgeneral', aliases=['cg'])
    @commands.has_permissions(manage_guild=True)
    async def classement_general_command(self, ctx, limit: int = 10):
        """[Admin] Affiche publiquement le classement général des pronostics."""
        
        # On limite l'affichage à 25 pour ne pas surcharger Discord.
        if limit > 25:
            limit = 25
            
        try:
            # Supprime la commande pour garder le salon propre
            await ctx.message.delete() 
        except discord.Forbidden:
            pass # Le bot n'a pas la perm, on continue quand même

        leaderboard = database.get_general_leaderboard(POINTS_BON_PRONO, limit=limit)
        
        if not leaderboard:
            await ctx.send("Aucun pronostic correct n'a été enregistré pour le moment.", ephemeral=True)
            return

        embed = discord.Embed(
            title=f"👑 Classement Général des Pronostics (Top {limit}) 👑",
            description="Classement basé sur tous les matchs terminés.",
            color=discord.Color.dark_gold(),
            timestamp=datetime.now(timezone.utc)
        )
        
        classement_text = ""
        for rank, row in enumerate(leaderboard, 1):
            user_id, bons_pronos, total_points = row['user_id'], row['bons_pronos'], row['total_points']
            member = ctx.guild.get_member(user_id)
            member_name = member.display_name if member else f"Utilisateur Inconnu ({user_id})"
            
            emoji = "🥇" if rank == 1 else "🥈" if rank == 2 else "🥉" if rank == 3 else f"**#{rank}**"
            classement_text += f"{emoji} **{member_name}** - **{total_points}** pts ({bons_pronos} corrects)\n"
        
        embed.add_field(name="Classement", value=classement_text, inline=False)
        
        # Envoi public dans le canal où la commande a été tapée
        await ctx.send(embed=embed)


async def setup(bot):
    await bot.add_cog(PronosticsCog(bot))
