import discord
from discord import app_commands
from discord.ext import commands, tasks
import os
import database
import json
import random
import asyncio
import datetime
import io
import pytz
import aiohttp
from utils import album_generator
from utils import card_renderer

# --- CONSTANTES & COULEURS ---
RARITY_COLORS = {
    "Commun": discord.Color.light_grey(),
    "Peu Commun": discord.Color.green(),
    "Rare": discord.Color.blue(),
    "Épique": discord.Color.purple(),
    "Légendaire": discord.Color.gold(),
    "Noël": discord.Color.from_rgb(220, 20, 60)
}

RARITY_EMOJI = {
    "Commun": "⬜", "Peu Commun": "🟩", "Rare": "🟦",
    "Épique": "🟪", "Légendaire": "🟨", "Noël": "🎄",
}

FRAGMENT_VALUES = {
    "Commun": 2,
    "Peu Commun": 5,
    "Rare": 10,
    "Épique": 30,
    "Légendaire": 100
}

JOKER_COSTS = {
    "rare": 200,
    "epique": 400,      # 600 → 400
    "legendaire": 1400, # recalibré avec le boost ×3 C/PC pour garder ~6 mois de complétion
    "noel": 100
}

# Taux de drop par slot (Saison 2) — appliqués PAR RARETÉ, pas par carte.
# Calibrés pour : 1 Légendaire tirée ~tous les 20 j à 3 packs/jour,
# et complétion totale médiane ~180 j (6 mois) craft compris.
SLOT1_RATES = {"Commun": 70, "Peu Commun": 30}
SLOT2_RATES = {"Commun": 40, "Peu Commun": 50, "Rare": 10}
SLOT3_RATES = {"Rare": 81.5, "Épique": 17, "Légendaire": 1.5}

# Ordre de rareté pour le crescendo de révélation (la meilleure carte en dernier)
RARITY_ORDER = {"Commun": 0, "Peu Commun": 1, "Rare": 2, "Épique": 3, "Légendaire": 4, "Noël": 5}
# Raretés annoncées dans le salon public lors d'un gros tirage
ANNOUNCE_RARITIES = {"Épique", "Légendaire", "Noël"}
# À partir de cette rareté, on joue un petit suspense avant la révélation
RARE_REVEAL_THRESHOLD = RARITY_ORDER["Épique"]
# Garde-fou : nombre max de packs ouverts en une seule action
MAX_BULK_OPEN = 20
# Garde-fou : nombre max de packs achetés en une seule commande
MAX_BULK_BUY = 100
# Jusqu'à ce nombre de packs, on joue l'ouverture détaillée pack par pack.
# Au-delà, on bascule sur une ouverture groupée (récap global, pas de spam).
DETAILED_OPEN_MAX = 5
# Rythme de l'animation d'ouverture (en secondes) — laisse le temps de lire
REVEAL_TEAR_DELAY = 1.1       # déchirure de l'emballage
REVEAL_SUSPENSE_DELAY = 1.7   # suspense avant une carte rare
REVEAL_CARD_DELAY = 2.1       # pause entre chaque carte révélée
REVEAL_PACK_GAP = 1.3         # respiration entre deux packs (mode détaillé)

# --- CONFIGURATION ---
ANNONCE_CHANNEL_ID = 1405724982436167762 
PACK_COST = 150
DAILY_BONUS = 100
POINTS_PER_MESSAGE = 20
MAX_DAILY_MESSAGE_POINTS = 300
# --- ANTI-TRICHE « écrire puis supprimer » ---
# Les points d'un message ne sont pas crédités tout de suite : ils MÛRISSENT pendant
# 12 heures. Tant qu'ils n'ont pas mûri ils ne sont ni dépensables ni comptés dans le
# solde, donc il n'existe aucun instant où l'on pourrait convertir en packs les points
# d'un message qu'on s'apprête à effacer. Supprimer avant maturité = zéro point ;
# supprimer après = le solde est débité (il peut devenir négatif).
# Le quota quotidien, lui, est consommé dès l'écriture et n'est JAMAIS rendu : sinon
# supprimer libérerait de la place pour regagner, et la triche deviendrait rentable.
#
# 12 h, c'est ce qui remplace l'ancien cooldown de 10 secondes : plafonner sa journée
# reste rapide, mais il faut ensuite LAISSER ses messages en place une demi-journée.
# Un spam de quinze lignes ne peut plus être effacé discrètement dans la foulée — il
# reste sous les yeux des modérateurs le temps de mûrir, ou il ne rapporte rien.
POINT_MATURATION_MINUTES = int(os.getenv("POINT_MATURATION_MINUTES", "720"))
# Durée de vie de la ligne comptable d'un message, donc fenêtre pendant laquelle le
# supprimer peut encore reprendre les points. Passé ce délai la ligne est purgée et le
# message est payé pour de bon : ça borne la table, et ça évite qu'un ménage dans un
# vieux salon coûte des points gagnés il y a des mois.
# À garder nettement au-dessus de POINT_MATURATION_MINUTES : la purge ne touche que les
# lignes déjà mûres (`muri = 1`), donc descendre en dessous ne priverait personne de son
# crédit, mais réduirait la fenêtre de reprise à zéro — supprimer après maturité
# redeviendrait gratuit, et toute la protection tomberait.
POINT_CLAWBACK_HOURS = int(os.getenv("POINT_CLAWBACK_HOURS", "48"))
LEADERBOARD_EXCLUDED_IDS = [133711821214449665]
# Boost de fin de collection basse rareté (Saison 2) :
# les cartes Commun / Peu Commun MANQUANTES pèsent ×3 dans le tirage
# dès que le joueur possède ≥ 80% de l'ensemble Commun + Peu Commun.
# Les autres raretés restent en tirage uniforme (leur pity passe par le craft /creer).
LOW_TIER_RARITIES = ("Commun", "Peu Commun")
LOW_TIER_MISSING_WEIGHT = 3
LOW_TIER_THRESHOLD = 0.80

def load_cards_data():
    """Charge les cartes en mémoire."""
    with open('cards.json', 'r', encoding='utf-8') as f:
        return json.load(f)


# --- SAISON (reprise du 4 septembre) ---------------------------------------
# Les cartes des saisons passées restent VISIBLES dans la collection — c'est
# l'archive, et elle est complète à un exemplaire par carte — mais elles ne sont
# plus tirables en pack, plus créables au craft, et plus alignables en duel.
# Une carte sans champ « saison » date d'avant tools/publier_s2.py : c'est une S1.
SAISON_COURANTE = 2


def saison_de(card):
    return card.get('saison', 1)


