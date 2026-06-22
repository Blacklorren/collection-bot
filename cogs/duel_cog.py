"""
Duels entre joueurs (Saison 2).

ÉTAT (handoff) :
  ✅ Moteur d'équilibrage  -> duel_engine.py (testé Monte-Carlo)
  ✅ Fonctions DB          -> database.py (elo, record_duel, anti-farm, leaderboard)
  ✅ Flux de défi complet  -> /defi @membre [amical] : accepter -> match -> Elo -> récompenses
  ✅ Composition AUTO      -> auto_lineup() : meilleure carte par poste (baseline jouable)
  ✅ Composition MANUELLE  -> DuelLineupView / LineupPicker : après acceptation, chaque
     joueur compose son équipe slot par slot dans un menu privé (éphémère), avec un
     bouton « Compo automatique » qui réutilise auto_lineup(). Le match se lance quand
     les deux ont cliqué « Prêt ». (Pattern repris de cogs/trade_cog.py.)
  ✅ Classement            -> /classement_duel

Gating : beta_guard (visible-mais-bloqué jusqu'au 1er août, cf beta.py).
"""
import os
from datetime import datetime

import discord
import pytz
from discord import app_commands
from discord.ext import commands

import database
import duel_engine as E
from beta import beta_guard, BetaLocked
from cogs.collection_cog import load_cards_data, RARITY_EMOJI

PARIS = pytz.timezone("Europe/Paris")

# --- Réglages anti-farm (surchargeables via .env) ---
DUEL_ELO_BAND = int(os.getenv("DUEL_ELO_BAND", str(E.ELO_BAND)))   # écart Elo max (classé)
DAILY_PAIR_CAP = int(os.getenv("DUEL_DAILY_PAIR_CAP", "3"))        # duels classés/jour entre 2 mêmes joueurs
DAILY_REWARD_CAP = int(os.getenv("DUEL_DAILY_REWARD_CAP", "10"))   # duels récompensés/jour/joueur

# Verrou : un joueur ne peut être que dans un duel à la fois.
ACTIVE_DUELISTS = set()


def _today_start_iso():
    """Minuit (Europe/Paris) du jour courant, au format comparable par SQLite (UTC)."""
    now = datetime.now(PARIS)
    midnight = PARIS.localize(datetime(now.year, now.month, now.day))
    return midnight.astimezone(pytz.utc).strftime("%Y-%m-%d %H:%M:%S")


class DuelChallengeView(discord.ui.View):
    """Message de défi : seul l'adversaire peut accepter ou refuser."""

    def __init__(self, cog, challenger, opponent, ranked):
        super().__init__(timeout=120)
        self.cog = cog
        self.challenger = challenger      # discord.Member
        self.opponent = opponent          # discord.Member
        self.ranked = ranked
        self.message = None
        self.resolved = False

    async def interaction_check(self, interaction):
        if interaction.user.id != self.opponent.id:
            await interaction.response.send_message("Seul l'adversaire défié peut répondre.", ephemeral=True)
            return False
        return True

    def _cleanup(self):
        ACTIVE_DUELISTS.discard(self.challenger.id)
        ACTIVE_DUELISTS.discard(self.opponent.id)

    @discord.ui.button(label="Accepter", emoji="⚔️", style=discord.ButtonStyle.green)
    async def accept(self, interaction, button):
        if self.resolved:
            return
        self.resolved = True
        for c in self.children:
            c.disabled = True
        # On passe à la phase de COMPOSITION : chaque joueur aligne son équipe,
        # puis le match se lance. ACTIVE_DUELISTS reste verrouillé jusqu'à la fin.
        session = DuelSession(self.challenger, self.opponent, self.ranked)
        lineup_view = DuelLineupView(self.cog, session)
        await interaction.response.edit_message(embed=lineup_view.build_embed(), view=lineup_view)
        lineup_view.message = await interaction.original_response()
        self.stop()

    @discord.ui.button(label="Refuser", emoji="✖️", style=discord.ButtonStyle.red)
    async def decline(self, interaction, button):
        self.resolved = True
        self._cleanup()
        for c in self.children:
            c.disabled = True
        e = discord.Embed(title="Défi refusé", description=f"{self.opponent.mention} a décliné le duel.", color=discord.Color.red())
        await interaction.response.edit_message(embed=e, view=self)
        self.stop()

    async def on_timeout(self):
        if not self.resolved:
            self._cleanup()


