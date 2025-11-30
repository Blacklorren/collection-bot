import discord
from discord import app_commands # Importation nécessaire pour les commandes slash
from discord.ext import commands
import database
import json
import random
import datetime
import pytz

RARITY_COLORS = {
    "Commun": discord.Color.light_grey(),
    "Peu Commun": discord.Color.green(),
    "Rare": discord.Color.blue(),
    "Épique": discord.Color.purple(),
    "Légendaire": discord.Color.gold(),
    "Noël": discord.Color.from_rgb(220, 20, 60)
}

ANSI_COLORS = {
    "Commun": " [2;37m",       # Gris
    "Peu Commun": " [0;32m",    # Vert
    "Rare": " [0;34m",          # Bleu
    "Épique": " [0;35m",        # Magenta/Violet
    "Légendaire": " [0;33m",     # Jaune/Or
    "Noël": " [0;31m"
}

# --- CONFIGURATION ---
ANNONCE_CHANNEL_ID = 1405724982436167762 
PACK_COST = 150
DAILY_BONUS = 100
POINTS_PER_MESSAGE = 20
MAX_DAILY_MESSAGE_POINTS = 300
MESSAGE_COOLDOWN = 10

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
    "legendaire": 3000,
    "noel": 100
}

LEADERBOARD_EXCLUDED_IDS = [133711821214449665]

def load_cards_data():
    with open('cards.json', 'r', encoding='utf-8') as f:
        return json.load(f)

class CollectionCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.all_cards = load_cards_data()

        self.cards_by_rarity = {
            "Commun": [c for c in self.all_cards if c['rarete'] == 'Commun'],
            "Peu Commun": [c for c in self.all_cards if c['rarete'] == 'Peu Commun'],
            "Rare": [c for c in self.all_cards if c['rarete'] == 'Rare'],
            "Épique": [c for c in self.all_cards if c['rarete'] == 'Épique'],
            "Légendaire": [c for c in self.all_cards if c['rarete'] == 'Légendaire']
        }
        
        self.cards_per_club_total = {}
        for card in self.all_cards:
            club = card['club']
            self.cards_per_club_total[club] = self.cards_per_club_total.get(club, 0) + 1
            
        self.card_map = {card['id']: card for card in self.all_cards}

    # === ÉVÉNEMENTS (INCHANGÉS) ===
    @commands.Cog.listener()
    async def on_member_join(self, member):
        """Donne un pack de bienvenue à un nouveau membre."""
        if member.bot:
            return
        database.add_pack(member.id, 1)
        try:
            await member.send(f"👋 Bienvenue sur le serveur de Handnews, {member.mention} ! Nous t'avons offert un **pack de bienvenue**. Fais `/ouvrir` dans un des salons pour découvrir tes premières cartes !")
        except discord.errors.Forbidden:
            print(f"Impossible d'envoyer un MP à {member.name}.")

    @commands.Cog.listener()
    async def on_message(self, message):
        """Gère le gain de points avec des logs de débogage détaillés."""
        if message.author.bot or message.content.startswith('/') or message.content.startswith('!') or not message.guild:
            return

        paris_tz = pytz.timezone('Europe/Paris')
        now_paris = datetime.datetime.now(paris_tz)
        user_id = message.author.id

        user_data_row = database.get_user_data(user_id)
        if not user_data_row:
            return
        user_data = dict(user_data_row)

        if user_data.get('has_received_onboarding', 0) == 0:
            try:
                onboarding_message = (
                    "🎉 **Bienvenue dans le jeu de collection de cartes Handnews !** 🎉\n\n"
                    "Voici comment ça marche :\n"
                    "1.  **Gagnez des points** en participant sur le serveur. Votre premier message de la journée vous donne 120 points ! Ensuite vous obtenez 20 points par message écrit dans une limite totale de 300 points par jour. \n"
                    f"2.  **Achetez des packs** de cartes avec la commande `/pack` (coût : {PACK_COST} points).\n"
                    "3.  **Ouvrez vos packs** avec `/ouvrir` pour découvrir de nouveaux joueurs de Starligue.\n"
                    "4.  **Consultez votre collection** avec `/collection`.\n"
                    "5.  **Recyclez vos doublons** contre des fragments avec `/recycler` et utilisez `/creer \"Nom du Joueur\"` pour fabriquer les cartes qui vous manquent !\n\n"
                    "Bonne collection !"
                )
                await message.author.send(onboarding_message)
                database.set_onboarding_received(user_id)
            except discord.errors.Forbidden:
                return

        today_str = now_paris.date().isoformat()
        if user_data['last_activity_date'] != today_str:
            database.reset_daily_and_add_first_bonus(user_id, DAILY_BONUS, POINTS_PER_MESSAGE, now_paris.isoformat())
            return
        
        if user_data['last_message_time']:
            last_message_time = datetime.datetime.fromisoformat(user_data['last_message_time'])
            if last_message_time.tzinfo is None:
                last_message_time = paris_tz.localize(last_message_time)
            time_diff = (now_paris - last_message_time).total_seconds()
            if time_diff < MESSAGE_COOLDOWN:
                return

        if user_data['daily_message_points'] >= MAX_DAILY_MESSAGE_POINTS:
            return

        database.update_on_message_activity(user_id, POINTS_PER_MESSAGE, now_paris.isoformat())

    # === COMMANDES SLASH ===
    @app_commands.command(name='aide', description="Affiche la liste des commandes du jeu de collection.")
    async def help_command(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="📜 Aide - Bot de Collection Handnews",
            description="Voici la liste des commandes pour gérer ta collection de cartes.",
            color=discord.Color.blue()
        )
        embed.add_field(name="`/collection`", value="Affiche toutes les cartes que tu possèdes, triées par club.", inline=False)
        embed.add_field(name="`/points`", value="Consulte ton solde de points, packs et fragments actuel.", inline=False)
        embed.add_field(name="`/pack`", value=f"Achète un pack de cartes pour **{PACK_COST} points**.", inline=False)
        embed.add_field(name="`/ouvrir`", value="Ouvre un pack pour recevoir de nouvelles cartes.", inline=False)
        embed.add_field(name="`/recycler`", value="Échange toutes tes cartes en double contre des fragments.", inline=False)
        embed.add_field(name="`/creer <nom_du_joueur>`", value="Dépense tes fragments pour créer une carte manquante (Rare ou supérieure).", inline=False)
        embed.add_field(name="`/fragments`", value="Affiche ton solde de fragments et les coûts de recyclage/création.", inline=False)
        embed.add_field(name="`/top`", value="Affiche le classement des meilleurs collectionneurs.", inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name='points', description="Consulte ton solde de points, packs et fragments.")
    async def points_command(self, interaction: discord.Interaction):
        user_data = database.get_user_data(interaction.user.id)
        points = user_data['points']
        packs = user_data['packs']
        fragments = user_data['fragments']
        
        await interaction.response.send_message(f"💰 Tu as **{points} points** et **{packs} pack(s)**.\n♻️ Tu possèdes également **{fragments} fragments**.", ephemeral=True)
    
    @app_commands.command(name='pack', description=f"Achète un pack de cartes pour {PACK_COST} points.")
    async def pack_command(self, interaction: discord.Interaction):
        user_id = interaction.user.id
        user_data = database.get_user_data(user_id)
        points = user_data['points']
    
        if points >= PACK_COST:
            database.update_points(user_id, -PACK_COST)
            database.add_pack(user_id, 1)
            await interaction.response.send_message(f"🛍️ {interaction.user.mention}, tu as acheté un pack pour **{PACK_COST} points** ! Fais `/ouvrir` pour l'ouvrir.", ephemeral=True)
        else:
            await interaction.response.send_message(f"❌ {interaction.user.mention}, tu n'as pas assez de points. Il te manque **{PACK_COST - points} points**.", ephemeral=True)

