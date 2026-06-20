"""
Échange de cartes entre joueurs (Saison 2).

- `/echange @membre` ouvre une fenêtre d'échange partagée.
- Chaque joueur compose SON offre via un sélecteur privé (éphémère) :
  club → carte → (retirer). Jusqu'à 6 cartes par côté.
- Pas de cadeau : les deux offres doivent contenir au moins une carte.
- Double validation : l'échange ne s'exécute que si LES DEUX confirment.
- Anti-arnaque : toute modification d'une offre annule les confirmations.
- L'échange est ATOMIQUE (database.execute_trade) : aucune carte dupliquée/perdue.
"""
from collections import Counter

import discord
from discord import app_commands
from discord.ext import commands

import database
from beta import beta_guard, BetaLocked
from cogs.collection_cog import load_cards_data

RARITY_EMOJI = {
    "Commun": "⬜", "Peu Commun": "🟩", "Rare": "🟦",
    "Épique": "🟪", "Légendaire": "🟨", "Noël": "🎄",
}
MAX_PER_SIDE = 6

# Verrou : un joueur ne peut être dans qu'un seul échange à la fois.
ACTIVE_TRADERS = set()


class TradeSession:
    """État partagé d'un échange. basket = liste de (rowid, card_id)."""

    def __init__(self, user_a, user_b):
        self.user_a = user_a
        self.user_b = user_b
        self.basket_a = []
        self.basket_b = []
        self.confirmed_a = False
        self.confirmed_b = False

    def basket(self, side):
        return self.basket_a if side == "a" else self.basket_b

    def reset_confirms(self):
        self.confirmed_a = False
        self.confirmed_b = False

    def confirm(self, side):
        if side == "a":
            self.confirmed_a = True
        else:
            self.confirmed_b = True


