import discord
from discord.ext import commands, tasks
import database
from datetime import datetime, timezone, timedelta
import asyncio
import pytz

# Configuration
PRONO_CHANNEL_ID = 1398822619809185895  # À remplacer par l'ID du canal de pronostics
ANNONCE_CHANNEL_ID = 1398822619809185895  # À remplacer par l'ID du canal d'annonces
POINTS_BON_PRONO = 50

# Emojis pour les pronostics
EMOJI_VICTOIRE_1 = "1️⃣"
EMOJI_NUL = "❌"
EMOJI_VICTOIRE_2 = "2️⃣"

PRONO_EMOJIS = [EMOJI_VICTOIRE_1, EMOJI_NUL, EMOJI_VICTOIRE_2]
PRONO_MAPPING = {
    EMOJI_VICTOIRE_1: "1",
    EMOJI_NUL: "N",
    EMOJI_VICTOIRE_2: "2"
}

class PronosticsCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.check_pronos_closure.start()
        self.check_journee_rappel.start()
        self.check_journee_complete.start()
        
    def cog_unload(self):
        """Arrête les tâches lors du déchargement du cog."""
        self.check_pronos_closure.cancel()
        self.check_journee_rappel.cancel()
        self.check_journee_complete.cancel()

    async def create_prono_message(self, channel, match_data):
        """Crée un message de pronostic pour un match."""
        match_time_paris = match_data['date_match'].astimezone(pytz.timezone('Europe/Paris'))
        
        embed = discord.Embed(
            title=f"🏐 {match_data['equipe1']} vs {match_data['equipe2']}",
            description=(
                f"**Date :** {match_time_paris.strftime('%d/%m/%Y à %H:%M')}\n"
                f"**Journée :** {match_data['journee_numero']}\n\n"
                "Cliquez sur une réaction pour pronostiquer :\n"
                f"{EMOJI_VICTOIRE_1} = Victoire **{match_data['equipe1']}**\n"
                f"{EMOJI_NUL} = Match nul\n"
                f"{EMOJI_VICTOIRE_2} = Victoire **{match_data['equipe2']}**"
            ),
            color=discord.Color.blue(),
            timestamp=match_data['date_match']
        )
        
        embed.set_footer(text="Pronostics ouverts jusqu'à 5 min avant le match")
        
        # Envoyer le message
        message = await channel.send(embed=embed)
        
        # Ajouter les réactions
        for emoji in PRONO_EMOJIS:
            await message.add_reaction(emoji)
        
        # Sauvegarder le message dans la DB
        database.save_prono_message(match_data['id'], message.id, channel.id)
        
        return message

    async def create_pronostic_messages_for_matches(self, matches):
        """Crée les messages de pronostics pour une liste de matchs."""
        if not PRONO_CHANNEL_ID:
            print("❌ (PRONOS) ID du canal de pronostics non configuré!")
            return
            
        channel = self.bot.get_channel(PRONO_CHANNEL_ID)
        if not channel:
            print(f"❌ (PRONOS) Canal de pronostics {PRONO_CHANNEL_ID} introuvable!")
            return
        
        for match in matches:
            # Vérifier si un message existe déjà
            existing_message = database.get_prono_message(match['id'])
            if not existing_message:
                await self.create_prono_message(channel, match)
                await asyncio.sleep(1)  # Éviter le rate limit

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
        
        # Pour l'instant, on ne fait rien lors de la suppression
        # Le pronostic reste enregistré jusqu'à ce qu'une nouvelle réaction soit ajoutée

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
                    
                    # Optionnel : Supprimer toutes les réactions
                    # await message.clear_reactions()
                    
                print(f"✅ (PRONOS) Pronostics fermés pour {match['equipe1']} vs {match['equipe2']}")
                
            except Exception as e:
                print(f"❌ (PRONOS) Erreur fermeture pronostics match {match['id']}: {e}")

    @tasks.loop(hours=1)
    async def check_journee_rappel(self):
        """Envoie un rappel 24h avant le début de chaque journée."""
        if not ANNONCE_CHANNEL_ID:
            return
            
        journees_a_rappeler = database.get_journees_for_rappel()
        
        for journee in journees_a_rappeler:
            try:
                # Récupérer les matchs de la journée
                matchs = database.get_matchs_journee(journee['id'])
                if not matchs:
                    continue
                
                # Créer l'embed de rappel
                embed = discord.Embed(
                    title=f"🔔 Rappel - Journée {journee['numero']} dans 24h !",
                    description=(
                        f"N'oubliez pas de faire vos pronostics pour la **Journée {journee['numero']}** !\n\n"
                        f"**{len(matchs)} matchs** vous attendent dans <#{PRONO_CHANNEL_ID}>.\n\n"
                        "Les pronostics fermeront 5 minutes avant chaque match."
                    ),
                    color=discord.Color.orange(),
                    timestamp=datetime.now(timezone.utc)
                )
                
                # Liste des matchs
                match_list = ""
                for match in matchs[:5]:  # Limiter à 5 matchs pour l'embed
                    match_time = datetime.fromisoformat(match['date_match']).astimezone(pytz.timezone('Europe/Paris'))
                    match_list += f"• {match['equipe1']} vs {match['equipe2']} - {match_time.strftime('%d/%m à %H:%M')}\n"
                
                if len(matchs) > 5:
                    match_list += f"... et {len(matchs) - 5} autres matchs"
                
                embed.add_field(name="📅 Programme", value=match_list, inline=False)
                embed.set_footer(text="Handnews Pronostics")
                
                # Envoyer le rappel
                channel = self.bot.get_channel(ANNONCE_CHANNEL_ID)
                if channel:
                    await channel.send(embed=embed)
                    
                # Marquer le rappel comme envoyé
                database.mark_journee_rappel_sent(journee['id'])
                print(f"✅ (PRONOS) Rappel envoyé pour la journée {journee['numero']}")
                
            except Exception as e:
                print(f"❌ (PRONOS) Erreur envoi rappel journée {journee['id']}: {e}")

    @tasks.loop(hours=2)
    async def check_journee_complete(self):
        """Vérifie si une journée est complète et publie le classement."""
        # Récupérer la journée active
        journee = database.get_active_journee()
        if not journee:
            return
            
        # Vérifier si tous les matchs ont un résultat
        matchs = database.get_matchs_journee(journee['id'])
        if not matchs:
            return
            
        all_finished = all(match['resultat'] is not None for match in matchs)
        
        if all_finished and PRONO_CHANNEL_ID:
            # Récupérer le classement
            leaderboard = database.get_journee_leaderboard(journee['id'])
            
            if leaderboard:
                # Créer l'embed du classement
                embed = discord.Embed(
                    title=f"🏆 Classement - Journée {journee['numero']}",
                    description="Voici les meilleurs pronostiqueurs de cette journée !",
                    color=discord.Color.gold(),
                    timestamp=datetime.now(timezone.utc)
                )
                
                # Formatter le classement
                classement_text = ""
                for rank, (user_id, bons_pronos, total_points) in enumerate(leaderboard[:10], 1):
                    member = self.bot.get_user(user_id)
                    member_name = member.display_name if member else f"Utilisateur {user_id}"
                    
                    emoji = ""
                    if rank == 1: emoji = "🥇"
                    elif rank == 2: emoji = "🥈"
                    elif rank == 3: emoji = "🥉"
                    else: emoji = f"**#{rank}**"
                    
                    classement_text += f"{emoji} **{member_name}** - {bons_pronos}/{len(matchs)} ({total_points} pts)\n"
                
                embed.add_field(name="Top 10", value=classement_text or "Aucun pronostic enregistré", inline=False)
                
                # Statistiques générales
                total_pronos = sum(1 for _ in database.get_match_pronostics(matchs[0]['id']))
                embed.add_field(
                    name="📊 Statistiques",
                    value=f"**Participants :** {len(leaderboard)}\n**Matchs :** {len(matchs)}",
                    inline=False
                )
                
                embed.set_footer(text="Handnews Pronostics - Prochain rappel 24h avant la prochaine journée")
                
                # Envoyer le classement
                channel = self.bot.get_channel(PRONO_CHANNEL_ID)
                if channel:
                    await channel.send(embed=embed)
                    print(f"✅ (PRONOS) Classement publié pour la journée {journee['numero']}")
                
                # Désactiver la journée
                with database.sqlite3.connect(database.DB_NAME) as con:
                    cur = con.cursor()
                    cur.execute("UPDATE journees SET is_active = 0 WHERE id = ?", (journee['id'],))
                    con.commit()

    @check_pronos_closure.before_loop
    @check_journee_rappel.before_loop
    @check_journee_complete.before_loop
    async def before_loops(self):
        """Attend que le bot soit prêt avant de démarrer les boucles."""
        await self.bot.wait_until_ready()

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

    @commands.command(name='pronos')
    @commands.has_permissions(manage_guild=True)
    async def pronos_stats(self, ctx):
        """Commande admin pour voir les statistiques des pronostics."""
        journee = database.get_active_journee()
        if not journee:
            await ctx.send("Aucune journée active.", ephemeral=True)
            return
            
        matchs = database.get_matchs_journee(journee['id'])
        
        embed = discord.Embed(
            title=f"📊 Statistiques - Journée {journee['numero']}",
            color=discord.Color.blue()
        )
        
        for match in matchs:
            pronos = database.get_match_pronostics(match['id'])
            pronos_count = {"1": 0, "N": 0, "2": 0}
            
            for _, prono in pronos:
                pronos_count[prono] += 1
            
            total = sum(pronos_count.values())
            
            field_value = (
                f"Total: {total} pronostics\n"
                f"{EMOJI_VICTOIRE_1} {match['equipe1']}: {pronos_count['1']} "
                f"({pronos_count['1']/total*100:.1f}%)\n" if total > 0 else "0%\n"
                f"{EMOJI_NUL} Nul: {pronos_count['N']} "
                f"({pronos_count['N']/total*100:.1f}%)\n" if total > 0 else "0%\n"
                f"{EMOJI_VICTOIRE_2} {match['equipe2']}: {pronos_count['2']} "
                f"({pronos_count['2']/total*100:.1f}%)" if total > 0 else "0%"
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
        """Affiche le classement provisoire de la journée de pronostics en cours."""
        
        # 1. Récupérer la journée active
        journee = database.get_active_journee()
        if not journee:
            await ctx.send("Il n'y a aucune journée de pronostics active en ce moment.", ephemeral=True)
            return
            
        # 2. Récupérer les données du classement pour cette journée
        leaderboard = database.get_journee_leaderboard(journee['id'])
        
        if not leaderboard:
            await ctx.send(f"Aucun pronostic n'a encore été enregistré pour la Journée {journee['numero']}.", ephemeral=True)
            return

        # 3. Récupérer le nombre total de matchs de la journée pour l'affichage (ex: 5/8)
        matchs = database.get_matchs_journee(journee['id'])
        total_matchs = len(matchs) if matchs else 0
            
        # 4. Créer et formater l'embed
        embed = discord.Embed(
            title=f"🏆 Classement Provisoire - Journée {journee['numero']}",
            description="Voici le classement actuel des pronostics pour la journée en cours. Les scores sont mis à jour après chaque résultat de match.",
            color=discord.Color.gold(),
            timestamp=datetime.now(timezone.utc)
        )
        
        classement_text = ""
        # Limiter l'affichage au top 10
        for rank, (user_id, bons_pronos, total_points) in enumerate(leaderboard[:10], 1):
            # Essayer de trouver le membre sur le serveur pour avoir son pseudo actuel
            member = ctx.guild.get_member(user_id)
            member_name = member.display_name if member else f"Utilisateur Inconnu ({user_id})"
            
            emoji = ""
            if rank == 1: emoji = "🥇"
            elif rank == 2: emoji = "🥈"
            elif rank == 3: emoji = "🥉"
            else: emoji = f"**#{rank}**"
            
            # Afficher le score sous la forme "bons pronos / total matchs (points)"
            classement_text += f"{emoji} **{member_name}** - {bons_pronos}/{total_matchs} ({total_points} pts)\n"
        
        if not classement_text:
            classement_text = "Aucun pronostic correct pour le moment."

        embed.add_field(name="Top 10 Provisoire", value=classement_text, inline=False)
        
        embed.set_footer(text=f"Basé sur {len(leaderboard)} participant(s).")
        
        # 5. Envoyer le message
        await ctx.send(embed=embed)        

async def setup(bot):
    """Fonction requise par discord.py pour charger le Cog."""
    await bot.add_cog(PronosticsCog(bot))
