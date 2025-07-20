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

# Configuration du système de recyclage et de Jokers
FRAGMENT_VALUES = {
    "Commun": 2,
    "Peu Commun": 5,
    "Rare": 10,
    "Épique": 30,
    "Légendaire": 100
}

JOKER_COSTS = {
    "rare": 250,
    "epique": 800,
    "legendaire": 3000
}

# List of user IDs to exclude from the !top command leaderboard
LEADERBOARD_EXCLUDED_IDS = 133711821214449665

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
        embed.add_field(name="`!recycler`", value="Échange toutes tes cartes en double contre des fragments.", inline=False)
        embed.add_field(name="`!creer \"Nom du Joueur\"`", value="Dépense tes fragments pour créer une carte manquante (Rare ou supérieure).", inline=False)
        embed.add_field(name="`!fragments`", value="Affiche ton solde de fragments et les coûts de recyclage/création.", inline=False)
        embed.add_field(name="`!top`", value="Affiche le classement des meilleurs collectionneurs.", inline=False)
        await ctx.send(embed=embed)


    
    @commands.command(name='points')
    async def points_command(self, ctx):
        """Affiche les soldes de l'utilisateur."""
        user_data = database.get_user_data(ctx.author.id)
        points = user_data['points']
        packs = user_data['packs']
        fragments = user_data['fragments']
        
        await ctx.send(f"💰 Tu as **{points} points** et **{packs} pack(s)**.\n♻️ Tu possèdes également **{fragments} fragments**.", ephemeral=True)
    
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
    
        def __init__(self, author_id, user_collection_data, total_available_cards):
            super().__init__(timeout=180) # La vue se désactive après 3 minutes
            self.author_id = author_id
            self.collection = user_collection_data
            self.total_available_cards = total_available_cards
            
            # On regroupe les cartes de l'utilisateur par club
            self.cards_by_club = {}
            for card in self.collection:
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
                discord.SelectOption(label=club, description=f"{len(cards)} carte(s) possédée(s)")
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
                    description="Utilise le menu déroulant pour explorer ta collection par club.",
                    color=discord.Color.dark_green()
                )
                
                unique_user_cards = len(self.collection)
                percentage = (unique_user_cards / self.total_available_cards) * 100 if self.total_available_cards > 0 else 0
                
                filled_blocks = int(percentage / 10)
                empty_blocks = 10 - filled_blocks
                progress_bar = "🟩" * filled_blocks + "⬛" * empty_blocks
                
                embed.add_field(
                    name="Progression Générale",
                    value=f"**{unique_user_cards} / {self.total_available_cards}** cartes uniques\n"
                          f"{progress_bar} **{percentage:.2f}%**",
                    inline=False
                )
                return embed
            
            # Cette partie n'est exécutée que si un club est sélectionné
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
            if interaction.user.id != self.author_id: return
            self.current_page -= 1
            self.update_buttons_state()
            embed = await self.generate_embed()
            await interaction.response.edit_message(embed=embed, view=self)
    
        @discord.ui.button(label="Suivant ▶", style=discord.ButtonStyle.blurple, row=1)
        async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
            if interaction.user.id != self.author_id: return
            self.current_page += 1
            self.update_buttons_state()
            embed = await self.generate_embed()
            await interaction.response.edit_message(embed=embed, view=self)
    
    
    @commands.command(name='collection')
    async def collection_command(self, ctx):
        """Affiche la collection de manière interactive."""
        user_id = ctx.author.id
        
        all_card_ids = database.get_user_collection(user_id)
        unique_card_ids = list(set(all_card_ids))
        
        user_collection_data = [self.card_map[card_id] for card_id in unique_card_ids if card_id in self.card_map]
        
        if not user_collection_data:
            await ctx.send("Ta collection est vide pour le moment. Ouvre des packs pour la commencer !", ephemeral=True)
            return
        
        # --- MODIFICATION ICI ---
        # On passe maintenant le nombre total de cartes à la vue
        view = self.CollectionView(user_id, user_collection_data, total_available_cards=len(self.all_cards))
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
   
    @commands.command(name='recycler')
    async def recycle_command(self, ctx):
        """Recycle les cartes en double pour obtenir des Fragments."""
        user_id = ctx.author.id
        all_card_ids = database.get_user_collection(user_id)
        
        if not all_card_ids:
            await ctx.send("Tu n'as aucune carte à recycler.", ephemeral=True)
            return
            
        seen_ids = set()
        duplicates = []
        for card_id in all_card_ids:
            if card_id in seen_ids:
                duplicates.append(card_id)
            else:
                seen_ids.add(card_id)
        
        if not duplicates:
            await ctx.send("Tu n'as aucune carte en double à recycler.", ephemeral=True)
            return
            
        fragments_gained = 0
        for card_id in duplicates:
            card = self.card_map.get(card_id)
            if card:
                fragments_gained += FRAGMENT_VALUES.get(card['rarete'], 0)
        
        # Mettre à jour la base de données
        database.update_fragments(user_id, fragments_gained)
        database.reset_and_set_collection(user_id, list(seen_ids))
        
        user_data = database.get_user_data(user_id)
        
        embed = discord.Embed(
            title="♻️ Recyclage Terminé ♻️",
            description=f"Tu as recyclé **{len(duplicates)}** carte(s) en double et obtenu **{fragments_gained} fragments**.",
            color=discord.Color.orange()
        )
        embed.add_field(name="Nouveau Solde", value=f"Tu possèdes maintenant **{user_data['fragments']} fragments**.")
        await ctx.send(embed=embed, ephemeral=True)
    
    
    # --- REMPLACEZ L'ANCIENNE create_card_command PAR CELLE-CI ---

    @commands.command(name='creer')
    async def create_card_command(self, ctx, *, card_name: str):
        """Crée une carte manquante en utilisant une recherche de nom flexible."""
        user_id = ctx.author.id
        
        # 1. Trouver les correspondances possibles (insensible à la casse et partiel)
        search_term = card_name.lower()
        matches = [card for card in self.all_cards if search_term in card['nom'].lower()]
        
        if not matches:
            await ctx.send(f"Désolé, je ne trouve aucune carte contenant \"{card_name}\".", ephemeral=True)
            return
        if len(matches) > 1:
            await ctx.send(f"Ta recherche \"{card_name}\" correspond à plusieurs joueurs. Sois plus précis !", ephemeral=True)
            return
        
        target_card = matches[0]
        
        # 2. Vérifier si l'utilisateur possède déjà la carte
        user_card_ids = database.get_user_collection(user_id)
        if target_card['id'] in user_card_ids:
            await ctx.send(f"Tu possèdes déjà la carte **{target_card['nom']}**.", ephemeral=True)
            return
            
        # 3. Vérifier le coût et le solde de fragments
        rarity_key = target_card['rarete'].lower().replace("é", "e") # Gère "Épique" et "Légendaire"
        if rarity_key not in JOKER_COSTS:
             await ctx.send(f"Tu ne peux pas créer de carte de rareté '{target_card['rarete']}'. Seules les cartes Rares, Épiques ou Légendaires peuvent être créées.", ephemeral=True)
             return
             
        cost = JOKER_COSTS[rarity_key]
        user_data = database.get_user_data(user_id)
        user_fragments = user_data['fragments']
        
        if user_fragments < cost:
            await ctx.send(f"Il te faut **{cost} fragments** pour créer cette carte, mais tu n'en as que **{user_fragments}**.", ephemeral=True)
            return
            
        # 4. Exécuter la transaction
        database.update_fragments(user_id, -cost)
        database.add_card_to_collection(user_id, target_card['id'])
        
        embed = discord.Embed(
            title="🃏 Création de Carte Réussie ! 🃏",
            description=f"Tu as dépensé **{cost} fragments** pour créer la carte **{target_card['nom']} ({target_card['rarete']})** !",
            color=RARITY_COLORS.get(target_card['rarete'], discord.Color.default())
        )
        embed.set_thumbnail(url=target_card['image_url'])
        await ctx.send(embed=embed, ephemeral=True)

    @commands.command(name='fragments')
    async def fragments_command(self, ctx):
        """Affiche le solde de fragments, les taux de recyclage et les coûts de création."""
        user_id = ctx.author.id
        user_data = database.get_user_data(user_id)
        
        embed = discord.Embed(
            title="♻️ Gestion des Fragments ♻️",
            description=f"Tu possèdes actuellement **{user_data['fragments']} fragments**.",
            color=discord.Color.from_rgb(180, 180, 180) # Une couleur grise/métal
        )
        
        # Taux de recyclage
        recycling_rates = ""
        for rarity, value in FRAGMENT_VALUES.items():
            recycling_rates += f"Doublon **{rarity}** : **{value}** fragments\n"
        
        embed.add_field(
            name="Taux de Recyclage (`!recycler`)",
            value=recycling_rates,
            inline=False
        )
        
        # Coûts de création
        creation_costs = ""
        for rarity, cost in JOKER_COSTS.items():
            # Met la première lettre en majuscule pour un affichage propre
            creation_costs += f"Créer une carte **{rarity.capitalize()}** : **{cost}** fragments\n"
            
        embed.add_field(
            name="Coût de Création (`!creer`)",
            value=creation_costs,
            inline=False
        )
        
        embed.set_footer(text="Utilise !recycler pour gagner des fragments et !creer \"Nom Joueur\" pour les dépenser.")
        
        await ctx.send(embed=embed, ephemeral=True)

    # --- REPLACE THE OLD top_command WITH THIS ONE ---

    @commands.command(name='top')
    async def top_command(self, ctx):
        """Affiche le classement des meilleurs collectionneurs."""
        leaderboard_data = database.get_leaderboard_data()
        
        # --- NEW FILTERING LOGIC ---
        # Filter out the excluded IDs from the data before displaying
        filtered_leaderboard = [
            (user_id, unique_cards) 
            for user_id, unique_cards in leaderboard_data 
            if user_id not in LEADERBOARD_EXCLUDED_IDS
        ]
        # We will only display the top 5 of the filtered list
        top_5_filtered = filtered_leaderboard[:5]
        
        embed = discord.Embed(
            title="🏆 Top 5 des Collectionneurs 🏆",
            description="Classement basé sur le nombre de cartes uniques possédées.",
            color=discord.Color.gold()
        )
        
        if not top_5_filtered:
            embed.description = "Le classement est encore vide. Collectionnez des cartes pour apparaître ici !"
        else:
            description_text = ""
            for rank, (user_id, unique_cards) in enumerate(top_5_filtered, 1):
                member = ctx.guild.get_member(user_id)
                member_name = member.display_name if member else f"Utilisateur Inconnu"
                
                # Skip if member is not found in the server
                if not member:
                    continue
    
                emoji = ""
                if rank == 1: emoji = "🥇 "
                elif rank == 2: emoji = "🥈 "
                elif rank == 3: emoji = "🥉 "
                else: emoji = f"**#{rank}** "
                
                description_text += f"{emoji} **{member_name}** - {unique_cards} / {len(self.all_cards)} cartes\n"
            
            embed.description = description_text
            
        await ctx.send(embed=embed)

async def setup(bot):
    """Fonction requise par discord.py pour charger le Cog."""
    await bot.add_cog(CollectionCog(bot))
