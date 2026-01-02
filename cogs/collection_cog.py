import discord
from discord import app_commands
from discord.ext import commands
import database
import json
import random
import datetime
import pytz
from utils import album_generator

# --- CONSTANTES & COULEURS ---
RARITY_COLORS = {
    "Commun": discord.Color.light_grey(),
    "Peu Commun": discord.Color.green(),
    "Rare": discord.Color.blue(),
    "Épique": discord.Color.purple(),
    "Légendaire": discord.Color.gold(),
    "Noël": discord.Color.from_rgb(220, 20, 60)
}

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

# --- CONFIGURATION ---
ANNONCE_CHANNEL_ID = 1405724982436167762 
PACK_COST = 150
DAILY_BONUS = 100
POINTS_PER_MESSAGE = 20
MAX_DAILY_MESSAGE_POINTS = 300
MESSAGE_COOLDOWN = 10
LEADERBOARD_EXCLUDED_IDS = [133711821214449665]

def load_cards_data():
    """Charge les cartes en mémoire."""
    with open('cards.json', 'r', encoding='utf-8') as f:
        return json.load(f)

class CollectionCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.all_cards = load_cards_data()

        # Organisation des cartes pour les tirages
        self.cards_by_rarity = {
            "Commun": [c for c in self.all_cards if c['rarete'] == 'Commun'],
            "Peu Commun": [c for c in self.all_cards if c['rarete'] == 'Peu Commun'],
            "Rare": [c for c in self.all_cards if c['rarete'] == 'Rare'],
            "Épique": [c for c in self.all_cards if c['rarete'] == 'Épique'],
            "Légendaire": [c for c in self.all_cards if c['rarete'] == 'Légendaire']
        }
        
        # Calcul du total par club pour l'affichage
        self.cards_per_club_total = {}
        for card in self.all_cards:
            club = card['club']
            self.cards_per_club_total[club] = self.cards_per_club_total.get(club, 0) + 1
            
        # Création d'une map robuste qui accepte les ID en int ET en str
        self.card_map = {}
        for card in self.all_cards:
            self.card_map[card['id']] = card       # Clé originale (int ou str)
            self.card_map[str(card['id'])] = card  # Clé en string

    # === ÉVÉNEMENTS ===
    @commands.Cog.listener()
    async def on_member_join(self, member):
        """Pack de bienvenue."""
        if member.bot: return
        database.add_pack(member.id, 1)
        try:
            await member.send(f"👋 Bienvenue sur Handnews, {member.mention} ! Tu as reçu un **pack de bienvenue**. Fais `/ouvrir` !")
        except discord.errors.Forbidden: pass

    @commands.Cog.listener()
    async def on_message(self, message):
        """Gestion des points par message."""
        if message.author.bot or message.content.startswith(('/', '!')) or not message.guild:
            return

        paris_tz = pytz.timezone('Europe/Paris')
        now_paris = datetime.datetime.now(paris_tz)
        user_id = message.author.id

        user_data_row = database.get_user_data(user_id)
        if not user_data_row: return
        user_data = dict(user_data_row)

        # Message d'accueil (Onboarding)
        if user_data.get('has_received_onboarding', 0) == 0:
            try:
                onboarding_msg = (
                    "🎉 **Bienvenue dans le jeu de collection Handnews !** 🎉\n\n"
                    "1. Gagnez des points en parlant (1er message = 120pts !).\n"
                    f"2. Achetez des packs (`/pack` - {PACK_COST} pts).\n"
                    "3. Ouvrez-les (`/ouvrir`) et complétez l'album (`/collection`).\n"
                    "4. Recyclez les doublons (`/recycler`) pour créer les cartes manquantes (`/creer`)."
                )
                await message.author.send(onboarding_msg)
                database.set_onboarding_received(user_id)
            except discord.errors.Forbidden: pass

        today_str = now_paris.date().isoformat()
        
        # Bonus journalier
        if user_data['last_activity_date'] != today_str:
            database.reset_daily_and_add_first_bonus(user_id, DAILY_BONUS, POINTS_PER_MESSAGE, now_paris.isoformat())
            return
        
        # Cooldown anti-spam
        if user_data['last_message_time']:
            last_msg = datetime.datetime.fromisoformat(user_data['last_message_time'])
            if last_msg.tzinfo is None: last_msg = paris_tz.localize(last_msg)
            if (now_paris - last_msg).total_seconds() < MESSAGE_COOLDOWN: return

        # Limite quotidienne
        if user_data['daily_message_points'] >= MAX_DAILY_MESSAGE_POINTS: return

        database.update_on_message_activity(user_id, POINTS_PER_MESSAGE, now_paris.isoformat())

    # === COMMANDES SLASH ===

    @app_commands.command(name='aide', description="Liste des commandes.")
    async def help_command(self, interaction: discord.Interaction):
        embed = discord.Embed(title="📜 Aide - Collection", color=discord.Color.blue())
        cmd_list = [
            ("`/collection`", "Voir tes cartes."),
            ("`/points`", "Voir ton solde."),
            ("`/pack`", f"Acheter un pack ({PACK_COST} pts)."),
            ("`/ouvrir`", "Ouvrir un pack."),
            ("`/recycler`", "Vendre les doublons."),
            ("`/creer`", "Fabriquer une carte spécifique."),
            ("`/fragments`", "Voir les coûts de fabrication.")
        ]
        for name, val in cmd_list:
            embed.add_field(name=name, value=val, inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name='points', description="Ton solde de points et packs.")
    async def points_command(self, interaction: discord.Interaction):
        data = database.get_user_data(interaction.user.id)
        await interaction.response.send_message(
            f"💰 **{data['points']} points** │ 🎒 **{data['packs']} packs** │ ♻️ **{data['fragments']} fragments**", 
            ephemeral=True
        )

    @app_commands.command(name='pack', description=f"Acheter un pack ({PACK_COST} pts).")
    async def pack_command(self, interaction: discord.Interaction):
        uid = interaction.user.id
        pts = database.get_user_data(uid)['points']
        
        if pts >= PACK_COST:
            database.update_points(uid, -PACK_COST)
            database.add_pack(uid, 1)
            await interaction.response.send_message(f"🛍️ Pack acheté ! (`/ouvrir` pour l'utiliser)", ephemeral=True)
        else:
            await interaction.response.send_message(f"❌ Il te manque **{PACK_COST - pts} points**.", ephemeral=True)

    @app_commands.command(name='ouvrir', description="Ouvrir un pack.")
    async def open_command(self, interaction: discord.Interaction):
        uid = interaction.user.id
        
        # --- MODIFICATION ICI : Conversion explicite en dictionnaire ---
        # Cela permet d'utiliser la méthode .get() sans erreur
        user_data = dict(database.get_user_data(uid))
        
        if user_data['packs'] <= 0:
            return await interaction.response.send_message("❌ Tu n'as pas de pack. Fais `/pack`.", ephemeral=True)
        
        await interaction.response.send_message("🎉 Ouverture en cours...", ephemeral=True)
        database.remove_pack(uid, 1)
        
        # Tirage pondéré
        slot1 = random.choices([*self.cards_by_rarity["Commun"], *self.cards_by_rarity["Peu Commun"]], weights=[70]*len(self.cards_by_rarity["Commun"]) + [30]*len(self.cards_by_rarity["Peu Commun"]), k=1)[0]
        slot2 = random.choices([*self.cards_by_rarity["Commun"], *self.cards_by_rarity["Peu Commun"], *self.cards_by_rarity["Rare"]], weights=[30]*len(self.cards_by_rarity["Commun"]) + [50]*len(self.cards_by_rarity["Peu Commun"]) + [20]*len(self.cards_by_rarity["Rare"]), k=1)[0]
        slot3 = random.choices([*self.cards_by_rarity["Rare"], *self.cards_by_rarity["Épique"], *self.cards_by_rarity["Légendaire"]], weights=[45]*len(self.cards_by_rarity["Rare"]) + [35]*len(self.cards_by_rarity["Épique"]) + [20]*len(self.cards_by_rarity["Légendaire"]), k=1)[0]

        # Gestion Noël
        is_advent = False
        now = datetime.datetime.now(pytz.timezone('Europe/Paris'))
        # Active uniquement du 1er au 24 décembre
        if now.month == 12 and 1 <= now.day <= 24:
            today_str = now.date().isoformat()
            # Grâce à la conversion en dict plus haut, .get() fonctionne maintenant
            if user_data.get('last_advent_pack_date') != today_str:
                advent_card = self.card_map.get(f"noel_{now.day}")
                if advent_card:
                    slot1 = advent_card
                    database.set_advent_pack_opened(uid, today_str)
                    is_advent = True

        cards = [slot1, slot2, slot3]
        
        for i, card in enumerate(cards):
            database.add_card_to_collection(uid, card['id'])
            
            title = f"**{card['nom']}**"
            desc = f"**{card['rarete']}**\nClub : {card['club']}"
            if is_advent and i == 0:
                title = f"🎄 CALENDRIER : {card['nom']} 🎄"
                desc += "\n✨ *Carte du jour !* ✨"

            embed = discord.Embed(title=title, description=desc, color=RARITY_COLORS.get(card['rarete'], discord.Color.default()))
            embed.set_image(url=card['image_url'])
            await interaction.followup.send(embed=embed, ephemeral=True)

            # Annonce globale (sauf calendrier auto si on ne veut pas spammer)
            if card['rarete'] in ["Épique", "Légendaire", "Noël"] and ANNONCE_CHANNEL_ID != 0:
                if card['rarete'] == "Noël" and is_advent: continue
                try:
                    chan = self.bot.get_channel(ANNONCE_CHANNEL_ID)
                    if chan:
                        e = discord.Embed(title="✨ Gros Tirage !", description=f"**{interaction.user.mention}** a eu **{card['nom']}** !", color=embed.color)
                        e.set_image(url=card['image_url'])
                        await chan.send(embed=e)
                except: pass

    @commands.command(name='compensation')
    @commands.has_permissions(administrator=True)
    async def compensation_command(self, ctx, card_id: str = "noel_19"):
        """
        [Admin] Donne une carte à tous les joueurs qui ne l'ont pas.
        Usage: !compensation noel_19
        """
        # 1. Vérifier que la carte existe
        # On gère le cas où l'ID est un int ou un str
        target_card = self.card_map.get(card_id) or self.card_map.get(int(card_id) if card_id.isdigit() else card_id)
        
        if not target_card:
            # CORRECTION : pas de ephemeral=True ici
            await ctx.send(f"❌ Carte introuvable avec l'ID `{card_id}`.", delete_after=10)
            return

        embed = discord.Embed(
            title="🎁 Distribution de Compensation",
            description=f"Préparation de la distribution de **{target_card['nom']}**...",
            color=discord.Color.gold()
        )
        msg = await ctx.send(embed=embed)

        # 2. Exécuter la distribution en masse
        try:
            # On utilise str(card_id) pour être sûr que ça matche le format dans la DB
            count = database.mass_give_card_if_missing(card_id)
            
            embed.description = (
                f"✅ **Opération terminée !**\n\n"
                f"🃏 **Carte :** {target_card['nom']} (ID: {card_id})\n"
                f"👥 **Utilisateurs crédités :** {count}\n"
                f"ℹ️ *Les utilisateurs qui l'avaient déjà n'ont pas reçu de doublon.*"
            )
            embed.color = discord.Color.green()
            embed.set_image(url=target_card['image_url'])
            await msg.edit(embed=embed)
            
        except Exception as e:
            await msg.edit(content=f"❌ Une erreur est survenue : `{e}`", embed=None)

    # --- CLASSE D'AFFICHAGE (View) ---
    class CollectionView(discord.ui.View):
        current_club: str = None
        current_page: int = 0
    
        def __init__(self, author_id, user_collection_data, total_available_cards, cards_per_club_total):
            super().__init__(timeout=180)
            self.author_id = author_id
            self.collection = user_collection_data
            self.total_available_cards = total_available_cards
            self.cards_per_club_total = cards_per_club_total
            
            self.cards_by_club = {}
            for card in self.collection:
                club = card['club']
                if club not in self.cards_by_club:
                    self.cards_by_club[club] = []
                self.cards_by_club[club].append(card)
            
            self.club_select.options = self.create_select_options()
            if not self.collection:
                self.club_select.placeholder = "Ta collection est vide !"
                self.club_select.disabled = True
            self.update_buttons_state()
    
        def create_select_options(self):
            options = []
            for club, total_count in sorted(self.cards_per_club_total.items()):
                owned = len(self.cards_by_club.get(club, []))
                label = f"🎄 {club}" if club == "Légendes Starligue" else club
                emoji = "🎁" if club == "Légendes Starligue" else None
                options.append(discord.SelectOption(label=label, description=f"{owned} / {total_count}", value=club, emoji=emoji))
            return options[:25] # Limite Discord
    
        def update_buttons_state(self):
            has_cards = self.current_club and self.cards_by_club.get(self.current_club)
            self.prev_button.disabled = not has_cards or self.current_page == 0
            self.next_button.disabled = not has_cards or self.current_page >= len(self.cards_by_club[self.current_club]) - 1

        def get_emoji_safe(self, rarity):
            return {"Commun":"⬜", "Peu Commun":"🟩", "Rare":"🟦", "Épique":"🟪", "Légendaire":"🟨", "Noël":"🎄"}.get(rarity, "🔹")

        async def generate_embed(self):
            # --- VUE GLOBALE ---
            if not self.current_club:
                unique = len(self.collection)
                total = self.total_available_cards
                pct = (unique / total * 100) if total > 0 else 0
                
                # Barre harmonisée (Style footer)
                nb_filled = int(pct / 5)
                if pct > 0 and pct < 5: nb_filled = 1 # Au moins un bloc si on a des cartes
                if pct >= 100: nb_filled = 20
                
                bar = "▰" * nb_filled + "▱" * (20 - nb_filled)
                
                embed = discord.Embed(title="🗂️ Album de Collection", description="Choisis un club ci-dessous pour voir tes cartes.", color=discord.Color.dark_theme())
                embed.add_field(
                    name="📈 Progression Globale", 
                    value=f"```\n{bar} {pct:.1f}%\n```\nVous possédez **{unique}** / **{total}** cartes.", 
                    inline=False
                )
                return embed
            
            # --- VUE CLUB ---
            cards = self.cards_by_club.get(self.current_club)
            if not cards:
                return discord.Embed(title=f"📁 {self.current_club}", description="Aucune carte dans ce classeur.", color=discord.Color.dark_grey())

            card = cards[self.current_page]
            is_xmas = card['rarete'] == "Noël"
            color = RARITY_COLORS.get(card['rarete'], discord.Color.default())
            
            embed = discord.Embed(title=f"{'❄️' if is_xmas else '🃏'} {card['nom']}", color=color)
            embed.set_image(url=card['image_url'])
            
            emoji = self.get_emoji_safe(card['rarete'])
            
            embed.add_field(name=f"{'🎁' if is_xmas else '🤾'} Club", value=f"**{card['club']}**", inline=True)
            embed.add_field(name=f"{emoji} Rareté", value=f"**{card['rarete']}**", inline=True)
            # Suppression du champ "Niveau" (étoiles) comme demandé
            
            total = len(cards)
            # Barre de progression interne au club
            prog_pct = (self.current_page + 1) / total
            nb_filled = int(prog_pct * 10)
            if nb_filled == 0 and total > 0: nb_filled = 1
            
            prog_bar = "▰" * nb_filled + "▱" * (10 - nb_filled)
            embed.set_footer(text=f"Carte {self.current_page + 1}/{total} │ {prog_bar}")
            return embed
    
        @discord.ui.select(placeholder="Choisis un club...", row=0)
        async def club_select(self, interaction: discord.Interaction, select: discord.ui.Select):
            if interaction.user.id != self.author_id: return
            self.current_club = select.values[0]
            self.current_page = 0
            self.update_buttons_state()
            await interaction.response.edit_message(embed=await self.generate_embed(), view=self)
    
        @discord.ui.button(label="◀", style=discord.ButtonStyle.blurple, row=1)
        async def prev_button(self, interaction: discord.Interaction, button: discord.ui.Button):
            if interaction.user.id != self.author_id: return
            self.current_page -= 1
            self.update_buttons_state()
            await interaction.response.edit_message(embed=await self.generate_embed(), view=self)
    
        @discord.ui.button(label="▶", style=discord.ButtonStyle.blurple, row=1)
        async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
            if interaction.user.id != self.author_id: return
            self.current_page += 1
            self.update_buttons_state()
            await interaction.response.edit_message(embed=await self.generate_embed(), view=self)

    @app_commands.command(name='collection', description="Affiche ta collection.")
    async def collection_command(self, interaction: discord.Interaction):
        # Récupération sécurisée avec support int/str
        raw_ids = database.get_user_collection(interaction.user.id)
        user_cards = []
        for cid in set(raw_ids):
            if cid in self.card_map: user_cards.append(self.card_map[cid])
            elif str(cid) in self.card_map: user_cards.append(self.card_map[str(cid)])
        
        view = self.CollectionView(interaction.user.id, user_cards, len(self.all_cards), self.cards_per_club_total)
        await interaction.response.send_message(embed=await view.generate_embed(), view=view, ephemeral=True)

    @app_commands.command(name='album', description="Affiche l'album d'un club (visuels masqués si non possédé).")
    @app_commands.describe(club="Le club dont tu veux voir l'album")
    async def album_command(self, interaction: discord.Interaction, club: str):
        # Validation du club (sensible à la casse mais on va aider)
        club_found = None
        for c in self.cards_per_club_total.keys():
            if c.lower() == club.lower():
                club_found = c
                break
        
        if not club_found:
            # On propose une liste si non trouvé
            clubs_list = ", ".join(sorted(self.cards_per_club_total.keys())[:5]) + "..."
            return await interaction.response.send_message(f"❌ Club inconnu. Essaye parmi : {clubs_list}", ephemeral=True)
            
        await interaction.response.defer(ephemeral=True)
        
        # 1. Cartes du club
        cards_in_club = [c for c in self.all_cards if c['club'] == club_found]
        # Tri par rareté puis nom
        rarity_order = {"Commun": 1, "Peu Commun": 2, "Rare": 3, "Épique": 4, "Légendaire": 5, "Noël": 6}
        cards_in_club.sort(key=lambda x: (rarity_order.get(x['rarete'], 99), x['nom']))
        
        # 2. Collection user
        user_ids = database.get_user_collection(interaction.user.id)
        
        # 3. Génération
        try:
            image_buffer = await album_generator.generate_club_album(club_found, cards_in_club, user_ids)
            file = discord.File(fp=image_buffer, filename=f"album_{club_found}.png")
            
            # 4. Stats
            owned_count = sum(1 for c in cards_in_club if c['id'] in user_ids or str(c['id']) in [str(u) for u in user_ids])
            total_count = len(cards_in_club)
            pct = (owned_count / total_count * 100) if total_count > 0 else 0
            
            embed = discord.Embed(title=f"📖 Album : {club_found}", color=discord.Color.gold())
            embed.description = f"Progression : **{owned_count}/{total_count}** ({pct:.1f}%)"
            embed.set_image(url=f"attachment://album_{club_found}.png")
            
            await interaction.followup.send(embed=embed, file=file)
            
        except Exception as e:
            await interaction.followup.send(f"❌ Erreur lors de la génération de l'album : {e}")

    @album_command.autocomplete('club')
    async def album_club_autocomplete(self, interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        clubs = sorted(self.cards_per_club_total.keys())
        return [
            app_commands.Choice(name=c, value=c)
            for c in clubs if current.lower() in c.lower()
        ][:25]
    @app_commands.command(name='recycler', description="Échange doublons contre fragments.")
    async def recycle_command(self, interaction: discord.Interaction):
        ids = database.get_user_collection(interaction.user.id)
        if not ids: return await interaction.response.send_message("Rien à recycler.", ephemeral=True)
            
        counts = {}
        for cid in ids: counts[cid] = counts.get(cid, 0) + 1
        
        fragments = 0
        kept = []
        for cid, count in counts.items():
            kept.append(cid)
            if count > 1:
                card = self.card_map.get(cid) or self.card_map.get(str(cid))
                if card: fragments += FRAGMENT_VALUES.get(card['rarete'], 0) * (count - 1)
        
        if fragments == 0: return await interaction.response.send_message("Aucun doublon.", ephemeral=True)
        
        database.update_fragments(interaction.user.id, fragments)
        database.reset_and_set_collection(interaction.user.id, kept)
        
        embed = discord.Embed(title="♻️ Recyclage", description=f"Tu as gagné **{fragments} fragments**.", color=discord.Color.green())
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name='creer', description="Fabriquer une carte.")
    async def create_card_command(self, interaction: discord.Interaction, nom_de_la_carte: str):
        matches = [c for c in self.all_cards if nom_de_la_carte.lower() in c['nom'].lower()]
        
        if not matches: return await interaction.response.send_message("Carte introuvable.", ephemeral=True)
        if len(matches) > 1: return await interaction.response.send_message("Trop de résultats.", ephemeral=True)
        
        target = matches[0]
        # Vérif possession
        user_ids = database.get_user_collection(interaction.user.id)
        # Gestion int/str pour la comparaison
        if target['id'] in user_ids or str(target['id']) in [str(u) for u in user_ids]:
            return await interaction.response.send_message(f"Tu as déjà **{target['nom']}**.", ephemeral=True)

        # Calcul coût
        rarity_key = target['rarete'].lower().replace("é", "e").replace("ë", "e")
        cost = 0
        
        if target['rarete'] == "Noël":
            now = datetime.datetime.now()
            if now.month == 12 and now.day < 25:
                return await interaction.response.send_message("❄️ Attends le 25 décembre !", ephemeral=True)
            cost = JOKER_COSTS["noel"]
        elif rarity_key in JOKER_COSTS:
            cost = JOKER_COSTS[rarity_key]
        else:
            return await interaction.response.send_message("Cette rareté ne peut pas être créée.", ephemeral=True)

        user_frags = database.get_user_data(interaction.user.id)['fragments']
        if user_frags < cost:
            return await interaction.response.send_message(f"Il te faut **{cost} fragments** (Tu as {user_frags}).", ephemeral=True)
        
        database.update_fragments(interaction.user.id, -cost)
        database.add_card_to_collection(interaction.user.id, target['id'])
        
        e = discord.Embed(title="🃏 Carte Créée !", description=f"Bienvenue à **{target['nom']}** !", color=RARITY_COLORS.get(target['rarete']))
        e.set_image(url=target['image_url'])
        await interaction.response.send_message(embed=e, ephemeral=True)

    @app_commands.command(name='fragments', description="Infos sur le recyclage et la création.")
    async def fragments_command(self, interaction: discord.Interaction):
        user_frags = database.get_user_data(interaction.user.id)['fragments']
        embed = discord.Embed(title="♻️ Atelier Fragments", description=f"Solde : **{user_frags}** fragments", color=discord.Color.light_grey())
        
        rec_text = "\n".join([f"• {r}: **{v}**" for r, v in FRAGMENT_VALUES.items()])
        create_text = "\n".join([f"• {r.capitalize()}: **{c}**" for r, c in JOKER_COSTS.items()])
        
        embed.add_field(name="Gains (Recyclage)", value=rec_text, inline=True)
        embed.add_field(name="Coûts (Création)", value=create_text, inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name='top', description="Classement collection.")
    async def top_command(self, interaction: discord.Interaction):
        # On passe la liste des ID valides pour ne compter que les cartes qui existent encore
        valid_ids = [c['id'] for c in self.all_cards]
        data = database.get_leaderboard_data(valid_ids)
        clean_data = [d for d in data if d[0] not in LEADERBOARD_EXCLUDED_IDS][:10]

        total_cards = len(self.all_cards)
        
        desc = ""
        for i, (uid, count) in enumerate(clean_data, 1):
            m = interaction.guild.get_member(uid)
            name = m.display_name if m else "Inconnu"
            medal = "🥇" if i==1 else "🥈" if i==2 else "🥉" if i==3 else f"#{i}"
            desc += f"{medal} **{name}** : {count} / {total_cards} cartes\n"
            
        await interaction.response.send_message(embed=discord.Embed(title="🏆 Meilleurs Collectionneurs", description=desc or "Aucune donnée", color=discord.Color.gold()))

    @app_commands.command(name='topowned', description="[Admin] Cartes les plus courantes.")
    @app_commands.default_permissions(manage_guild=True)
    async def top_owned_command(self, interaction: discord.Interaction):
        await interaction.response.defer()
        with database.sqlite3.connect(database.DB_NAME) as con:
            cur = con.cursor()
            cur.execute("SELECT card_id, COUNT(DISTINCT user_id) as c FROM user_cards GROUP BY card_id ORDER BY c DESC")
            rows = cur.fetchall()
            
        res = {r: [] for r in ["Commun", "Peu Commun", "Rare", "Épique", "Légendaire"]}
        for cid, cnt in rows:
            c = self.card_map.get(cid) or self.card_map.get(str(cid))
            if c and c['rarete'] in res: res[c['rarete']].append((c['nom'], cnt))
            
        e = discord.Embed(title="📊 Stats Cartes", color=discord.Color.blue())
        for r, l in res.items():
            if l: e.add_field(name=r, value="\n".join([f"{n} ({c})" for n,c in l[:3]]), inline=False)
        await interaction.followup.send(embed=e)

    @app_commands.command(name='addpoints', description="[Admin] Donner des points.")
    @app_commands.default_permissions(manage_guild=True)
    async def add_points_command(self, interaction: discord.Interaction, membre: discord.Member, montant: int):
        database.update_points(membre.id, montant)
        await interaction.response.send_message(f"✅ **{montant} points** donnés à {membre.mention}.", ephemeral=True)
        try: await membre.send(f"🎁 Un admin t'a donné **{montant} points** !")
        except: pass

async def setup(bot):
    await bot.add_cog(CollectionCog(bot))