@app_commands.command(name='ouvrir', description="Ouvre un pack pour recevoir de nouvelles cartes.")
    async def open_command(self, interaction: discord.Interaction):
        user_id = interaction.user.id
        user_data = database.get_user_data(user_id)
        packs = user_data['packs']
    
        if packs <= 0:
            await interaction.response.send_message(f"Tu n'as pas de pack à ouvrir. Fais `/pack` pour en acheter un.", ephemeral=True)
            return
        
        await interaction.response.send_message(f"🎉 C'est parti ! J'ouvre ton pack, {interaction.user.mention}...", ephemeral=True)
        
        database.remove_pack(user_id, 1)
        
        # --- LOGIQUE DE TIRAGE ---
        cartes_obtenues = []

        # Préparation des slots standards
        # Slot 1 (Commun/Peu Commun)
        slot1 = random.choices(
            population=[*self.cards_by_rarity["Commun"], *self.cards_by_rarity["Peu Commun"]],
            weights=[70]*len(self.cards_by_rarity["Commun"]) + [30]*len(self.cards_by_rarity["Peu Commun"]),
            k=1
        )[0]
        
        # Slot 2 (Commun/Peu Commun/Rare)
        slot2 = random.choices(
            population=[*self.cards_by_rarity["Commun"], *self.cards_by_rarity["Peu Commun"], *self.cards_by_rarity["Rare"]],
            weights=[30]*len(self.cards_by_rarity["Commun"]) + [50]*len(self.cards_by_rarity["Peu Commun"]) + [20]*len(self.cards_by_rarity["Rare"]),
            k=1
        )[0]

        # Slot 3 (Rare/Épique/Légendaire)
        slot3 = random.choices(
            population=[*self.cards_by_rarity["Rare"], *self.cards_by_rarity["Épique"], *self.cards_by_rarity["Légendaire"]],
            weights=[45]*len(self.cards_by_rarity["Rare"]) + [35]*len(self.cards_by_rarity["Épique"]) + [20]*len(self.cards_by_rarity["Légendaire"]),
            k=1
        )[0]

        # --- LOGIQUE CALENDRIER DE L'AVENT ---
        paris_tz = pytz.timezone('Europe/Paris')
        now = datetime.datetime.now(paris_tz)
        today_str = now.date().isoformat()
        
        # Vérifier si on est en décembre (mois 12) et entre le 1 et le 24
        is_advent_time = (now.month == 12 and 1 <= now.day <= 24)
        
        advent_card_triggered = False
        
        if is_advent_time:
            # Vérifier si l'utilisateur a déjà eu son pack de l'avent aujourd'hui
            last_advent_date = user_data['last_advent_pack_date'] # Assurez-vous d'avoir ajouté la colonne en DB
            
            if last_advent_date != today_str:
                # C'est le premier pack du jour !
                # On cherche la carte correspondante (ex: ID "noel_15" pour le 15 décembre)
                target_id = f"noel_{now.day}"
                advent_card = self.card_map.get(target_id)
                
                if advent_card:
                    # On remplace le Slot 1 (le moins rare) par la carte de Noël
                    slot1 = advent_card
                    database.set_advent_pack_opened(user_id, today_str)
                    advent_card_triggered = True

        # Assemblage final
        cartes_obtenues = [slot1, slot2, slot3]
        
        # --- ENREGISTREMENT ET AFFICHAGE ---
        for i, carte in enumerate(cartes_obtenues):
            database.add_card_to_collection(user_id, carte['id'])
            
            couleur = RARITY_COLORS.get(carte['rarete'], discord.Color.default())
            
            # Titre spécial pour la carte de l'avent
            titre_carte = f"**{carte['nom']}**"
            description_carte = f"**Rareté : {carte['rarete']}**\n*Club : {carte['club']}*"

            if advent_card_triggered and i == 0:
                titre_carte = f"🎄 CALENDRIER DE L'AVENT : {carte['nom']} 🎄"
                description_carte += "\n✨ *Carte exclusive du jour !* ✨"

            embed_carte = discord.Embed(
                title=titre_carte,
                description=description_carte,
                color=couleur
            )
            embed_carte.set_image(url=carte['image_url'])
            
            await interaction.followup.send(embed=embed_carte, ephemeral=True)
            
            # Annonce globale pour les grosses cartes (et Noël pourquoi pas ?)
            if (carte['rarete'] in ["Épique", "Légendaire", "Noël"]) and ANNONCE_CHANNEL_ID != 0:
                # Pour éviter le spam des cartes Noël, on peut filtrer si on veut
                should_announce = True
                if carte['rarete'] == "Noël" and not advent_card_triggered: 
                    # Si c'est du craft plus tard, on annonce pas forcément, ou si
                    pass

                if should_announce:
                    annonce_embed = discord.Embed(
                        title=f"✨ Tirage Exceptionnel ! ✨",
                        description=f"**{interaction.user.mention}** vient d'obtenir **{carte['nom']} ({carte['rarete']})** !",
                        color=couleur
                    )
                    annonce_embed.set_image(url=carte['image_url'])
                    
                    channel = self.bot.get_channel(ANNONCE_CHANNEL_ID)
                    if channel:
                        await channel.send(embed=annonce_embed)
                    
        await interaction.followup.send(f"Tes nouvelles cartes ont été ajoutées à ta collection ! Fais `/collection` pour les voir.", ephemeral=True)

    class CollectionView(discord.ui.View):
        current_club: str = None
        current_page: int = 0
    
        def __init__(self, author_id, user_collection_data, total_available_cards, cards_per_club_total):
            super().__init__(timeout=180)
            self.author_id = author_id
            self.collection = user_collection_data
            self.total_available_cards = total_available_cards
            self.cards_per_club_total = cards_per_club_total # On stocke le total
            
            self.cards_by_club = {}
            for card in self.collection:
                club = card['club']
                if club not in self.cards_by_club:
                    self.cards_by_club[club] = []
                self.cards_by_club[club].append(card)
            
            # La création des options est maintenant gérée par la nouvelle méthode
            self.club_select.options = self.create_select_options()
            
            if not self.collection:
                self.club_select.placeholder = "Ta collection est vide !"
                self.club_select.disabled = True
                
            self.update_buttons_state()
    
        def create_select_options(self):
            """Crée les options pour le menu déroulant avec la progression."""
            options = []
            # On itère sur tous les clubs possibles, triés par nom
            for club, total_count in sorted(self.cards_per_club_total.items()):
                # On récupère le nombre de cartes que l'utilisateur possède pour ce club
                owned_count = len(self.cards_by_club.get(club, []))
                
                # Si le club est celui de Noël, on ajoute une décoration
                label_display = club
                emoji_display = None
                
                if club == "Légendes Starligue": 
                    label_display = f"🎄 {club}"
                    emoji_display = "🎁" 
                
                options.append(
                    discord.SelectOption(
                        label=label_display,
                        description=f"{owned_count} / {total_count} carte(s) possédée(s)",
                        value=club, # Important : la value reste le nom brut du club
                        emoji=emoji_display
                    )
                )
            return options
    
        def update_buttons_state(self):
            if self.current_club is None:
                self.prev_button.disabled = True
                self.next_button.disabled = True
            else:

                cards_in_club = self.cards_by_club.get(self.current_club)
                if not cards_in_club:
                    self.prev_button.disabled = True
                    self.next_button.disabled = True
                else:
                    self.prev_button.disabled = self.current_page == 0
                    self.next_button.disabled = self.current_page >= len(cards_in_club) - 1
    
    def get_stars(self, rarity):
        """Génère une ligne d'étoiles selon la rareté."""
        mapping = {
            "Commun": "⭐",
            "Peu Commun": "⭐⭐",
            "Rare": "⭐⭐⭐",
            "Épique": "🌟🌟🌟🌟",
            "Légendaire": "👑👑👑👑👑",
            "Noël": "🎄🎄🎄🎄🎄"
        }
        return mapping.get(rarity, "⭐")

    def get_progress_bar(self, current, total, length=10):
        """Crée une petite barre de progression textuelle."""
        filled = int((current / total) * length)
        # On s'assure que si c'est la dernière, la barre est pleine
        if current == total - 1: filled = length 
        
        empty = length - filled
        return "▰" * filled + "▱" * empty

    async def generate_embed(self):
        # 1. PAGE D'ACCUEIL (Vue globale)
        if self.current_club is None:
            embed = discord.Embed(
                title="🗂️ Album de Collection",
                description="Sélectionnez un club ci-dessous pour feuilletter votre album.",
                color=discord.Color.dark_theme()
            )
            
            # Calcul des stats globales
            unique_user_cards = len(self.collection)
            percentage = (unique_user_cards / self.total_available_cards) * 100 if self.total_available_cards > 0 else 0
            
            # Barre de progression globale stylée
            blocks = int(percentage / 5) # Barre de 20 blocs
            bar = "█" * blocks + "░" * (20 - blocks)
            
            embed.add_field(
                name="📈 Progression Totale",
                value=f"```\n{bar} {percentage:.1f}%\n```\n**{unique_user_cards}** cartes sur **{self.total_available_cards}**",
                inline=False
            )
            
            # Petit teasing des fragments
            user_data = database.get_user_data(self.author_id)
            embed.set_footer(text=f"Solde de fragments : {user_data['fragments']} ♻️")
            return embed
        
        # 2. VUE D'UN CLUB
        cards_in_club = self.cards_by_club.get(self.current_club)
        
        # Si le club est vide
        if not cards_in_club:
            total_for_club = self.cards_per_club_total.get(self.current_club, 0)
            embed = discord.Embed(
                title=f"📁 {self.current_club}",
                description="*Ce classeur est vide... pour l'instant !*",
                color=discord.Color.dark_grey()
            )
            embed.set_image(url="https://media.discordapp.net/attachments/placeholder_empty_album.png") # Optionnel : mettre une image d'album vide
            embed.set_footer(text=f"Progression Club : 0 / {total_for_club}")
            return embed

        # 3. VUE D'UNE CARTE (Le rendu amélioré)
        card = cards_in_club[self.current_page]
        color = RARITY_COLORS.get(card['rarete'], discord.Color.default())
        
        # Détection "Noël" pour le style
        is_xmas = card['rarete'] == "Noël"
        
        # Titre avec Emojis
        emoji_club = "🎁" if is_xmas else "🤾"
        title_prefix = "❄️" if is_xmas else "🃏"
        
        embed = discord.Embed(
            title=f"{title_prefix} {card['nom']}",
            color=color
        )
        
        # Ajout de l'image
        embed.set_image(url=card['image_url'])
        
        # --- MISE EN PAGE AVEC FIELDS (Plus propre) ---
        
        # Colonne de Gauche : Club
        embed.add_field(
            name=f"{emoji_club} Club", 
            value=f"**{card['club']}**", 
            inline=True
        )
        
        # Colonne de Droite : Rareté
        rarity_emoji = self.bot.get_cog("CollectionCog").get_rarity_emoji(card['rarete'])
        embed.add_field(
            name=f"{rarity_emoji} Rareté", 
            value=f"**{card['rarete']}**", 
            inline=True
        )
        
        # Ligne du dessous : "Note" (Étoiles)
        embed.add_field(
            name="Niveau",
            value=self.get_stars(card['rarete']),
            inline=False
        )

        # Si c'est Noël, on ajoute une petite phrase d'ambiance
        if is_xmas:
            embed.add_field(
                name="🎄 Édition Limitée", 
                value="*Cette carte célèbre les fêtes de fin d'année 2025.*", 
                inline=False
            )

        # Footer avec barre de progression interne au club
        current_idx = self.current_page
        total_in_hand = len(cards_in_club)
        progress_bar = self.get_progress_bar(current_idx, total_in_hand)
        
        embed.set_footer(text=f"Carte {current_idx + 1}/{total_in_hand} du classeur │ {progress_bar}")

        return embed
    
        @discord.ui.select(placeholder="Choisis un club pour voir ta progression...", row=0)
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
    
    @app_commands.command(name='collection', description="Affiche ta collection de cartes de manière interactive.")
    async def collection_command(self, interaction: discord.Interaction):
        user_id = interaction.user.id
        
        all_card_ids = database.get_user_collection(user_id)
        unique_card_ids = list(set(all_card_ids))
        
        user_collection_data = [self.card_map[card_id] for card_id in unique_card_ids if card_id in self.card_map]
        
        view = self.CollectionView(
            author_id=user_id, 
            user_collection_data=user_collection_data, 
            total_available_cards=len(self.all_cards),
            cards_per_club_total=self.cards_per_club_total
        )
        initial_embed = await view.generate_embed()
        await interaction.response.send_message(embed=initial_embed, view=view, ephemeral=True)
    
    @app_commands.command(name='addpoints', description="[Admin] Donne des points à un membre.")
    @app_commands.describe(membre="L'utilisateur à qui donner des points.", montant="Le nombre de points à ajouter.")
    @app_commands.default_permissions(manage_guild=True)
    async def add_points_command(self, interaction: discord.Interaction, membre: discord.Member, montant: int):
        if montant <= 0:
            await interaction.response.send_message("❌ Vous devez donner un nombre de points positif.", ephemeral=True)
            return
    
        database.update_points(membre.id, montant)
    
        await interaction.response.send_message(f"✅ J'ai ajouté avec succès **{montant} points** à {membre.mention}.", ephemeral=True)
    
        try:
            await membre.send(f"🎉 Un administrateur vient de vous créditer de **{montant} points** !")
        except discord.errors.Forbidden:
            await interaction.followup.send(f"*(Note : Impossible de notifier {membre.mention} par message privé.)*", ephemeral=True)
   
    @app_commands.command(name='recycler', description="Échange toutes tes cartes en double contre des fragments.")
    async def recycle_command(self, interaction: discord.Interaction):
        user_id = interaction.user.id
        all_card_ids = database.get_user_collection(user_id)
        
        if not all_card_ids:
            await interaction.response.send_message("Tu n'as aucune carte à recycler.", ephemeral=True)
            return
            
        seen_ids = set()
        duplicates = []
        for card_id in all_card_ids:
            if card_id in seen_ids:
                duplicates.append(card_id)
            else:
                seen_ids.add(card_id)
        
        if not duplicates:
            await interaction.response.send_message("Tu n'as aucune carte en double à recycler.", ephemeral=True)
            return
            
        fragments_gained = 0
        for card_id in duplicates:
            card = self.card_map.get(card_id)
            if card:
                fragments_gained += FRAGMENT_VALUES.get(card['rarete'], 0)
        
        database.update_fragments(user_id, fragments_gained)
        database.reset_and_set_collection(user_id, list(seen_ids))
        
        user_data = database.get_user_data(user_id)
        
        embed = discord.Embed(
            title="♻️ Recyclage Terminé ♻️",
            description=f"Tu as recyclé **{len(duplicates)}** carte(s) en double et obtenu **{fragments_gained} fragments**.",
            color=discord.Color.orange()
        )
        embed.add_field(name="Nouveau Solde", value=f"Tu possèdes maintenant **{user_data['fragments']} fragments**.")
        await interaction.response.send_message(embed=embed, ephemeral=True)

