import discord
from discord.ext import commands
import database
import json
import random
import datetime

RARITY_COLORS = {
    "Commun": discord.Color.light_grey(),
    "Peu Commun": discord.Color.green(),
    "Rare": discord.Color.blue(),
    "Épique": discord.Color.purple(),
    "Légendaire": discord.Color.gold()
}

ANSI_COLORS = {
    "Commun": "[2;37m",       # Gris
    "Peu Commun": "[0;32m",    # Vert
    "Rare": "[0;34m",          # Bleu
    "Épique": "[0;35m",        # Magenta/Violet
    "Légendaire": "[0;33m"     # Jaune/Or
}

# --- CONFIGURATION ---
# Remplacez 0 par l'ID du canal où les annonces de cartes rares seront postées.
# Pour obtenir l'ID : Clic droit sur le canal -> "Copier l'ID du salon" (Mode développeur doit être activé dans Discord)
ANNONCE_CHANNEL_ID = 0 
PACK_COST = 150  # Coût en points pour acheter un pack
DAILY_BONUS = 100 # Points pour le premier message de la journée
POINTS_PER_MESSAGE = 10 # Points par message
MAX_DAILY_MESSAGE_POINTS = 200 # Limite de points gagnés par message par jour
MESSAGE_COOLDOWN = 10 # Temps en secondes entre chaque gain de points

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
        """Gère le gain de points pour les messages et le bonus journalier."""
        if message.author.bot or message.content.startswith('!') or not message.guild:
            return
    
        user_id = message.author.id
        user_data = database.get_user_data(user_id)
        
        now = datetime.datetime.now()
        today_str = datetime.date.today().isoformat()
    
        # 1. Vérifier si c'est un nouveau jour pour l'utilisateur
        if user_data['last_activity_date'] != today_str:
            # C'est le premier message de la journée !
            database.reset_daily_and_add_first_bonus(user_id, DAILY_BONUS, POINTS_PER_MESSAGE)
            try:
                # On le notifie de son bonus
                await message.author.send(f"🎉 C'est ton premier message de la journée ! Tu as reçu un bonus de **{DAILY_BONUS} points** ainsi que **{POINTS_PER_MESSAGE} points** pour ton message.")
            except discord.errors.Forbidden:
                pass # L'utilisateur a bloqué les MPs, on continue silencieusement
            return # L'action est terminée pour ce message
    
        # 2. Si c'est le même jour, on vérifie le cooldown
        if user_data['last_message_time']:
            last_message_time = datetime.datetime.fromisoformat(user_data['last_message_time'])
            if (now - last_message_time).total_seconds() < MESSAGE_COOLDOWN:
                return # Cooldown n'est pas terminé, on ne fait rien
    
        # 3. On vérifie si la limite de points par message est atteinte
        if user_data['daily_message_points'] >= MAX_DAILY_MESSAGE_POINTS:
            return # Limite atteinte, on ne donne plus de points pour les messages aujourd'hui
    
        # 4. Si toutes les conditions sont remplies, on donne les points
        database.update_on_message_activity(user_id, POINTS_PER_MESSAGE)

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
        embed.add_field(name="`!pack`", value=f"Achète un pack de cartes pour **{PACK_COST} points**.", inline=False)
        embed.add_field(name="`!ouvrir`", value="Ouvre un pack pour recevoir de nouvelles cartes.", inline=False)
        await ctx.send(embed=embed)


    @commands.command(name='points')
    async def points_command(self, ctx):
        """Affiche le solde de points de l'utilisateur."""
        # On récupère toutes les données de l'utilisateur dans un seul objet
        user_data = database.get_user_data(ctx.author.id)
    
        # On accède aux valeurs dont on a besoin par leur nom
        points = user_data['points']
        packs = user_data['packs']
    
        await ctx.send(f"💰 {ctx.author.mention}, tu as actuellement **{points} points** et **{packs} pack(s)** à ouvrir.", ephemeral=True)
    
    @commands.command(name='pack')
    async def pack_command(self, ctx):
        """Achète un pack de cartes."""
        user_id = ctx.author.id
        # On récupère les données de l'utilisateur
        user_data = database.get_user_data(user_id)
    
        # On accède aux points par leur nom
        points = user_data['points']
    
        if points >= PACK_COST:
            database.update_points(user_id, -PACK_COST)
            database.add_pack(user_id, 1)
            await ctx.send(f"🛍️ {ctx.author.mention}, tu as acheté un pack pour **{PACK_COST} points** ! Fais `!ouvrir` pour l'ouvrir.", ephemeral=True)
        else:
            await ctx.send(f"❌ {ctx.author.mention}, tu n'as pas assez de points. Il te manque **{PACK_COST - points} points**.", ephemeral=True)

    
    @commands.command(name='ouvrir')
    async def open_command(self, ctx):
        """Ouvre un pack et révèle les cartes obtenues une par une."""
        user_id = ctx.author.id
        # On récupère les données de l'utilisateur
        user_data = database.get_user_data(user_id)
        
        # On accède au nombre de packs par son nom
        packs = user_data['packs']
    
        if packs <= 0:
            await ctx.send(f"Tu n'as pas de pack à ouvrir. Fais `!pack` pour en acheter un.", ephemeral=True)
            return
        
        # On informe l'utilisateur que l'ouverture commence
        await ctx.send(f"🎉 C'est parti ! J'ouvre ton pack, {ctx.author.mention}...", ephemeral=True)
        
        database.remove_pack(user_id, 1)
        
        # Logique de tirage des cartes (inchangée)
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
        
        # Boucle pour envoyer une carte par message
        for carte in cartes_obtenues:
            database.add_card_to_collection(user_id, carte['id'])
            
            couleur = RARITY_COLORS.get(carte['rarete'], discord.Color.default())
            embed_carte = discord.Embed(
                title=f"**{carte['nom']}**",
                description=f"**Rareté : {carte['rarete']}**\n*Club : {carte['club']}*",
                color=couleur
            )
            embed_carte.set_image(url=carte['image_url'])
            
            await ctx.send(embed=embed_carte, ephemeral=True)
            
            if carte['rarete'] in ["Épique", "Légendaire"] and ANNONCE_CHANNEL_ID != 0:
                annonce_embed = discord.Embed(
                    title=f"✨ Tirage Exceptionnel ! ✨",
                    description=f"**{ctx.author.mention}** vient d'obtenir **{carte['nom']} ({carte['rarete']})** dans un pack !",
                    color=RARITY_COLORS.get(carte['rarete'])
                )
                annonce_embed.set_image(url=carte['image_url'])
                annonce_embed.set_footer(text="Félicitations !")
                
                channel = self.bot.get_channel(ANNONCE_CHANNEL_ID)
                if channel:
                    await channel.send(embed=annonce_embed)
                    
        await ctx.send(f"Tes nouvelles cartes ont été ajoutées à ta collection ! Fais `!collection` pour les voir.", ephemeral=True)

    

    class CollectionView(discord.ui.View):
        # On définit les variables d'état ici
        current_club: str = None
        current_page: int = 0
    
        def __init__(self, author_id, user_collection_data):
            super().__init__(timeout=180) # La vue se désactive après 3 minutes
            self.author_id = author_id
            
            # On regroupe les cartes de l'utilisateur par club
            self.cards_by_club = {}
            for card in user_collection_data:
                club = card['club']
                if club not in self.cards_by_club:
                    self.cards_by_club[club] = []
                self.cards_by_club[club].append(card)
            
            # On peuple dynamiquement le menu déroulant avec les clubs possédés
            self.club_select.options = self.create_select_options()
            if not self.club_select.options:
                self.club_select.placeholder = "Ta collection est vide !"
                self.club_select.disabled = True
                
            # On met à jour l'état initial des boutons
            self.update_buttons_state()
    
        def create_select_options(self):
            """Crée la liste des options pour le menu déroulant."""
            return [
                discord.SelectOption(label=club, description=f"{len(cards)} carte(s)")
                for club, cards in sorted(self.cards_by_club.items())
            ]
    
        def update_buttons_state(self):
            """Active ou désactive les boutons de navigation."""
            if self.current_club is None:
                self.prev_button.disabled = True
                self.next_button.disabled = True
            else:
                cards_in_club = self.cards_by_club[self.current_club]
                self.prev_button.disabled = self.current_page == 0
                self.next_button.disabled = self.current_page >= len(cards_in_club) - 1
    
        async def generate_embed(self):
            """Génère l'embed en fonction de l'état actuel (club et page)."""
            if self.current_club is None:
                embed = discord.Embed(
                    title="🗂️ Ta Collection",
                    description="Utilise le menu déroulant ci-dessous pour sélectionner un club et voir tes cartes.",
                    color=discord.Color.dark_green()
                )
                total_unique_cards = len(self.cards_by_club.keys())
                embed.set_footer(text=f"Tu possèdes des cartes de {total_unique_cards} club(s) différent(s).")
                return embed
            
            cards_in_club = self.cards_by_club[self.current_club]
            card = cards_in_club[self.current_page]
            
            color = RARITY_COLORS.get(card['rarete'], discord.Color.default())
            embed = discord.Embed(
                title=f"**{card['nom']}**",
                description=f"**Club :** {card['club']}\n**Rareté :** {card['rarete']}",
                color=color
            )
            embed.set_image(url=card['image_url'])
            embed.set_footer(text=f"Carte {self.current_page + 1} / {len(cards_in_club)}")
            return embed
    
        # --- Définition des composants interactifs avec les décorateurs ---
    
        @discord.ui.select(placeholder="Choisis un club pour voir les cartes...", row=0)
        async def club_select(self, interaction: discord.Interaction, select: discord.ui.Select):
            if interaction.user.id != self.author_id:
                await interaction.response.send_message("Tu ne peux pas interagir avec la collection de quelqu'un d'autre.", ephemeral=True)
                return
    
            self.current_club = select.values[0]
            self.current_page = 0
            self.update_buttons_state()
            embed = await self.generate_embed()
            await interaction.response.edit_message(embed=embed, view=self)
    
        @discord.ui.button(label="◀ Précédent", style=discord.ButtonStyle.blurple, row=1)
        async def prev_button(self, interaction: discord.Interaction, button: discord.ui.Button):
            if interaction.user.id != self.author_id:
                return
            self.current_page -= 1
            self.update_buttons_state()
            embed = await self.generate_embed()
            await interaction.response.edit_message(embed=embed, view=self)
    
        @discord.ui.button(label="Suivant ▶", style=discord.ButtonStyle.blurple, row=1)
        async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
            if interaction.user.id != self.author_id:
                return
            self.current_page += 1
            self.update_buttons_state()
            embed = await self.generate_embed()
            await interaction.response.edit_message(embed=embed, view=self)
    
    
    @commands.command(name='collection')
    async def collection_command(self, ctx):
        """Affiche la collection de manière interactive."""
        user_id = ctx.author.id
        
        all_card_ids = database.get_user_collection(user_id)
        unique_card_ids = list(set(all_card_ids)) # Gestion des doublons
        
        user_collection_data = [self.card_map[card_id] for card_id in unique_card_ids if card_id in self.card_map]
        
        if not user_collection_data:
            await ctx.send("Ta collection est vide pour le moment. Ouvre des packs pour la commencer !", ephemeral=True)
            return
            
        view = self.CollectionView(user_id, user_collection_data)
        initial_embed = await view.generate_embed()
        await ctx.send(embed=initial_embed, view=view, ephemeral=True)



    @commands.command(name='addpoints')
    @commands.has_permissions(manage_guild=True) # <-- LA SÉCURITÉ EST ICI !
    async def add_points_command(self, ctx, member: discord.Member, amount: int):
        """
        Commande réservée aux admins pour donner des points à un membre.
        Utilisation : !addpoints @NomUtilisateur 500
        """
        if amount <= 0:
            await ctx.send("❌ Vous devez donner un nombre de points positif.", ephemeral=True)
            return
    
        # On ajoute les points dans la base de données
        database.update_points(member.id, amount)
    
        # On confirme l'action à l'administrateur
        await ctx.send(f"✅ J'ai ajouté avec succès **{amount} points** à {member.mention}.", ephemeral=True)
    
        # (Optionnel) On envoie un message privé à l'utilisateur qui a reçu les points
        try:
            await member.send(f"🎉 Un administrateur vient de vous créditer de **{amount} points** !")
        except discord.errors.Forbidden:
            # Si l'utilisateur a bloqué les MPs, on prévient l'admin
            await ctx.send(f"*(Note : Impossible de notifier {member.mention} par message privé.)*", ephemeral=True)

async def setup(bot):
    """Fonction requise par discord.py pour charger le Cog."""
    await bot.add_cog(CollectionCog(bot))
