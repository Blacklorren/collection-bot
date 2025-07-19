import discord
from discord.ext import commands
import database
import json
import random
import datetime

# --- CONFIGURATION ---
# Remplacez 0 par l'ID du canal où les annonces de cartes rares seront postées.
# Pour obtenir l'ID : Clic droit sur le canal -> "Copier l'ID du salon" (Mode développeur doit être activé dans Discord)
ANNONCE_CHANNEL_ID = 0 
PACK_COST = 150  # Coût en points pour acheter un pack
DAILY_REWARD = 100 # Points donnés par la commande !daily
POINTS_PER_MESSAGE = 5 # Points donnés par message (avec cooldown)
MESSAGE_COOLDOWN = 60 # Temps en secondes avant de pouvoir regagner des points par message

# Fonction pour charger les données des cartes depuis le fichier JSON
def load_cards_data():
    with open('cards.json', 'r', encoding='utf-8') as f:
        return json.load(f)

class CollectionCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.all_cards = load_cards_data()

        # Prépare les listes de cartes par rareté pour un accès facile
        self.cards_by_rarity = {
            "Commun": [c for c in self.all_cards if c['rarete'] == 'Commun'],
            "Peu Commun": [c for c in self.all_cards if c['rarete'] == 'Peu Commun'],
            "Rare": [c for c in self.all_cards if c['rarete'] == 'Rare'],
            "Épique": [c for c in self.all_cards if c['rarete'] == 'Épique'],
            "Légendaire": [c for c in self.all_cards if c['rarete'] == 'Légendaire']
        }

        # Permet de retrouver une carte par son ID
        self.card_map = {card['id']: card for card in self.all_cards}

    # === ÉVÉNEMENTS ===
    @commands.Cog.listener()
    async def on_member_join(self, member):
        """Donne un pack de bienvenue à un nouveau membre."""
        if member.bot:
            return
        database.add_pack(member.id, 1)
        try:
            await member.send(f"👋 Bienvenue sur le serveur de Handnews, {member.mention} ! Nous t'avons offert un **pack de bienvenue**. Fais `!ouvrir` dans un des salons pour découvrir tes premières cartes !")
        except discord.errors.Forbidden:
            print(f"Impossible d'envoyer un MP à {member.name}.")

    @commands.Cog.listener()
    async def on_message(self, message):
        """Donne des points quand un utilisateur envoie un message (avec cooldown)."""
        if message.author.bot or message.content.startswith('!') or not message.guild:
            return

        user_id = message.author.id
        _, _, _, last_message_time_str = database.get_user_data(user_id)

        now = datetime.datetime.now()

        if last_message_time_str:
            last_message_time = datetime.datetime.fromisoformat(last_message_time_str)
            if (now - last_message_time).total_seconds() < MESSAGE_COOLDOWN:
                return # Cooldown n'est pas terminé

        database.update_points(user_id, POINTS_PER_MESSAGE)
        database.update_last_message_time(user_id)

    # === COMMANDES ===
    @commands.command(name='aide')
    async def help_command(self, ctx):
        """Affiche la liste des commandes."""
        embed = discord.Embed(
            title="📜 Aide - Bot de Collection Handnews",
            description="Voici la liste des commandes pour gérer ta collection de cartes.",
            color=discord.Color.blue()
        )
        embed.add_field(name="`!collection`", value="Affiche toutes les cartes que tu possèdes, triées par club.", inline=False)
        embed.add_field(name="`!points`", value="Consulte ton solde de points actuel.", inline=False)
        embed.add_field(name="`!daily`", value=f"Récupère ta récompense journalière de **{DAILY_REWARD} points**.", inline=False)
        embed.add_field(name="`!pack`", value=f"Achète un pack de cartes pour **{PACK_COST} points**.", inline=False)
        embed.add_field(name="`!ouvrir`", value="Ouvre un pack pour recevoir de nouvelles cartes.", inline=False)
        await ctx.send(embed=embed)


    @commands.command(name='points')
    async def points_command(self, ctx):
        """Affiche le solde de points de l'utilisateur."""
        points, packs, _, _ = database.get_user_data(ctx.author.id)
        await ctx.send(f"💰 {ctx.author.mention}, tu as actuellement **{points} points** et **{packs} pack(s)** à ouvrir.", ephemeral=True)

    @commands.command(name='daily')
    async def daily_command(self, ctx):
        """Permet de récupérer une récompense journalière."""
        user_id = ctx.author.id
        _, _, last_daily_str, _ = database.get_user_data(user_id)
        today_str = datetime.date.today().isoformat()

        if last_daily_str == today_str:
            await ctx.send(f"⏳ {ctx.author.mention}, tu as déjà récupéré ta récompense aujourd'hui. Reviens demain !", ephemeral=True)
        else:
            database.update_points(user_id, DAILY_REWARD)
            database.set_daily_claimed(user_id)
            await ctx.send(f"🎉 {ctx.author.mention}, tu as reçu tes **{DAILY_REWARD} points** quotidiens !", ephemeral=True)

    @commands.command(name='pack')
    async def pack_command(self, ctx):
        """Achète un pack de cartes."""
        user_id = ctx.author.id
        points, _, _, _ = database.get_user_data(user_id)

        if points >= PACK_COST:
            database.update_points(user_id, -PACK_COST)
            database.add_pack(user_id, 1)
            await ctx.send(f"🛍️ {ctx.author.mention}, tu as acheté un pack pour **{PACK_COST} points** ! Fais `!ouvrir` pour l'ouvrir.", ephemeral=True)
        else:
            await ctx.send(f"❌ {ctx.author.mention}, tu n'as pas assez de points. Il te manque **{PACK_COST - points} points**.", ephemeral=True)

    @commands.command(name='ouvrir')
    async def open_command(self, ctx):
        """Ouvre un pack et révèle les cartes obtenues."""
        user_id = ctx.author.id
        _, packs, _, _ = database.get_user_data(user_id)

        if packs <= 0:
            await ctx.send(f"Tu n'as pas de pack à ouvrir. Fais `!pack` pour en acheter un.", ephemeral=True)
            return

        database.remove_pack(user_id, 1)

        # Logique de tirage des cartes
        cartes_obtenues = []
        # Carte 1: 100% Commun
        cartes_obtenues.append(random.choice(self.cards_by_rarity["Commun"]))
        # Carte 2: 65% Commun / 35% Peu Commun
        cartes_obtenues.append(random.choices(
            population=[*self.cards_by_rarity["Commun"], *self.cards_by_rarity["Peu Commun"]],
            weights=[65]*len(self.cards_by_rarity["Commun"]) + [35]*len(self.cards_by_rarity["Peu Commun"]),
            k=1
        )[0])
        # Carte 3: 60% Rare / 30% Épique / 10% Légendaire
        cartes_obtenues.append(random.choices(
            population=[*self.cards_by_rarity["Rare"], *self.cards_by_rarity["Épique"], *self.cards_by_rarity["Légendaire"]],
            weights=[60]*len(self.cards_by_rarity["Rare"]) + [30]*len(self.cards_by_rarity["Épique"]) + [10]*len(self.cards_by_rarity["Légendaire"]),
            k=1
        )[0])

        # Création de l'embed pour afficher les résultats
        embed = discord.Embed(title=f"🎁 Contenu de ton pack, {ctx.author.name} !", color=discord.Color.gold())

        annonce_publique = None

        for carte in cartes_obtenues:
            database.add_card_to_collection(user_id, carte['id'])
            embed.add_field(name=f"**{carte['nom']}** ({carte['rarete']})", value=f"*{carte['club']}*", inline=False)

            # Si une carte Épique ou Légendaire est tirée, on prépare une annonce
            if carte['rarete'] in ["Épique", "Légendaire"]:
                annonce_embed = discord.Embed(
                    title=f"✨ Tirage Exceptionnel ! ✨",
                    description=f"**{ctx.author.mention}** vient d'obtenir **{carte['nom']} ({carte['rarete']})** dans un pack !",
                    color=discord.Color.orange() if carte['rarete'] == "Épique" else discord.Color.purple()
                )
                annonce_embed.set_thumbnail(url=carte['image_url'])
                annonce_publique = annonce_embed

        # On prend la miniature de la carte la plus rare pour l'embed principal
        carte_plus_rare = max(cartes_obtenues, key=lambda c: list(self.cards_by_rarity.keys()).index(c['rarete']))
        embed.set_thumbnail(url=carte_plus_rare['image_url'])

        await ctx.send(embed=embed, ephemeral=True)

        # Envoi de l'annonce publique si une carte rare a été trouvée et si le canal est configuré
        if annonce_publique and ANNONCE_CHANNEL_ID != 441234583372038153:
            channel = self.bot.get_channel(ANNONCE_CHANNEL_ID)
            if channel:
                await channel.send(embed=annonce_publique)

    @commands.command(name='collection')
    async def collection_command(self, ctx):
        """Affiche la collection de l'utilisateur, groupée par club."""
        user_id = ctx.author.id
        card_ids = database.get_user_collection(user_id)

        if not card_ids:
            await ctx.send("Ta collection est vide pour le moment. Ouvre des packs pour la commencer !", ephemeral=True)
            return

        collection = {}
        for card_id in card_ids:
            card = self.card_map.get(card_id)
            if card:
                club = card['club']
                if club not in collection:
                    collection[club] = []
                collection[club].append(f"**{card['nom']}** ({card['rarete']})")

        embed = discord.Embed(title=f"🗂️ Collection de {ctx.author.name}", color=discord.Color.dark_green())

        if not collection:
            embed.description = "Ta collection est vide."
        else:
            # Trie les clubs par ordre alphabétique
            for club in sorted(collection.keys()):
                # S'assure que la valeur du champ ne dépasse pas la limite de Discord (1024 caractères)
                joueurs_str = "\n".join(collection[club])
                if len(joueurs_str) > 1024:
                    joueurs_str = joueurs_str[:1020] + "\n..."
                embed.add_field(name=f"**{club}**", value=joueurs_str, inline=False)

        await ctx.send(embed=embed, ephemeral=True)

async def setup(bot):
    """Fonction requise par discord.py pour charger le Cog."""
    await bot.add_cog(CollectionCog(bot))