@app_commands.command(name='creer', description="Dépense tes fragments pour créer une carte manquante.")
    @app_commands.describe(nom_de_la_carte="Le nom (même partiel) du joueur que tu veux créer.")
    async def create_card_command(self, interaction: discord.Interaction, nom_de_la_carte: str):
        user_id = interaction.user.id
        
        search_term = nom_de_la_carte.lower()
        matches = [card for card in self.all_cards if search_term in card['nom'].lower()]
        
        if not matches:
            await interaction.response.send_message(f"Désolé, je ne trouve aucune carte contenant \"{nom_de_la_carte}\".", ephemeral=True)
            return
        if len(matches) > 1:
            await interaction.response.send_message(f"Ta recherche \"{nom_de_la_carte}\" correspond à plusieurs joueurs. Sois plus précis !", ephemeral=True)
            return
        
        target_card = matches[0]
        
        user_card_ids = database.get_user_collection(user_id)
        if target_card['id'] in user_card_ids:
            await interaction.response.send_message(f"Tu possèdes déjà la carte **{target_card['nom']}**.", ephemeral=True)
            return
            
        rarity_key = target_card['rarete'].lower().replace("é", "e").replace("ë", "e") # gère "Noël" -> "noel"
        
        # --- LOGIQUE SPÉCIALE NOËL ---
        cost = 0
        
        if target_card['rarete'] == "Noël":
            paris_tz = pytz.timezone('Europe/Paris')
            now = datetime.datetime.now(paris_tz)
            
            # Si on est avant le 25 décembre (mois < 12 ou (mois 12 et jour < 25))
            if now.month < 12 or (now.month == 12 and now.day < 25):
                await interaction.response.send_message(f"❄️ Les cartes du **Calendrier de l'Avent** sont exclusives aux packs quotidiens jusqu'au 25 décembre ! Reviens plus tard.", ephemeral=True)
                return
            
            # Après le 25 décembre
            cost = 100
        
        elif rarity_key in JOKER_COSTS:
            cost = JOKER_COSTS[rarity_key]
        else:
             await interaction.response.send_message(f"Tu ne peux pas créer de carte de rareté '{target_card['rarete']}'.", ephemeral=True)
             return
             
        user_data = database.get_user_data(user_id)
        user_fragments = user_data['fragments']
        
        if user_fragments < cost:
            await interaction.response.send_message(f"Il te faut **{cost} fragments** pour créer cette carte, mais tu n'en as que **{user_fragments}**.", ephemeral=True)
            return
            
        database.update_fragments(user_id, -cost)
        database.add_card_to_collection(user_id, target_card['id'])
        
        embed = discord.Embed(
            title="🃏 Création de Carte Réussie ! 🃏",
            description=f"Tu as dépensé **{cost} fragments** pour créer la carte **{target_card['nom']} ({target_card['rarete']})** !",
            color=RARITY_COLORS.get(target_card['rarete'], discord.Color.default())
        )
        embed.set_thumbnail(url=target_card['image_url'])
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name='fragments', description="Affiche ton solde de fragments et les coûts de création/recyclage.")
    async def fragments_command(self, interaction: discord.Interaction):
        user_id = interaction.user.id
        user_data = database.get_user_data(user_id)
        
        embed = discord.Embed(
            title="♻️ Gestion des Fragments ♻️",
            description=f"Tu possèdes actuellement **{user_data['fragments']} fragments**.",
            color=discord.Color.from_rgb(180, 180, 180)
        )
        
        recycling_rates = ""
        for rarity, value in FRAGMENT_VALUES.items():
            recycling_rates += f"Doublon **{rarity}** : **{value}** fragments\n"
        embed.add_field(name="Taux de Recyclage (`/recycler`)", value=recycling_rates, inline=False)
        
        creation_costs = ""
        for rarity, cost in JOKER_COSTS.items():
            if rarity == "noel":
                creation_costs += f"Créer une carte **Noël** (après le 25/12) : **{cost}** fragments\n"
            else:
                creation_costs += f"Créer une carte **{rarity.capitalize()}** : **{cost}** fragments\n"
        embed.add_field(name="Coût de Création (`/creer`)", value=creation_costs, inline=False)
        
        embed.set_footer(text="Utilise /recycler pour gagner des fragments et /creer pour les dépenser.")
        
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name='top', description="Affiche le classement des meilleurs collectionneurs.")
    async def top_command(self, interaction: discord.Interaction):
        leaderboard_data = database.get_leaderboard_data()
        
        filtered_leaderboard = [
            (user_id, unique_cards) 
            for user_id, unique_cards in leaderboard_data 
            if user_id not in LEADERBOARD_EXCLUDED_IDS
        ]
       
        embed = discord.Embed(
            title="🏆 Top 5 des Collectionneurs 🏆",
            description="Classement basé sur le nombre de cartes uniques possédées.",
            color=discord.Color.gold()
        )
        
        description_text = ""
        displayed = 0
        for user_id, unique_cards in filtered_leaderboard:
            if displayed >= 5:
                break
            member = interaction.guild.get_member(user_id)
            if not member:
                continue
            displayed += 1

            emoji = ""
            if displayed == 1:
                emoji = "🥇 "
            elif displayed == 2:
                emoji = "🥈 "
            elif displayed == 3:
                emoji = "🥉 "
            else:
                emoji = f"**#{displayed}** "

            description_text += f"{emoji} **{member.display_name}** - {unique_cards} / {len(self.all_cards)} cartes\n"

        if not description_text:
            embed.description = "Le classement est encore vide. Collectionnez des cartes pour apparaître ici !"
        else:
            embed.description = description_text
            
        # Cette commande est publique, donc pas de ephemeral=True
        await interaction.response.send_message(embed=embed)

    # Petite fonction utilitaire pour obtenir un emoji correspondant à la rareté
    def get_rarity_emoji(self, rarity_name: str) -> str:
        emojis = {
            "Commun": "⬜",
            "Peu Commun": "🟩",
            "Rare": "🟦",
            "Épique": "🟪",
            "Légendaire": "🟨",
            "Noël": "🎄" 
        }
        return emojis.get(rarity_name, "🔹")

    @app_commands.command(name='topowned', description="[Admin] Affiche les 5 cartes les plus possédées par rareté.")
    @app_commands.default_permissions(manage_guild=True)
    async def top_owned_command(self, interaction: discord.Interaction):
        """Affiche les 5 cartes les plus possédées pour chaque rareté, basé sur le nombre d'utilisateurs uniques."""
        await interaction.response.defer()

        try:
            # 1. Interroger la base de données pour compter le nombre de propriétaires UNIQUES pour chaque carte
            with database.sqlite3.connect(database.DB_NAME) as con:
                con.row_factory = database.sqlite3.Row
                cur = con.cursor()
                
                # <<<--- LA CORRECTION EST ICI ---<<<
                # On utilise COUNT(DISTINCT user_id) pour ne compter chaque utilisateur qu'une seule fois par carte.
                cur.execute("""
                    SELECT card_id, COUNT(DISTINCT user_id) as owner_count
                    FROM user_cards
                    GROUP BY card_id
                    ORDER BY owner_count DESC
                """)
                ownership_counts = cur.fetchall()

            if not ownership_counts:
                await interaction.followup.send("Personne ne possède de cartes pour le moment.")
                return

            # 2. Organiser les résultats par rareté (le reste du code est inchangé)
            stats_by_rarity = {
                "Commun": [], "Peu Commun": [], "Rare": [], "Épique": [], "Légendaire": []
            }

            for row in ownership_counts:
                card_id = row['card_id']
                count = row['owner_count']
                
                card_details = self.card_map.get(card_id)
                if card_details:
                    rarity = card_details['rarete']
                    if rarity in stats_by_rarity:
                        stats_by_rarity[rarity].append((card_details['nom'], count))

            # 3. Construire l'embed final
            embed = discord.Embed(
                title="🏆 Top 5 des Cartes les Plus Possédées",
                description="Ce classement est basé sur le nombre d'utilisateurs uniques possédant chaque carte actuellement.",
                color=discord.Color.blue()
            )

            for rarity, cards in stats_by_rarity.items():
                if not cards:
                    field_value = "Aucune carte de cette rareté n'est possédée."
                else:
                    field_value = ""
                    for rank, (name, count) in enumerate(cards[:5], 1):
                        field_value += f"**{rank}.** {name} - `{count}` possesseur(s)\n"
                
                embed.add_field(
                    name=f"{self.get_rarity_emoji(rarity)} {rarity}",
                    value=field_value,
                    inline=False
                )

            await interaction.followup.send(embed=embed)

        except Exception as e:
            await interaction.followup.send(f"❌ Une erreur est survenue lors de la génération des statistiques : {e}", ephemeral=True)
            print(f"Erreur dans /topowned : {e}")

async def setup(bot):
    await bot.add_cog(CollectionCog(bot))