class DuelSession:
    """État partagé d'un duel pendant la phase de composition.
    lineup_x : dict {slot: card_dict | None}. ready_x : bool."""

    def __init__(self, challenger, opponent, ranked):
        self.challenger = challenger      # discord.Member
        self.opponent = opponent          # discord.Member
        self.ranked = ranked
        self.lineup_c = {s: None for s in E.SLOTS}
        self.lineup_o = {s: None for s in E.SLOTS}
        self.ready_c = False
        self.ready_o = False

    def side_of(self, user_id):
        return "c" if user_id == self.challenger.id else "o"

    def lineup(self, side):
        return self.lineup_c if side == "c" else self.lineup_o

    def is_ready(self, side):
        return self.ready_c if side == "c" else self.ready_o

    def set_ready(self, side, val):
        if side == "c":
            self.ready_c = val
        else:
            self.ready_o = val


class LineupPicker(discord.ui.View):
    """Sélecteur privé (éphémère) : composer son équipe poste par poste.
    Choix d'un slot (1) → parcours d'un club (2) → placement d'une carte (3).
    Une même carte ne peut occuper qu'un seul slot (déplacée automatiquement)."""

    def __init__(self, cog, session, main_view, side, user_id):
        super().__init__(timeout=180)
        self.cog = cog
        self.s = session
        self.main_view = main_view
        self.side = side
        self.user_id = user_id
        self.current_slot = E.SLOTS[0]
        self.current_club = None
        self._refresh_components()

    async def interaction_check(self, interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Ce n'est pas ton équipe.", ephemeral=True)
            return False
        return True

    def lineup(self):
        return self.s.lineup(self.side)

    def _grouped_owned(self):
        """{club: [card_dict, ...]} des cartes jouables possédées (dédupliquées par carte)."""
        clubs, seen = {}, set()
        for cid in database.get_user_collection(self.user_id):
            if cid in seen:
                continue
            seen.add(cid)
            card = self.cog.get_card(cid)
            if card and card.get("rarete") != "Noël":   # Noël = promo, non jouable
                clubs.setdefault(card["club"], []).append(card)
        return clubs

    def _placed_slots(self):
        """{card_id: slot} des cartes déjà alignées."""
        return {c["id"]: slot for slot, c in self.lineup().items() if c}

    def _refresh_components(self):
        clubs = self._grouped_owned()
        placed = self._placed_slots()

        # 1) Sélecteur de poste (slot)
        slot_opts = []
        for slot in E.SLOTS:
            card = self.lineup().get(slot)
            slot_opts.append(discord.SelectOption(
                label=f"{slot} · {E.SLOT_LABELS[slot]}"[:100], value=slot,
                description=(card["nom"][:100] if card else "(vide)"),
                default=(slot == self.current_slot)))
        self.slot_select.options = slot_opts

        # 2) Sélecteur de club
        club_opts = []
        for club in sorted(clubs.keys()):
            club_opts.append(discord.SelectOption(
                label=club[:100], value=club[:100],
                description=f"{len(clubs[club])} carte(s)",
                default=(club == self.current_club)))
        self.club_select.options = club_opts[:25] or [
            discord.SelectOption(label="(aucune carte jouable)", value="__none__")]
        self.club_select.disabled = not clubs

        # 3) Sélecteur de carte (dans le club choisi) pour le slot courant
        card_opts = []
        if self.current_club and self.current_club in clubs:
            for card in clubs[self.current_club][:25]:
                where = placed.get(card["id"])
                if where and where != self.current_slot:
                    desc = f"{card['rarete']} — déjà aligné en {where}"
                else:
                    fit = "à son poste ✓" if E.normalize_poste(card.get("poste")) == self.current_slot else "hors poste ✗"
                    desc = f"{card['rarete']} — {fit}"
                card_opts.append(discord.SelectOption(
                    label=card["nom"][:100], value=str(card["id"]),
                    description=desc[:100],
                    emoji=RARITY_EMOJI.get(card["rarete"], "🔹")))
        self.card_select.options = card_opts or [
            discord.SelectOption(label="(choisis d'abord un club)", value="__none__")]
        self.card_select.disabled = not card_opts
        self.card_select.placeholder = f"3️⃣ Place une carte sur {self.current_slot}…"

    def _embed(self):
        lu = self.lineup()
        lines = []
        for slot in E.SLOTS:
            card = lu.get(slot)
            marker = "▸ " if slot == self.current_slot else "  "
            if card:
                emoji = RARITY_EMOJI.get(card["rarete"], "🔹")
                fit = "✓" if E.normalize_poste(card.get("poste")) == slot else "✗"
                lines.append(f"{marker}`{slot}` {emoji} {card['nom']} {fit}")
            else:
                lines.append(f"{marker}`{slot}` — *(vide)*")
        pow_, _ = E.team_power(lu)
        filled = sum(1 for c in lu.values() if c)
        e = discord.Embed(title="🛠️ Compose ton équipe", description="\n".join(lines), color=discord.Color.gold())
        e.set_footer(text=f"{filled}/7 postes · puissance estimée {round(pow_)} · ✓ = à son poste (×{E.POSTE_BONUS})")
        return e

    async def _apply(self, interaction):
        self.s.set_ready(self.side, False)   # toute modif annule le « Prêt »
        self._refresh_components()
        await interaction.response.edit_message(embed=self._embed(), view=self)
        await self.main_view.refresh()

    @discord.ui.select(placeholder="1️⃣ Choisis un poste à remplir…", row=0)
    async def slot_select(self, interaction, select):
        self.current_slot = select.values[0]
        self._refresh_components()
        await interaction.response.edit_message(embed=self._embed(), view=self)

    @discord.ui.select(placeholder="2️⃣ Parcours un club…", row=1)
    async def club_select(self, interaction, select):
        if select.values[0] == "__none__":
            return await interaction.response.defer()
        self.current_club = select.values[0]
        self._refresh_components()
        await interaction.response.edit_message(embed=self._embed(), view=self)

    @discord.ui.select(placeholder="3️⃣ Place une carte…", row=2)
    async def card_select(self, interaction, select):
        val = select.values[0]
        if val == "__none__":
            return await interaction.response.defer()
        card = self.cog.get_card(val)
        if not card:
            return await interaction.response.send_message("Carte introuvable.", ephemeral=True)
        lu = self.lineup()
        # une carte = un seul slot : on la retire de son slot précédent éventuel
        for slot, c in lu.items():
            if c and c["id"] == card["id"]:
                lu[slot] = None
        lu[self.current_slot] = card
        await self._apply(interaction)

    @discord.ui.button(label="Vider le poste", emoji="🗑️", style=discord.ButtonStyle.grey, row=3)
    async def clear_btn(self, interaction, button):
        self.lineup()[self.current_slot] = None
        await self._apply(interaction)

    @discord.ui.button(label="Compo automatique", emoji="🎲", style=discord.ButtonStyle.blurple, row=3)
    async def auto_btn(self, interaction, button):
        auto = self.cog.auto_lineup(self.user_id)
        lu = self.lineup()
        lu.clear()
        lu.update(auto)
        await self._apply(interaction)

    @discord.ui.button(label="Prêt", emoji="✅", style=discord.ButtonStyle.green, row=3)
    async def ready_btn(self, interaction, button):
        if not any(self.lineup().values()):
            return await interaction.response.send_message(
                "Aligne au moins une carte (ou clique « Compo automatique »).", ephemeral=True)
        self.s.set_ready(self.side, True)
        for c in self.children:
            c.disabled = True
        e = self._embed()
        e.title = "✅ Équipe validée — en attente de l'adversaire…"
        e.color = discord.Color.green()
        await interaction.response.edit_message(embed=e, view=self)
        self.stop()
        await self.main_view.after_ready()


class DuelLineupView(discord.ui.View):
    """Message partagé de la phase de composition (statut des deux joueurs)."""

    def __init__(self, cog, session):
        super().__init__(timeout=300)
        self.cog = cog
        self.s = session
        self.message = None
        self.launched = False

    async def interaction_check(self, interaction):
        if interaction.user.id not in (self.s.challenger.id, self.s.opponent.id):
            await interaction.response.send_message("Ce n'est pas ton duel.", ephemeral=True)
            return False
        return True

    def _status(self, side):
        lu = self.s.lineup(side)
        if self.s.is_ready(side):
            return "✅ **Prêt**"
        filled = sum(1 for c in lu.values() if c)
        return f"⏳ {filled}/7 postes"

    def build_embed(self):
        s = self.s
        e = discord.Embed(
            title="⚔️ Composez vos équipes",
            description="Cliquez **Composer mon équipe** pour aligner vos 7 cartes dans un menu privé, "
                        "puis **Prêt**.\nLe match se lance dès que les deux joueurs sont prêts.",
            color=discord.Color.blurple())
        e.add_field(name=s.challenger.display_name, value=self._status("c"), inline=True)
        e.add_field(name=s.opponent.display_name, value=self._status("o"), inline=True)
        e.set_footer(text=f"Mode : {'🏆 Classé' if s.ranked else '🤝 Amical'}")
        return e

    def _cleanup(self):
        ACTIVE_DUELISTS.discard(self.s.challenger.id)
        ACTIVE_DUELISTS.discard(self.s.opponent.id)

    async def refresh(self):
        if self.message:
            try:
                await self.message.edit(embed=self.build_embed(), view=self)
            except discord.HTTPException:
                pass

    @discord.ui.button(label="Composer mon équipe", emoji="🛠️", style=discord.ButtonStyle.blurple, row=0)
    async def compose_btn(self, interaction, button):
        side = self.s.side_of(interaction.user.id)
        picker = LineupPicker(self.cog, self.s, self, side, interaction.user.id)
        await interaction.response.send_message(embed=picker._embed(), view=picker, ephemeral=True)

    @discord.ui.button(label="Annuler", emoji="❌", style=discord.ButtonStyle.red, row=0)
    async def cancel_btn(self, interaction, button):
        self._cleanup()
        for c in self.children:
            c.disabled = True
        e = self.build_embed()
        e.title = "❌ Duel annulé"
        e.color = discord.Color.red()
        await interaction.response.edit_message(embed=e, view=self)
        self.stop()

    async def after_ready(self):
        """Appelé après qu'un joueur a cliqué « Prêt ». Lance le match si les deux le sont."""
        await self.refresh()
        if self.s.ready_c and self.s.ready_o and not self.launched:
            self.launched = True
            for c in self.children:
                c.disabled = True
            await self.cog.play_match(self)

    async def on_timeout(self):
        if self.launched:
            return
        self._cleanup()
        if self.message:
            for c in self.children:
                c.disabled = True
            e = self.build_embed()
            e.title = "⌛ Duel expiré (composition trop longue)"
            e.color = discord.Color.greyple()
            try:
                await self.message.edit(embed=e, view=self)
            except discord.HTTPException:
                pass


class DuelCog(commands.Cog):
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

    # --- Composition automatique (baseline ; remplaçable par un picker manuel) ---
    def auto_lineup(self, user_id):
        """Aligne la meilleure carte possédée sur chaque poste (glouton par note de poste).
        Retourne {slot: card_dict | None}."""
        seen, cards = set(), []
        for cid in database.get_user_collection(user_id):
            if cid in seen:
                continue
            seen.add(cid)
            card = self.get_card(cid)
            if card and card.get("rarete") != "Noël":   # Noël = promo, non jouable
                cards.append(card)

        lineup = {s: None for s in E.SLOTS}
        used = set()
        for slot in E.SLOTS:
            best, best_note, best_key = None, -1.0, None
            for card in cards:
                key = id(card)
                if key in used:
                    continue
                note = E.card_note(card, slot)
                if note > best_note:
                    best, best_note, best_key = card, note, key
            if best is not None:
                lineup[slot] = best
                used.add(best_key)
        return lineup

    def _lineup_card_ids(self, lineup):
        return {slot: (card["id"] if card else None) for slot, card in lineup.items()}

    def _mvp(self, lineup):
        best, best_note = None, -1.0
        for slot, card in lineup.items():
            if not card:
                continue
            note = E.card_note(card, slot)
            if note > best_note:
                best, best_note = card, note
        return best

    @app_commands.command(name="defi", description="Défier un autre joueur en duel de cartes.")
    @app_commands.describe(membre="Le joueur à défier", amical="Match amical (sans Elo ni récompense)")
    @beta_guard()
    async def defi(self, interaction: discord.Interaction, membre: discord.Member, amical: bool = False):
        challenger, opponent = interaction.user, membre
        if opponent.bot:
            return await interaction.response.send_message("Tu ne peux pas défier un bot.", ephemeral=True)
        if opponent.id == challenger.id:
            return await interaction.response.send_message("Tu ne peux pas te défier toi-même.", ephemeral=True)
        if challenger.id in ACTIVE_DUELISTS or opponent.id in ACTIVE_DUELISTS:
            return await interaction.response.send_message("Un des deux joueurs a déjà un duel en cours.", ephemeral=True)

        # Faut-il au moins quelques cartes jouables ?
        if not any(self.get_card(c) and self.get_card(c).get("rarete") != "Noël"
                   for c in database.get_user_collection(challenger.id)):
            return await interaction.response.send_message("Tu n'as pas encore de cartes jouables.", ephemeral=True)

        ranked = not amical
        if ranked:
            elo_c, elo_o = database.get_user_elo(challenger.id), database.get_user_elo(opponent.id)
            if not E.within_band(elo_c, elo_o, DUEL_ELO_BAND):
                return await interaction.response.send_message(
                    f"⚖️ Écart d'Elo trop grand pour un match **classé** "
                    f"(toi {elo_c} · lui {elo_o}, max ±{DUEL_ELO_BAND}).\n"
                    f"Lance un **match amical** : `/defi @{opponent.display_name} amical:True`.",
                    ephemeral=True)
            since = _today_start_iso()
            if database.count_ranked_duels_between(challenger.id, opponent.id, since) >= DAILY_PAIR_CAP:
                return await interaction.response.send_message(
                    f"🚫 Vous avez déjà fait {DAILY_PAIR_CAP} duels classés aujourd'hui. "
                    f"Joue en **amical** pour continuer.", ephemeral=True)

        ACTIVE_DUELISTS.add(challenger.id)
        ACTIVE_DUELISTS.add(opponent.id)
        view = DuelChallengeView(self, challenger, opponent, ranked)
        mode = "🏆 Classé" if ranked else "🤝 Amical"
        e = discord.Embed(
            title="⚔️ Défi lancé !",
            description=f"{challenger.mention} défie {opponent.mention} !\n**Mode : {mode}**\n\n"
                        f"{opponent.mention}, acceptes-tu le duel ?",
            color=discord.Color.blurple())
        await interaction.response.send_message(content=opponent.mention, embed=e, view=view)
        view.message = await interaction.original_response()

    async def play_match(self, view: "DuelLineupView"):
        """Simule avec les compositions choisies, applique Elo + récompenses, enregistre, affiche."""
        s = view.s
        c, o = s.challenger, s.opponent
        lu_c = s.lineup_c
        lu_o = s.lineup_o
        pow_c, det_c = E.team_power(lu_c)
        pow_o, det_o = E.team_power(lu_o)

        s_c, s_o = E.resolve_duel(pow_c, pow_o, allow_draw=False)
        winner = c.id if s_c > s_o else o.id if s_o > s_c else None

        elo_c0, elo_o0 = database.get_user_elo(c.id), database.get_user_elo(o.id)
        elo_c1, elo_o1 = elo_c0, elo_o0
        reward_line = ""

        if s.ranked:
            result1 = 1.0 if winner == c.id else 0.0 if winner == o.id else 0.5
            elo_c1, elo_o1 = E.elo_apply(elo_c0, elo_o0, result1)
            database.set_user_elo(c.id, elo_c1)
            database.set_user_elo(o.id, elo_o1)
            reward_line = self._apply_rewards(c, o, winner, elo_c0, elo_o0)

        database.record_duel(c.id, o.id, s_c, s_o, winner, s.ranked,
                             elo_c0, elo_o0, elo_c1, elo_o1,
                             self._lineup_card_ids(lu_c), self._lineup_card_ids(lu_o))
        view._cleanup()

        # --- Embed résultat ---
        if winner is None:
            title = f"🤝 Match nul {s_c} - {s_o}"
            color = discord.Color.greyple()
        else:
            win_member = c if winner == c.id else o
            title = f"🏆 {win_member.display_name} l'emporte {max(s_c, s_o)} - {min(s_c, s_o)} !"
            color = discord.Color.gold()
        e = discord.Embed(title=title, color=color)
        e.add_field(name=f"{c.display_name}", value=self._team_summary(lu_c, det_c, s_c), inline=True)
        e.add_field(name=f"{o.display_name}", value=self._team_summary(lu_o, det_o, s_o), inline=True)
        mvp = self._mvp(lu_c if s_c >= s_o else lu_o)
        if mvp:
            e.add_field(name="⭐ Homme du match", value=f"{RARITY_EMOJI.get(mvp['rarete'], '🔹')} {mvp['nom']}", inline=False)
        if s.ranked:
            e.add_field(name="📊 Elo",
                        value=f"{c.display_name} : {elo_c0} → **{elo_c1}**\n{o.display_name} : {elo_o0} → **{elo_o1}**",
                        inline=False)
            if reward_line:
                e.add_field(name="🎁 Récompenses", value=reward_line, inline=False)
        else:
            e.set_footer(text="Match amical — aucun impact sur l'Elo.")
        await view.message.edit(embed=e, view=view)
        view.stop()

    def _apply_rewards(self, c, o, winner, elo_c0, elo_o0):
        """Points au vainqueur (scalés par l'Elo), consolation au perdant, avec plafond quotidien."""
        if winner is None:
            return ""
        since = _today_start_iso()
        win_member = c if winner == c.id else o
        lose_member = o if winner == c.id else c
        win_elo = elo_c0 if winner == c.id else elo_o0
        lose_elo = elo_o0 if winner == c.id else elo_c0

        lines = []
        if database.count_ranked_duels_for(win_member.id, since) <= DAILY_REWARD_CAP:
            gain = E.duel_reward(win_elo, lose_elo)
            database.update_points(win_member.id, gain)
            lines.append(f"🥇 {win_member.display_name} : +{gain} points")
        else:
            lines.append(f"🥇 {win_member.display_name} : plafond quotidien atteint (0 pt)")
        if database.count_ranked_duels_for(lose_member.id, since) <= DAILY_REWARD_CAP:
            database.update_points(lose_member.id, E.DUEL_LOSS_POINTS)
            lines.append(f"🥈 {lose_member.display_name} : +{E.DUEL_LOSS_POINTS} points")
        return "\n".join(lines)

    def _team_summary(self, lineup, details, score):
        lines = [f"**Score : {score}**", f"Puissance : {round(details['base_total'] * details['synergy'])}",
                 f"Synergie club : ×{details['synergy']} (max {details['max_club_group']})", ""]
        for slot in E.SLOTS:
            card = lineup.get(slot)
            if card:
                emoji = RARITY_EMOJI.get(card["rarete"], "🔹")
                at_post = "✓" if E.normalize_poste(card.get("poste")) == slot else "✗"
                lines.append(f"`{slot}` {emoji} {card['nom']} {at_post}")
            else:
                lines.append(f"`{slot}` — *(vide)*")
        return "\n".join(lines)

    @app_commands.command(name="classement_duel", description="Classement Elo des duels.")
    @beta_guard()
    async def classement_duel(self, interaction: discord.Interaction):
        data = database.get_duel_leaderboard(limit=15)
        if not data:
            return await interaction.response.send_message("Aucun duel classé pour l'instant.", ephemeral=True)
        desc = ""
        for i, row in enumerate(data, 1):
            m = interaction.guild.get_member(row["user_id"]) if interaction.guild else None
            name = m.display_name if m else "Inconnu"
            medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else f"#{i}"
            desc += f"{medal} **{name}** — {row['elo']} Elo ({row['victoires']}/{row['matchs']} V)\n"
        e = discord.Embed(title="🏆 Classement des duels", description=desc, color=discord.Color.gold())
        await interaction.response.send_message(embed=e)


async def setup(bot):
    await bot.add_cog(DuelCog(bot))