class TradePicker(discord.ui.View):
    """Sélecteur privé (éphémère) pour composer son offre."""

    def __init__(self, cog, session, main_view, side, user_id):
        super().__init__(timeout=180)
        self.cog = cog
        self.s = session
        self.main_view = main_view
        self.side = side
        self.user_id = user_id
        self.current_club = None
        self._refresh_components()

    async def interaction_check(self, interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Ce n'est pas ton offre.", ephemeral=True)
            return False
        return True

    def basket(self):
        return self.s.basket(self.side)

    def _grouped_owned(self):
        """{club: [(rowid, card_id), ...]} des cartes possédées par le joueur."""
        clubs = {}
        for rowid, cid in database.get_user_cards_with_rowid(self.user_id):
            card = self.cog.get_card(cid)
            if not card:
                continue
            clubs.setdefault(card["club"], []).append((rowid, cid))
        return clubs

    def _refresh_components(self):
        clubs = self._grouped_owned()
        basket_rowids = {r for r, _ in self.basket()}

        # 1) Sélecteur de club
        club_opts = []
        for club in sorted(clubs.keys()):
            items = clubs[club]
            avail = sum(1 for r, _ in items if r not in basket_rowids)
            club_opts.append(discord.SelectOption(
                label=club[:100], value=club[:100],
                description=f"{avail} dispo / {len(items)}",
                default=(club == self.current_club),
            ))
        self.club_select.options = club_opts[:25] or [
            discord.SelectOption(label="(aucune carte)", value="__none__")]
        self.club_select.disabled = not clubs

        # 2) Sélecteur de carte (dans le club choisi)
        card_opts = []
        if self.current_club and self.current_club in clubs:
            avail_count = Counter()
            for rowid, cid in clubs[self.current_club]:
                if rowid not in basket_rowids:
                    avail_count[str(cid)] += 1
            seen = set()
            for rowid, cid in clubs[self.current_club]:
                if str(cid) in seen:
                    continue
                seen.add(str(cid))
                card = self.cog.get_card(cid)
                n = avail_count[str(cid)]
                desc = f"{card['rarete']} — {n} dispo" if n else f"{card['rarete']} — déjà dans l'offre"
                card_opts.append(discord.SelectOption(
                    label=card["nom"][:100], value=str(cid),
                    description=desc[:100],
                    emoji=RARITY_EMOJI.get(card["rarete"], "🔹"),
                ))
        self.card_select.options = card_opts[:25] or [
            discord.SelectOption(label="(choisis d'abord un club)", value="__none__")]
        self.card_select.disabled = not card_opts

        # 3) Sélecteur de retrait (offre actuelle)
        rem_opts = []
        for rowid, cid in self.basket():
            card = self.cog.get_card(cid)
            rem_opts.append(discord.SelectOption(
                label=(card["nom"] if card else str(cid))[:100],
                value=str(rowid), description="Retirer de l'offre",
            ))
        self.remove_select.options = rem_opts[:25] or [
            discord.SelectOption(label="(offre vide)", value="__none__")]
        self.remove_select.disabled = not self.basket()

    def _embed(self):
        if self.basket():
            lines = []
            for rowid, cid in self.basket():
                card = self.cog.get_card(cid)
                emoji = RARITY_EMOJI.get(card["rarete"], "🔹") if card else "🔹"
                lines.append(f"{emoji} {card['nom'] if card else cid}")
            desc = "\n".join(lines)
        else:
            desc = "_Aucune carte sélectionnée._"
        e = discord.Embed(title="🎒 Compose ton offre", description=desc, color=discord.Color.gold())
        e.set_footer(text=f"{len(self.basket())}/{MAX_PER_SIDE} cartes · 1) club  2) ajouter  3) retirer")
        return e

    async def _apply(self, interaction):
        self._refresh_components()
        await interaction.response.edit_message(embed=self._embed(), view=self)
        await self.main_view.refresh()

    @discord.ui.select(placeholder="1️⃣ Choisis un club…", row=0)
    async def club_select(self, interaction, select):
        if select.values[0] == "__none__":
            return await interaction.response.defer()
        self.current_club = select.values[0]
        self._refresh_components()
        await interaction.response.edit_message(embed=self._embed(), view=self)

    @discord.ui.select(placeholder="2️⃣ Ajoute une carte à ton offre…", row=1)
    async def card_select(self, interaction, select):
        val = select.values[0]
        if val == "__none__":
            return await interaction.response.defer()
        if len(self.basket()) >= MAX_PER_SIDE:
            return await interaction.response.send_message(
                f"Maximum {MAX_PER_SIDE} cartes par côté.", ephemeral=True)
        basket_rowids = {r for r, _ in self.basket()}
        chosen = next(((r, c) for r, c in database.get_user_cards_with_rowid(self.user_id)
                       if str(c) == val and r not in basket_rowids), None)
        if not chosen:
            return await interaction.response.send_message(
                "Plus d'exemplaire disponible de cette carte.", ephemeral=True)
        self.basket().append(chosen)
        self.s.reset_confirms()
        await self._apply(interaction)

    @discord.ui.select(placeholder="🗑️ Retirer une carte de ton offre…", row=2)
    async def remove_select(self, interaction, select):
        val = select.values[0]
        if val == "__none__":
            return await interaction.response.defer()
        rid = int(val)
        b = self.basket()
        b[:] = [(r, c) for (r, c) in b if r != rid]
        self.s.reset_confirms()
        await self._apply(interaction)


class TradeView(discord.ui.View):
    """Fenêtre d'échange partagée par les deux joueurs."""

    def __init__(self, cog, session):
        super().__init__(timeout=300)
        self.cog = cog
        self.s = session
        self.message = None
        self._sync_buttons()

    async def interaction_check(self, interaction):
        if interaction.user.id not in (self.s.user_a, self.s.user_b):
            await interaction.response.send_message("Ce n'est pas ton échange.", ephemeral=True)
            return False
        return True

    def _side_of(self, user_id):
        return "a" if user_id == self.s.user_a else "b"

    def _fmt(self, basket):
        if not basket:
            return "_(vide)_"
        out = []
        for rowid, cid in basket:
            card = self.cog.get_card(cid)
            emoji = RARITY_EMOJI.get(card["rarete"], "🔹") if card else "🔹"
            out.append(f"{emoji} {card['nom'] if card else cid}")
        return "\n".join(out)

    def build_embed(self):
        s = self.s
        e = discord.Embed(title="🤝 Échange de cartes", color=discord.Color.blurple())
        a_check = "✅" if s.confirmed_a else "⬜"
        b_check = "✅" if s.confirmed_b else "⬜"
        e.add_field(
            name=f"{a_check} <@{s.user_a}> ({len(s.basket_a)}/{MAX_PER_SIDE})",
            value=self._fmt(s.basket_a), inline=True)
        e.add_field(
            name=f"{b_check} <@{s.user_b}> ({len(s.basket_b)}/{MAX_PER_SIDE})",
            value=self._fmt(s.basket_b), inline=True)
        e.set_footer(text="➕ Mes cartes : composer · ✅ Confirmer (les deux) · ❌ Annuler")
        return e

    def _sync_buttons(self):
        ready = bool(self.s.basket_a) and bool(self.s.basket_b)
        self.confirm_btn.disabled = not ready

    async def refresh(self):
        self._sync_buttons()
        if self.message:
            try:
                await self.message.edit(embed=self.build_embed(), view=self)
            except discord.HTTPException:
                pass

    def _cleanup(self):
        ACTIVE_TRADERS.discard(self.s.user_a)
        ACTIVE_TRADERS.discard(self.s.user_b)

    @discord.ui.button(label="Mes cartes", emoji="➕", style=discord.ButtonStyle.blurple, row=0)
    async def edit_btn(self, interaction, button):
        side = self._side_of(interaction.user.id)
        picker = TradePicker(self.cog, self.s, self, side, interaction.user.id)
        await interaction.response.send_message(embed=picker._embed(), view=picker, ephemeral=True)

    @discord.ui.button(label="Confirmer", emoji="✅", style=discord.ButtonStyle.green, row=0)
    async def confirm_btn(self, interaction, button):
        s = self.s
        if not s.basket_a or not s.basket_b:
            return await interaction.response.send_message(
                "Chaque joueur doit proposer au moins une carte (pas de cadeau).", ephemeral=True)
        side = self._side_of(interaction.user.id)
        s.confirm(side)
        if s.confirmed_a and s.confirmed_b:
            await self._finalize(interaction)
        else:
            await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(label="Annuler", emoji="❌", style=discord.ButtonStyle.red, row=0)
    async def cancel_btn(self, interaction, button):
        self._cleanup()
        for c in self.children:
            c.disabled = True
        e = self.build_embed()
        e.title = "❌ Échange annulé"
        e.color = discord.Color.red()
        await interaction.response.edit_message(embed=e, view=self)
        self.stop()

    async def _finalize(self, interaction):
        s = self.s
        rowids_a = [r for r, _ in s.basket_a]
        rowids_b = [r for r, _ in s.basket_b]
        ok = database.execute_trade(s.user_a, rowids_a, s.user_b, rowids_b)
        self._cleanup()
        for c in self.children:
            c.disabled = True
        e = self.build_embed()
        if ok:
            database.log_trade(s.user_a, [c for _, c in s.basket_a],
                               s.user_b, [c for _, c in s.basket_b])
            e.title = "✅ Échange effectué !"
            e.color = discord.Color.green()
        else:
            e.title = "❌ Échange échoué (une carte n'était plus disponible)"
            e.color = discord.Color.red()
        await interaction.response.edit_message(embed=e, view=self)
        self.stop()

    async def on_timeout(self):
        self._cleanup()
        if self.message:
            for c in self.children:
                c.disabled = True
            e = self.build_embed()
            e.title = "⌛ Échange expiré"
            e.color = discord.Color.greyple()
            try:
                await self.message.edit(embed=e, view=self)
            except discord.HTTPException:
                pass


class TradeCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.all_cards = load_cards_data()
        self.card_map = {}
        for c in self.all_cards:
            self.card_map[c["id"]] = c
            self.card_map[str(c["id"])] = c

    def get_card(self, cid):
        return self.card_map.get(cid) or self.card_map.get(str(cid))

    async def cog_app_command_error(self, interaction, error):
        msg = error.user_message if isinstance(error, BetaLocked) else None
        if msg is None and isinstance(error, app_commands.CheckFailure):
            msg = "🔒 Action non autorisée."
        if msg is None:
            raise error
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)

    @app_commands.command(name="echange", description="Proposer un échange de cartes à un autre joueur.")
    @app_commands.describe(membre="Le joueur avec qui tu veux échanger")
    @beta_guard()
    async def echange(self, interaction: discord.Interaction, membre: discord.Member):
        a, b = interaction.user.id, membre.id
        if membre.bot:
            return await interaction.response.send_message("Tu ne peux pas échanger avec un bot.", ephemeral=True)
        if a == b:
            return await interaction.response.send_message("Tu ne peux pas échanger avec toi-même.", ephemeral=True)
        if a in ACTIVE_TRADERS or b in ACTIVE_TRADERS:
            return await interaction.response.send_message(
                "Un des deux joueurs a déjà un échange en cours.", ephemeral=True)

        ACTIVE_TRADERS.add(a)
        ACTIVE_TRADERS.add(b)
        view = TradeView(self, TradeSession(a, b))
        await interaction.response.send_message(
            content=f"{interaction.user.mention} propose un échange à {membre.mention} !",
            embed=view.build_embed(), view=view)
        view.message = await interaction.original_response()


async def setup(bot):
    await bot.add_cog(TradeCog(bot))