def cartes_de_la_saison(cards, saison=SAISON_COURANTE):
    return [c for c in cards if saison_de(c) == saison]


def saison_en_cours(cards):
    """La saison qui SE JOUE réellement.

    SAISON_COURANTE dès qu'elle est publiée dans cards.json, sinon la plus récente
    qui s'y trouve. Ce repli existe pour une raison précise : si le code part en
    production avant tools/publier_s2.py, le pool de tirage serait VIDE — /pack
    planterait sur un tirage sans candidat et plus personne ne pourrait aligner
    une équipe. Avec le repli, le bot continue simplement la saison précédente
    jusqu'à ce que les cartes arrivent."""
    if any(saison_de(c) == SAISON_COURANTE for c in cards):
        return SAISON_COURANTE
    dispo = {saison_de(c) for c in cards}
    return max(dispo) if dispo else SAISON_COURANTE

class RecycleView(discord.ui.View):
    """Recyclage sélectif (Saison 2) : choisir les doublons à recycler, ou tout recycler."""

    def __init__(self, cog, user_id, dups):
        super().__init__(timeout=120)
        self.cog = cog
        self.user_id = user_id
        self.dups = dups          # {card_id: nombre total possédé}
        self.selected = []
        self.done = False
        self.id_by_str = {}

        options = []
        for cid, cnt in list(dups.items())[:25]:
            card = cog.get_card_safe(cid)
            self.id_by_str[str(cid)] = cid
            extra = cnt - 1
            frags = (FRAGMENT_VALUES.get(card['rarete'], 0) * extra) if card else 0
            options.append(discord.SelectOption(
                label=(card['nom'] if card else str(cid))[:100],
                value=str(cid),
                description=f"{extra} doublon(s) → {frags} fragments",
                emoji=RARITY_EMOJI.get(card['rarete'], "🔹") if card else "🔹",
            ))
        self.pick.options = options
        self.pick.min_values = 0
        self.pick.max_values = len(options)

    async def interaction_check(self, interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Ce n'est pas ton recyclage.", ephemeral=True)
            return False
        return True

    def _frags_all(self):
        removed = {cid: cnt - 1 for cid, cnt in self.dups.items()}
        return self.cog._fragments_for_removed(removed)

    def build_embed(self):
        truncated = len(self.dups) > 25
        e = discord.Embed(
            title="♻️ Recyclage sélectif",
            description=("Choisis les doublons à recycler, ou recycle tout.\n"
                         "⚠️ Garde tes doublons si tu comptes les **échanger** !"),
            color=discord.Color.green(),
        )
        e.set_footer(text=f"Tout recycler = {self._frags_all()} fragments"
                          + (" · (25 premiers affichés dans la liste)" if truncated else ""))
        return e

    async def _finish(self, interaction, title, fragments):
        self.done = True
        for c in self.children:
            c.disabled = True
        embed = discord.Embed(title=title, description=f"Tu as gagné **{fragments} fragments**.", color=discord.Color.green())
        await interaction.response.edit_message(embed=embed, view=self)
        self.stop()

    @discord.ui.select(placeholder="Choisis les doublons à recycler…", row=0)
    async def pick(self, interaction, select):
        self.selected = select.values
        await interaction.response.defer()

    @discord.ui.button(label="Recycler la sélection", emoji="♻️", style=discord.ButtonStyle.green, row=1)
    async def recycle_sel_btn(self, interaction, button):
        if self.done:
            return
        cids = [self.id_by_str[v] for v in self.selected if v in self.id_by_str]
        if not cids:
            return await interaction.response.send_message("Sélectionne au moins une carte.", ephemeral=True)
        fragments = self.cog.recycle_selected(self.user_id, cids)
        await self._finish(interaction, "♻️ Doublons recyclés", fragments)

    @discord.ui.button(label="Tout recycler", emoji="🗑️", style=discord.ButtonStyle.grey, row=1)
    async def recycle_all_btn(self, interaction, button):
        if self.done:
            return
        fragments = self.cog.recycle_all(self.user_id)
        await self._finish(interaction, "♻️ Tous les doublons recyclés", fragments)


class PackOpenView(discord.ui.View):
    """Boutons pour enchaîner les ouvertures de packs automatiquement."""

    def __init__(self, cog, user_id):
        super().__init__(timeout=180)
        self.cog = cog
        self.user_id = user_id

    async def interaction_check(self, interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Ce ne sont pas tes packs !", ephemeral=True)
            return False
        return True

    def refresh(self, packs_left):
        """Met à jour libellés et états des boutons selon les packs restants."""
        packs_left = max(0, packs_left)
        self.open_one.disabled = packs_left <= 0
        self.open_all.disabled = packs_left <= 0
        self.open_one.label = f"🎴 Ouvrir encore ({packs_left})"
        self.open_all.label = f"📦 Tout ouvrir ({min(packs_left, MAX_BULK_OPEN)})"

    @discord.ui.button(label="🎴 Ouvrir encore", style=discord.ButtonStyle.green, row=0)
    async def open_one(self, interaction, button):
        await self.cog._run_button_open(interaction, self, 1)

    @discord.ui.button(label="📦 Tout ouvrir", style=discord.ButtonStyle.blurple, row=0)
    async def open_all(self, interaction, button):
        await self.cog._run_button_open(interaction, self, MAX_BULK_OPEN)


def _delai_maturation():
    """Le délai de maturation, dit en français plutôt qu'en minutes brutes."""
    h, m = divmod(POINT_MATURATION_MINUTES, 60)
    if h and m:
        return f"{h} h {m:02d}"
    if h:
        return f"{h} heure" + ("s" if h > 1 else "")
    return f"{m} minutes"


class CollectionCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.all_cards = load_cards_data()
        # Ce qui se JOUE cette saison. Tout le reste (tirage, craft, progression,
        # classement) s'y rapporte ; seul card_map garde les cartes archivées, sans
        # quoi une collection de la saison passée ne se résoudrait plus (nom, rareté,
        # image) et l'album afficherait des trous à la place.
        self.saison = saison_en_cours(self.all_cards)
        if self.saison != SAISON_COURANTE:
            print(f"⚠️  (CARTES) Aucune carte de la saison {SAISON_COURANTE} dans "
                  f"cards.json — le bot continue la saison {self.saison}. "
                  f"Lance tools/publier_s2.py puis redémarre.")
        self.cards_saison = cartes_de_la_saison(self.all_cards, self.saison)

        # Organisation des cartes pour les tirages (saison en cours uniquement)
        self.cards_by_rarity = {
            "Commun": [c for c in self.cards_saison if c['rarete'] == 'Commun'],
            "Peu Commun": [c for c in self.cards_saison if c['rarete'] == 'Peu Commun'],
            "Rare": [c for c in self.cards_saison if c['rarete'] == 'Rare'],
            "Épique": [c for c in self.cards_saison if c['rarete'] == 'Épique'],
            "Légendaire": [c for c in self.cards_saison if c['rarete'] == 'Légendaire']
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

    def get_weighted_pool(self, user_id: int, cards_pool: list) -> tuple:
        """Retourne (cards, weights) pour le tirage dans une rareté.
        Poids ×3 sur les Commun/Peu Commun manquantes une fois que le joueur
        possède ≥ 80% de l'ensemble Commun + Peu Commun ; uniforme sinon."""
        user_collection = database.get_user_collection(user_id)
        user_collection_set = set(str(cid) for cid in user_collection)

        unique_cards = {c['id']: c for c in cards_pool}.values()

        # Complétion calculée sur le sous-ensemble Commun + Peu Commun uniquement,
        # et SUR LA SAISON EN COURS seulement : compter l'archive activerait le boost
        # dès le premier pack (une collection S1 complète, c'est 166 basses raretés
        # pour 173 en S2), alors que le joueur repart de zéro cette saison.
        low_tier_total = sum(len(self.cards_by_rarity.get(r, [])) for r in LOW_TIER_RARITIES)
        low_tier_owned = 0
        for cid in user_collection_set:
            card = self.card_map.get(cid)
            if (card and card.get('rarete') in LOW_TIER_RARITIES
                    and saison_de(card) == self.saison):
                low_tier_owned += 1
        boost_active = low_tier_total > 0 and (low_tier_owned / low_tier_total) >= LOW_TIER_THRESHOLD

        cards = []
        weights = []
        for card in unique_cards:
            cards.append(card)
            is_missing = str(card['id']) not in user_collection_set
            if boost_active and is_missing and card.get('rarete') in LOW_TIER_RARITIES:
                weights.append(LOW_TIER_MISSING_WEIGHT)
            else:
                weights.append(1)

        return cards, weights

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
            # La remise à zéro quotidienne est immédiate, les points partent en
            # maturation : le bonus de 120 doit être aussi révocable que le reste,
            # sinon il suffirait d'un message par jour, supprimé aussitôt.
            database.reset_daily_and_add_first_bonus(
                user_id, DAILY_BONUS, POINTS_PER_MESSAGE, now_paris.isoformat(), credit=False)
            database.add_pending_message_points(
                message.id, user_id, message.channel.id,
                DAILY_BONUS + POINTS_PER_MESSAGE, now_paris.isoformat())
            return
        
        # Plus de cooldown entre deux messages : il ne servait plus à rien. Ce qui
        # freine la triche maintenant, ce n'est pas le rythme d'écriture mais le fait
        # de devoir laisser ses messages en ligne 12 h pour être payé. Le plafond
        # quotidien reste le seul garde-fou sur le volume.

        # Limite quotidienne
        if user_data['daily_message_points'] >= MAX_DAILY_MESSAGE_POINTS: return

        # Le quota est consommé maintenant, les points murissent à part.
        database.update_on_message_activity(
            user_id, POINTS_PER_MESSAGE, now_paris.isoformat(), credit=False)
        database.add_pending_message_points(
            message.id, user_id, message.channel.id, POINTS_PER_MESSAGE, now_paris.isoformat())

    # === MATURATION DES POINTS & RÉVOCATION ===

    async def cog_load(self):
        self.mature_points_loop.start()

    async def cog_unload(self):
        self.mature_points_loop.cancel()

    @tasks.loop(seconds=60)
    async def mature_points_loop(self):
        """Fait mûrir les points dont le délai est écoulé, si le message existe encore.

        La vérification par `fetch_message` est une ceinture en plus de la bretelle :
        l'écoute des suppressions suffit en temps normal, mais si le bot était hors
        ligne au moment de l'effacement, l'événement a été perdu — sans ce contrôle,
        il suffirait de couper au bon moment pour que la triche repasse.
        """
        paris_tz = pytz.timezone('Europe/Paris')
        now = datetime.datetime.now(paris_tz)
        due_before = (now - datetime.timedelta(minutes=POINT_MATURATION_MINUTES)).isoformat()
        for row in database.get_due_message_points(due_before, limit=100):
            if not await self._message_still_exists(row['channel_id'], row['message_id']):
                database.revoke_message_points(row['message_id'])
                continue
            database.credit_message_points(row['message_id'])
        database.purge_matured_message_points(
            (now - datetime.timedelta(hours=POINT_CLAWBACK_HOURS)).isoformat())

    @mature_points_loop.before_loop
    async def before_mature_points_loop(self):
        await self.bot.wait_until_ready()

    async def _message_still_exists(self, channel_id, message_id):
        """True si le message est toujours là. En cas de doute (salon introuvable,
        permission manquante, coupure réseau) on répond True : mieux vaut créditer un
        message effacé que spolier quelqu'un pour une panne."""
        try:
            channel = self.bot.get_channel(channel_id) or await self.bot.fetch_channel(channel_id)
            await channel.fetch_message(message_id)
            return True
        except discord.NotFound:
            return False
        except (discord.Forbidden, discord.HTTPException, AttributeError):
            return True

    @commands.Cog.listener()
    async def on_raw_message_delete(self, payload):
        """Message supprimé : les points partent avec lui.

        On écoute la version RAW parce que `on_message_delete` ne se déclenche que
        pour les messages encore en cache — c'est-à-dire pas ceux d'hier, et pas
        après un redémarrage.
        """
        database.revoke_message_points(payload.message_id)

    @commands.Cog.listener()
    async def on_raw_bulk_message_delete(self, payload):
        """Purge groupée (modération, nettoyage de salon) : même traitement."""
        database.revoke_message_points(list(payload.message_ids))

    # === COMMANDES SLASH ===

    @app_commands.command(name='aide', description="Liste des commandes.")
    async def help_command(self, interaction: discord.Interaction):
        embed = discord.Embed(title="📜 Aide - Collection", color=discord.Color.blue())
        cmd_list = [
            ("`/collection`", "Voir tes cartes."),
            ("`/points`", "Voir ton solde."),
            ("`/pack`", f"Acheter un pack ({PACK_COST} pts)."),
            ("`/ouvrir`", "Ouvrir un ou plusieurs packs (`/ouvrir nombre:5`)."),
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
        pending = database.get_pending_message_points(interaction.user.id)
        # Les points en attente sont annoncés à part : ils ne sont pas dépensables
        # tant qu'ils n'ont pas mûri, autant que ce soit clair plutôt que de laisser
        # croire à un solde qui refuse d'acheter.
        attente = (f"\n⏳ **{pending} points** en cours d'acquisition "
                   f"(disponibles {_delai_maturation()} après le message, "
                   f"perdus si tu le supprimes)." if pending else "")
        solde = (f"💰 **{data['points']} points** │ 🎒 **{data['packs']} packs** │ "
                 f"♻️ **{data['fragments']} fragments**")
        if data['points'] < 0:
            solde += ("\n⚠️ Solde négatif : des points ont été repris parce que les "
                      "messages correspondants ont été supprimés.")
        await interaction.response.send_message(solde + attente, ephemeral=True)

    @app_commands.command(name='pack', description=f"Acheter un ou plusieurs packs ({PACK_COST} pts/pack).")
    @app_commands.describe(quantite="Nombre de packs, ou « tout » pour tout dépenser (défaut : 1).")
    async def pack_command(self, interaction: discord.Interaction, quantite: str = "1"):
        uid = interaction.user.id
        pts = database.get_user_data(uid)['points']
        affordable = pts // PACK_COST

        # Parse de la quantité : un nombre, ou un mot-clé pour tout dépenser
        token = quantite.strip().lower()
        spend_all = token in ("tout", "max", "all")
        if not spend_all:
            if not token.isdigit() or int(token) < 1:
                return await interaction.response.send_message(
                    "❌ Indique un nombre de packs (ex. `5`) ou `tout` pour tout dépenser.",
                    ephemeral=True)
            requested = int(token)
        else:
            requested = affordable

        if affordable <= 0:
            return await interaction.response.send_message(
                f"❌ Il te manque **{PACK_COST - pts} points** pour acheter un pack.", ephemeral=True)

        count = min(requested, affordable, MAX_BULK_BUY)
        cost = count * PACK_COST

        database.update_points(uid, -cost)
        database.add_pack(uid, count)
        new_pts = pts - cost

        note = ""
        if not spend_all and count < requested:
            reasons = []
            if affordable < requested:
                reasons.append(f"points insuffisants (max {affordable})")
            if requested > MAX_BULK_BUY:
                reasons.append(f"limite {MAX_BULK_BUY}/achat")
            note = f"\nℹ️ Demandé {requested}, acheté {count} — {', '.join(reasons)}."

        await interaction.response.send_message(
            f"🛍️ **{count} pack(s)** acheté(s) pour **{cost} pts** !\n"
            f"💰 Solde : **{new_pts} pts** · 🎒 ouvre avec `/ouvrir nombre:{count}`.{note}",
            ephemeral=True
        )

    @app_commands.command(name='ouvrir', description="Ouvrir un ou plusieurs packs.")
    @app_commands.describe(nombre="Combien de packs ouvrir d'un coup (défaut : 1).")
    async def open_command(self, interaction: discord.Interaction, nombre: int = 1):
        uid = interaction.user.id
        packs_owned = database.get_packs(uid)

        if packs_owned <= 0:
            return await interaction.response.send_message("❌ Tu n'as pas de pack. Fais `/pack`.", ephemeral=True)

        count = max(1, min(nombre, packs_owned, MAX_BULK_OPEN))
        await interaction.response.defer(ephemeral=True)

        # Débit ATOMIQUE : le solde lu plus haut peut déjà être périmé (le defer
        # rend la main à l'event loop). Si un autre clic est passé entre-temps,
        # consume_packs renvoie False et on n'ouvre rien.
        if not database.consume_packs(uid, count):
            return await interaction.followup.send(
                "❌ Solde insuffisant (packs déjà ouverts ailleurs ?).", ephemeral=True
            )

        msg = await interaction.followup.send(
            embed=discord.Embed(title="🎴 Préparation du pack…", color=discord.Color.dark_theme()),
            ephemeral=True, wait=True
        )

        async def edit(embed, file=None):
            await msg.edit(embed=embed, attachments=[file] if file else [])

        packs = await self._animate_open(edit, uid, count, allow_advent=True)
        await self._announce_big_pulls(interaction.user, packs)

        view = PackOpenView(self, uid)
        view.refresh(packs_owned - count)
        await msg.edit(view=view)

    # === MOTEUR D'OUVERTURE DE PACKS ===

    async def _run_button_open(self, interaction: discord.Interaction, view: "PackOpenView", requested: int):
        """Ouverture déclenchée par un bouton : ré-édite le message d'origine."""
        uid = interaction.user.id
        packs_owned = database.get_packs(uid)
        if packs_owned <= 0:
            view.refresh(0)
            return await interaction.response.edit_message(view=view)

        count = max(1, min(requested, packs_owned, MAX_BULK_OPEN))
        await interaction.response.defer()

        # Débit ATOMIQUE : protège du double-clic sur le bouton.
        if not database.consume_packs(uid, count):
            view.refresh(database.get_packs(uid))
            return await interaction.edit_original_response(view=view)

        async def edit(embed, file=None):
            await interaction.edit_original_response(embed=embed, attachments=[file] if file else [])

        packs = await self._animate_open(edit, uid, count, allow_advent=True)
        await self._announce_big_pulls(interaction.user, packs)

        view.refresh(packs_owned - count)
        await interaction.edit_original_response(view=view)

    def _draw_slot(self, uid, rarity_weights: dict):
        """Tire d'abord la rareté selon les taux exacts, puis une carte dans cette rareté
        (bonus ×3 pour les cartes manquantes, sans déformer les taux entre raretés)."""
        rarities = list(rarity_weights.keys())
        rarity = random.choices(rarities, weights=[rarity_weights[r] for r in rarities], k=1)[0]
        pool, w = self.get_weighted_pool(uid, self.cards_by_rarity[rarity])
        return random.choices(pool, weights=w, k=1)[0]

    def _draw_pack(self, uid):
        """Tire les 3 cartes d'un pack. Taux calibrés Saison 2 :
        1 Légendaire ~tous les 20 jours, complétion ~7 mois à 3 packs/jour."""
        slot1 = self._draw_slot(uid, SLOT1_RATES)
        slot2 = self._draw_slot(uid, SLOT2_RATES)
        slot3 = self._draw_slot(uid, SLOT3_RATES)
        return [slot1, slot2, slot3]

    def _maybe_advent(self, uid):
        """Renvoie la carte du calendrier de l'avent du jour si éligible, sinon None."""
        now = datetime.datetime.now(pytz.timezone('Europe/Paris'))
        if not (now.month == 12 and 1 <= now.day <= 24):
            return None
        today_str = now.date().isoformat()
        if dict(database.get_user_data(uid)).get('last_advent_pack_date') == today_str:
            return None
        advent_card = self.card_map.get(f"noel_{now.day}")
        if advent_card:
            database.set_advent_pack_opened(uid, today_str)
        return advent_card

    def _open_batch(self, uid, count, allow_advent_first):
        """Ouvre `count` packs en base. Retourne une liste de packs,
        chaque pack étant une liste de {card, is_new}."""
        owned = set(str(c) for c in database.get_user_collection(uid))
        packs = []
        for i in range(count):
            cards = self._draw_pack(uid)
            if allow_advent_first and i == 0:
                advent = self._maybe_advent(uid)
                if advent:
                    cards[0] = advent
            pack = []
            for card in cards:
                cid = str(card['id'])
                is_new = cid not in owned
                if is_new:
                    owned.add(cid)
                database.add_card_to_collection(uid, card['id'])
                pack.append({"card": card, "is_new": is_new})
            packs.append(pack)
        return packs

    def _card_line(self, card, is_new):
        """Une ligne de carte avec son tag neuf / doublon."""
        emoji = RARITY_EMOJI.get(card['rarete'], "🔹")
        if is_new:
            tag = "✨ **NOUVELLE !**"
        else:
            frags = FRAGMENT_VALUES.get(card['rarete'], 0)
            tag = f"♻️ doublon (+{frags} frag.)" if frags else "♻️ doublon"
        return f"{emoji} **{card['nom']}** · {card['rarete']} — {tag}"

    def _album_progress_text(self, uid):
        ids_saison = {str(c['id']) for c in self.cards_saison}
        unique = len({str(c) for c in database.get_user_collection(uid)} & ids_saison)
        total = len(self.cards_saison)
        pct = (unique / total * 100) if total else 0
        return f"📈 Album : {unique}/{total} ({pct:.0f}%)"

    async def _animate_open(self, edit, uid, count, allow_advent):
        packs = self._open_batch(uid, count, allow_advent)
        await self._prewarm_cards(packs)
        if count <= DETAILED_OPEN_MAX:
            await self._reveal_each(edit, uid, packs)
        else:
            await self._reveal_bulk(edit, uid, packs)
        return packs

    async def _prewarm_cards(self, packs):
        """Pré-rend (et met en cache disque) toutes les cartes du lot avec une seule
        session réseau, pour que le reveal lise ensuite depuis le cache."""
        cards_by_id = {str(e['card']['id']): e['card'] for pack in packs for e in pack}
        try:
            async with aiohttp.ClientSession() as session:
                await asyncio.gather(
                    *[card_renderer.get_card_bytes(c, session) for c in cards_by_id.values()],
                    return_exceptions=True,
                )
        except Exception:
            pass  # le reveal retombera sur l'image brute si le rendu échoue

    async def _card_image(self, card):
        """Renvoie (discord.File, url) pour l'embed : la carte composée si dispo,
        sinon l'image brute en fallback."""
        try:
            data = await card_renderer.get_card_bytes(card)
        except Exception:
            data = None
        if data:
            return discord.File(io.BytesIO(data), filename="card.png"), "attachment://card.png"
        return None, card.get('image_url')

    async def _reveal_each(self, edit, uid, packs):
        """Ouverture détaillée : révèle chaque pack l'un après l'autre (1 à 5 packs)."""
        total = len(packs)
        for idx, pack in enumerate(packs):
            await self._reveal_single(edit, uid, pack,
                                      pack_index=idx + 1, pack_total=total,
                                      final=(idx == total - 1))
            if idx != total - 1:
                await asyncio.sleep(REVEAL_PACK_GAP)

    async def _reveal_single(self, edit, uid, pack, pack_index=1, pack_total=1, final=True):
        """Révélation dramatique d'un pack : crescendo de rareté, une carte à la fois."""
        ordered = sorted(pack, key=lambda e: RARITY_ORDER.get(e['card']['rarete'], 0))
        n = len(ordered)
        new_count = sum(1 for e in pack if e['is_new'])
        multi = pack_total > 1
        prefix = f"Pack {pack_index}/{pack_total} · " if multi else ""

        await edit(discord.Embed(title=f"🎴 {prefix}Ouverture du pack…",
                                 description="Tu déchires l'emballage… 🤞",
                                 color=discord.Color.dark_theme()))
        await asyncio.sleep(REVEAL_TEAR_DELAY)

        lines = []
        for i, entry in enumerate(ordered):
            card, is_new = entry['card'], entry['is_new']
            if RARITY_ORDER.get(card['rarete'], 0) >= RARE_REVEAL_THRESHOLD:
                await edit(discord.Embed(title="✨ Quelque chose brille…", description="Une carte rare se révèle ! 👀",
                                         color=RARITY_COLORS.get(card['rarete'], discord.Color.gold())))
                await asyncio.sleep(REVEAL_SUSPENSE_DELAY)

            lines.append(self._card_line(card, is_new))
            title = ("🎄 " if card['rarete'] == "Noël" else "🃏 ") + card['nom']
            emb = discord.Embed(title=title, description="\n".join(lines),
                                color=RARITY_COLORS.get(card['rarete'], discord.Color.default()))
            file, url = await self._card_image(card)
            emb.set_image(url=url)
            last_card = i == n - 1
            if last_card and final:
                emb.set_footer(text=f"✨ {new_count} nouvelle(s) · ♻️ {n - new_count} doublon(s) · {self._album_progress_text(uid)}")
            elif last_card and multi:
                emb.set_footer(text=f"Pack {pack_index}/{pack_total} terminé · ✨ {new_count} nouvelle(s) · ♻️ {n - new_count} doublon(s)")
            else:
                emb.set_footer(text=f"{prefix}Carte {i + 1}/{n}…")
            await edit(emb, file)
            await asyncio.sleep(REVEAL_CARD_DELAY)

    async def _reveal_bulk(self, edit, uid, packs):
        """Ouverture groupée : suspense court puis récap global (pas de spam)."""
        entries = [e for pack in packs for e in pack]
        new_entries = [e for e in entries if e['is_new']]
        dup_entries = [e for e in entries if not e['is_new']]
        frags = sum(FRAGMENT_VALUES.get(e['card']['rarete'], 0) for e in dup_entries)
        best = max(entries, key=lambda e: RARITY_ORDER.get(e['card']['rarete'], 0))
        best_color = RARITY_COLORS.get(best['card']['rarete'], discord.Color.blurple())

        await edit(discord.Embed(title=f"📦 Ouverture de {len(packs)} packs…",
                                 description="Ça déchire de partout ! 🎴🎴🎴", color=discord.Color.dark_theme()))
        await asyncio.sleep(1.0)

        if RARITY_ORDER.get(best['card']['rarete'], 0) >= RARE_REVEAL_THRESHOLD:
            await edit(discord.Embed(title="✨ Une pépite est apparue dans le lot…", color=best_color))
            await asyncio.sleep(1.2)

        emb = discord.Embed(title=f"📦 {len(packs)} packs ouverts !", color=best_color)
        best_file, best_url = await self._card_image(best['card'])
        emb.set_image(url=best_url)
        emb.add_field(name="🏆 Meilleure carte", value=self._card_line(best['card'], best['is_new']), inline=False)

        if new_entries:
            new_sorted = sorted(new_entries, key=lambda e: -RARITY_ORDER.get(e['card']['rarete'], 0))
            shown = new_sorted[:12]
            txt = "\n".join(self._card_line(e['card'], True) for e in shown)
            if len(new_sorted) > len(shown):
                txt += f"\n… et **+{len(new_sorted) - len(shown)}** autres nouvelles"
            emb.add_field(name=f"✨ Nouvelles cartes ({len(new_entries)})", value=txt, inline=False)
        else:
            emb.add_field(name="✨ Nouvelles cartes", value="Aucune cette fois… 😅", inline=False)

        emb.add_field(name="♻️ Doublons",
                      value=f"{len(dup_entries)} doublon(s) · ~{frags} fragments en recyclant (`/recycler`)",
                      inline=False)
        emb.set_footer(text=self._album_progress_text(uid))
        await edit(emb, best_file)

    async def _announce_big_pulls(self, member, packs):
        """Annonce les plus gros tirages dans le salon public (cappé pour éviter le spam)."""
        if not ANNONCE_CHANNEL_ID:
            return
        chan = self.bot.get_channel(ANNONCE_CHANNEL_ID)
        if not chan:
            return
        bigs = [e for pack in packs for e in pack if e['card']['rarete'] in ANNOUNCE_RARITIES]
        # Priorité aux nouvelles cartes puis à la rareté la plus haute, max 3 annonces
        bigs.sort(key=lambda e: (e['is_new'], RARITY_ORDER.get(e['card']['rarete'], 0)), reverse=True)
        for e in bigs[:3]:
            card = e['card']
            try:
                em = discord.Embed(
                    title="✨ Gros Tirage !",
                    description=f"**{member.mention}** vient de tirer **{card['nom']}** ({card['rarete']}) !",
                    color=RARITY_COLORS.get(card['rarete'], discord.Color.gold()),
                )
                # La CARTE composée, pas le portrait brut : `image_url` pointe sur la
                # photo de référence (le détouré du joueur sur fond nu pour la S2), ce
                # qui affichait un portrait sans cadre ni bandeau dans le salon public.
                file, url = await self._card_image(card)
                em.set_image(url=url)
                if file:
                    await chan.send(embed=em, file=file)
                else:
                    await chan.send(embed=em)
            except Exception:
                pass

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
            file, url = await self._card_image(target_card)
            embed.set_image(url=url)
            if file:
                await msg.edit(embed=embed, attachments=[file])
            else:
                await msg.edit(embed=embed)
            
        except Exception as e:
            await msg.edit(content=f"❌ Une erreur est survenue : `{e}`", embed=None)

    # --- CLASSE D'AFFICHAGE (View) ---
    class CollectionView(discord.ui.View):
        # Ordre d'affichage des raretés dans le tableau de bord
        RARITY_ORDER_LIST = ["Commun", "Peu Commun", "Rare", "Épique", "Légendaire", "Noël"]

        def __init__(self, author_id, all_cards, owned_ids, saison=SAISON_COURANTE):
            super().__init__(timeout=180)
            self.author_id = author_id
            self.toutes_cartes = all_cards
            self.owned_ids = {str(x) for x in owned_ids}
            # Tant qu'une saison n'est pas publiée, son catalogue est vide : on ouvre
            # alors sur la plus récente qui existe. Sans ça le menu de clubs partirait
            # sans une seule option, et Discord refuse le message entier.
            if not cartes_de_la_saison(all_cards, saison):
                dispo = {saison_de(c) for c in all_cards}
                saison = max(dispo) if dispo else saison
            self._indexer(saison)

        def _indexer(self, saison):
            """(Re)construit les index sur UNE saison.

            Une collection ne se lit que saison par saison : mélanger les deux
            donnerait des comptes de club faux (165 joueurs ont une carte dans
            chacune) et un pourcentage de complétion qui ne veut rien dire. Rejoué
            tel quel au basculement vers l'archive."""
            self.saison = saison
            self.all_cards = cartes_de_la_saison(self.toutes_cartes, saison)
            self.current_club = None
            self._album_cache = {}  # bytes de l'image d'album, par club (évite de régénérer)

            # Cartes possédées (uniques)
            self.collection = [c for c in self.all_cards if str(c['id']) in self.owned_ids]

            # Index : toutes les cartes par club, cartes possédées par club, totaux par rareté
            self.full_by_club = {}
            self.cards_per_club_total = {}
            self.rarity_total = {}
            for card in self.all_cards:
                self.full_by_club.setdefault(card['club'], []).append(card)
                self.cards_per_club_total[card['club']] = self.cards_per_club_total.get(card['club'], 0) + 1
                self.rarity_total[card['rarete']] = self.rarity_total.get(card['rarete'], 0) + 1

            self.cards_by_club = {}
            self.rarity_owned = {}
            for card in self.collection:
                self.cards_by_club.setdefault(card['club'], []).append(card)
                self.rarity_owned[card['rarete']] = self.rarity_owned.get(card['rarete'], 0) + 1

            # Un menu sans option fait rejeter le message par Discord : on en garde
            # toujours une, inerte, plutôt que de casser /collection.
            options = self.create_select_options()
            self.club_select.options = options or [discord.SelectOption(label="—", value="_vide")]
            self.club_select.disabled = not (options and self.collection)
            if not options:
                self.club_select.placeholder = "Saison pas encore publiée."
            elif not self.collection:
                self.club_select.placeholder = ("Ta collection est vide !" if saison == SAISON_COURANTE
                                                else "Aucune carte archivée.")
            else:
                self.club_select.placeholder = "Choisis un club..."
            self.home_button.disabled = True
            # Rien à basculer tant que l'autre saison n'a pas été publiée.
            autre = 1 if saison == SAISON_COURANTE else SAISON_COURANTE
            self.saison_button.disabled = not cartes_de_la_saison(self.toutes_cartes, autre)
            self.saison_button.label = ("🗄️ Archive S1" if saison == SAISON_COURANTE
                                        else f"🔙 Saison {SAISON_COURANTE}")

        # --- Helpers d'affichage ---
        def _bar(self, pct, length=20):
            filled = int(round(pct / 100 * length))
            if pct > 0 and filled == 0:
                filled = 1
            if pct >= 100:
                filled = length
            return "▰" * filled + "▱" * (length - filled)

        def create_select_options(self):
            options = []
            for club, total_count in sorted(self.cards_per_club_total.items()):
                owned = len(self.cards_by_club.get(club, []))
                done = owned >= total_count
                label = f"🎄 {club}" if club == "Légendes Starligue" else club
                emoji = "🎁" if club == "Légendes Starligue" else None
                desc = f"{owned} / {total_count}" + (" ✅" if done else "")
                options.append(discord.SelectOption(label=label, description=desc, value=club, emoji=emoji))
            return options[:25]  # Limite Discord

        # --- VUE GLOBALE : tableau de bord ---
        def build_home_embed(self):
            unique = len(self.collection)
            total = len(self.all_cards)
            pct = (unique / total * 100) if total else 0

            archive = self.saison != SAISON_COURANTE
            embed = discord.Embed(
                title=(f"🗄️ Archive — Saison {self.saison}" if archive
                       else f"🗂️ Album de Collection — Saison {self.saison}"),
                description=("Ces cartes ne sont plus tirables ni jouables en duel : "
                             "elles restent là comme souvenir de la saison passée."
                             if archive else
                             "Choisis un club ci-dessous pour afficher son album complet en image."),
                color=discord.Color.dark_theme(),
            )
            embed.add_field(
                name="📈 Progression globale",
                value=f"```\n{self._bar(pct)} {pct:.1f}%\n```**{unique}** / **{total}** cartes possédées.",
                inline=False,
            )

            # Répartition par rareté
            rarity_lines = []
            for r in self.RARITY_ORDER_LIST:
                tot = self.rarity_total.get(r, 0)
                if not tot:
                    continue
                own = self.rarity_owned.get(r, 0)
                rarity_lines.append(f"{RARITY_EMOJI.get(r, '🔹')} **{r}** — {own}/{tot}")
            if rarity_lines:
                embed.add_field(name="🎚️ Par rareté", value="\n".join(rarity_lines), inline=True)

            # Clubs complétés / bientôt complétés
            completed, in_progress = [], []
            for club, tot in self.cards_per_club_total.items():
                own = len(self.cards_by_club.get(club, []))
                if tot > 0 and own >= tot:
                    completed.append(club)
                elif own > 0:
                    in_progress.append((club, own, tot, tot - own))
            in_progress.sort(key=lambda x: (x[3], -x[1]))  # moins de cartes restantes d'abord
            if in_progress:
                near = "\n".join(f"**{club}** — {own}/{tot} (reste {rem})"
                                 for club, own, tot, rem in in_progress[:3])
                embed.add_field(name="🔥 Bientôt complétés", value=near, inline=True)

            if completed:
                embed.set_footer(text=f"🏆 {len(completed)} club(s) complété(s) sur {len(self.cards_per_club_total)}")
            else:
                embed.set_footer(text="Aucun club complété pour l'instant — continue d'ouvrir des packs !")
            return embed

        # --- VUE CLUB : image d'album ---
        async def build_club_view(self):
            club = self.current_club
            cards = sorted(self.full_by_club.get(club, []),
                           key=lambda x: (RARITY_ORDER.get(x['rarete'], 99), x['nom']))
            owned = len(self.cards_by_club.get(club, []))
            total = len(cards)
            pct = (owned / total * 100) if total else 0

            if club not in self._album_cache:
                buf = await album_generator.generate_club_album(club, cards, self.owned_ids)
                self._album_cache[club] = buf.getvalue()
            file = discord.File(io.BytesIO(self._album_cache[club]), filename=f"album_{club}.png")

            is_xmas = club == "Légendes Starligue"
            embed = discord.Embed(
                title=f"{'🎄' if is_xmas else '📖'} {club}",
                description=f"{self._bar(pct)} **{pct:.0f}%**\n"
                            f"**{owned}/{total}** cartes — reste **{total - owned}** à trouver.",
                color=discord.Color.gold(),
            )
            embed.set_image(url=f"attachment://album_{club}.png")
            return embed, file

        # --- Interactions ---
        async def _deny(self, interaction):
            await interaction.response.send_message("Ce n'est pas ta collection 😉", ephemeral=True)

        @discord.ui.select(placeholder="Choisis un club...", row=0)
        async def club_select(self, interaction: discord.Interaction, select: discord.ui.Select):
            if interaction.user.id != self.author_id:
                return await self._deny(interaction)
            self.current_club = select.values[0]
            self.home_button.disabled = False
            await interaction.response.defer()  # la génération d'image peut prendre quelques secondes
            embed, file = await self.build_club_view()
            await interaction.edit_original_response(embed=embed, attachments=[file], view=self)

        @discord.ui.button(label="🗄️ Archive S1", style=discord.ButtonStyle.secondary, row=1)
        async def saison_button(self, interaction: discord.Interaction, button: discord.ui.Button):
            if interaction.user.id != self.author_id:
                return await self._deny(interaction)
            self._indexer(1 if self.saison == SAISON_COURANTE else SAISON_COURANTE)
            await interaction.response.edit_message(embed=self.build_home_embed(),
                                                    attachments=[], view=self)

        @discord.ui.button(label="↩️ Vue d'ensemble", style=discord.ButtonStyle.grey, row=1, disabled=True)
        async def home_button(self, interaction: discord.Interaction, button: discord.ui.Button):
            if interaction.user.id != self.author_id:
                return await self._deny(interaction)
            self.current_club = None
            button.disabled = True
            await interaction.response.edit_message(embed=self.build_home_embed(), attachments=[], view=self)

    @app_commands.command(name='collection', description="Affiche ta collection.")
    async def collection_command(self, interaction: discord.Interaction):
        raw_ids = database.get_user_collection(interaction.user.id)
        view = self.CollectionView(interaction.user.id, self.all_cards, raw_ids, self.saison)
        await interaction.response.send_message(embed=view.build_home_embed(), view=view, ephemeral=True)

    # --- Helpers de recyclage (partagés legacy / sélectif) ---
    def get_card_safe(self, cid):
        return self.card_map.get(cid) or self.card_map.get(str(cid))

    def _fragments_for_removed(self, removed_counts):
        """removed_counts: {card_id: nb_exemplaires_retirés} -> total fragments."""
        total = 0
        for cid, n in removed_counts.items():
            card = self.get_card_safe(cid)
            if card:
                total += FRAGMENT_VALUES.get(card['rarete'], 0) * n
        return total

    def recycle_all(self, user_id):
        """Recycle TOUS les doublons (garde un de chaque). Retourne les fragments gagnés."""
        ids = database.get_user_collection(user_id)
        counts = {}
        for cid in ids:
            counts[cid] = counts.get(cid, 0) + 1
        removed = {cid: cnt - 1 for cid, cnt in counts.items() if cnt > 1}
        fragments = self._fragments_for_removed(removed)
        if fragments > 0:
            database.update_fragments(user_id, fragments)
            database.reset_and_set_collection(user_id, list(counts.keys()))
        return fragments

    def recycle_selected(self, user_id, card_ids):
        """Recycle uniquement les doublons des cartes choisies. Retourne les fragments gagnés."""
        removed = database.remove_extra_copies(user_id, card_ids)
        fragments = self._fragments_for_removed(removed)
        if fragments > 0:
            database.update_fragments(user_id, fragments)
        return fragments

    @app_commands.command(name='recycler', description="Échange tes doublons contre des fragments.")
    async def recycle_command(self, interaction: discord.Interaction):
        from beta import beta_access

        # --- Public (avant la sortie) : ancien recyclage « tout d'un coup » ---
        if not beta_access(interaction):
            fragments = self.recycle_all(interaction.user.id)
            if fragments == 0:
                return await interaction.response.send_message("Aucun doublon.", ephemeral=True)
            embed = discord.Embed(title="♻️ Recyclage", description=f"Tu as gagné **{fragments} fragments**.", color=discord.Color.green())
            return await interaction.response.send_message(embed=embed, ephemeral=True)

        # --- Saison 2 : recyclage sélectif + garde-fou anti-échange ---
        from cogs.trade_cog import ACTIVE_TRADERS
        if interaction.user.id in ACTIVE_TRADERS:
            return await interaction.response.send_message(
                "♻️ Tu as un échange en cours — termine-le avant de recycler.", ephemeral=True)

        ids = database.get_user_collection(interaction.user.id)
        counts = {}
        for cid in ids:
            counts[cid] = counts.get(cid, 0) + 1
        dups = {cid: cnt for cid, cnt in counts.items() if cnt > 1}
        if not dups:
            return await interaction.response.send_message("Aucun doublon à recycler.", ephemeral=True)

        view = RecycleView(self, interaction.user.id, dups)
        await interaction.response.send_message(embed=view.build_embed(), view=view, ephemeral=True)

    @app_commands.command(name='creer', description="Fabriquer une carte.")
    async def create_card_command(self, interaction: discord.Interaction, nom_de_la_carte: str):
        # Sur la saison en cours seulement : 165 joueurs existent dans les deux
        # saisons, donc chercher sur tout le catalogue rendrait « Trop de résultats »
        # pour la majorité des noms, en plus de permettre de fabriquer une archive.
        matches = [c for c in self.cards_saison if nom_de_la_carte.lower() in c['nom'].lower()]
        
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
        # La carte composée, pas le portrait brut (cf. _card_image)
        file, url = await self._card_image(target)
        e.set_image(url=url)
        if file:
            await interaction.response.send_message(embed=e, file=file, ephemeral=True)
        else:
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
        valid_ids = [c['id'] for c in self.cards_saison]
        data = database.get_leaderboard_data(valid_ids, limit=20)
        clean_data = [d for d in data if d[0] not in LEADERBOARD_EXCLUDED_IDS][:20]

        total_cards = len(self.cards_saison)
        
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

    @app_commands.command(name='addpointsall',
                          description="[Admin] Donner des points à TOUS les joueurs enregistrés.")
    @app_commands.describe(montant="Points ajoutés à chaque joueur (positif uniquement)")
    @app_commands.default_permissions(manage_guild=True)
    async def add_points_all_command(self, interaction: discord.Interaction, montant: int):
        # Additif uniquement : un retrait de masse n'a aucun garde-fou (soldes
        # négatifs, points déjà dépensés) et n'a pas de cas d'usage ici.
        if montant <= 0:
            return await interaction.response.send_message(
                "❌ Le montant doit être positif : cette commande sert uniquement à ajouter.",
                ephemeral=True)

        # Contrairement à /addpoints, aucun MP n'est envoyé et rien n'est posté dans
        # le salon : à l'échelle de tous les joueurs ça ferait autant de MP que de
        # comptes. Seul l'admin voit le récap.
        count = database.mass_add_points(montant)
        await interaction.response.send_message(
            f"✅ **{montant} points** ajoutés à **{count}** joueur(s) enregistré(s).\n"
            f"ℹ️ *Aucune notification envoyée.*", ephemeral=True)

    @app_commands.command(name='donnercarte', description="[Admin] Donner une carte précise à un joueur.")
    @app_commands.describe(membre="Le joueur qui reçoit la carte", carte="Nom de la carte à donner")
    @app_commands.default_permissions(manage_guild=True)
    async def give_card_command(self, interaction: discord.Interaction, membre: discord.Member, carte: str):
        # L'autocomplétion renvoie l'ID de la carte ; on accepte aussi un nom saisi à la main
        target = self.card_map.get(carte) or self.card_map.get(str(carte))
        if not target:
            matches = [c for c in self.all_cards if carte.lower() in c['nom'].lower()]
            if not matches:
                return await interaction.response.send_message(f"❌ Carte introuvable : `{carte}`.", ephemeral=True)
            if len(matches) > 1:
                return await interaction.response.send_message(
                    "❌ Plusieurs cartes correspondent, précise le nom (ou utilise l'autocomplétion).", ephemeral=True)
            target = matches[0]

        database.add_card_to_collection(membre.id, target['id'])

        e = discord.Embed(
            title="🎁 Carte donnée !",
            description=f"**{target['nom']}** a été ajoutée à la collection de {membre.mention}.",
            color=RARITY_COLORS.get(target['rarete'], discord.Color.default()),
        )
        file, url = await self._card_image(target)
        e.set_image(url=url)
        if file:
            await interaction.response.send_message(embed=e, file=file, ephemeral=True)
        else:
            await interaction.response.send_message(embed=e, ephemeral=True)
        try:
            await membre.send(f"🎁 Un admin t'a offert la carte **{target['nom']}** ({target['rarete']}) !")
        except discord.errors.Forbidden:
            pass

    @give_card_command.autocomplete('carte')
    async def give_card_autocomplete(self, interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        cur = current.lower()
        results = []
        for c in self.all_cards:
            if cur in c['nom'].lower():
                results.append(app_commands.Choice(name=f"{c['nom']} · {c['rarete']}"[:100], value=str(c['id'])))
            if len(results) >= 25:
                break
        return results

async def setup(bot):
    await bot.add_cog(CollectionCog(bot))
