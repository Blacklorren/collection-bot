"""
Duels entre joueurs (Saison 2) — ASYMÉTRIQUES.

PRINCIPE : un duel n'exige plus que les deux joueurs soient connectés en même
temps. On attaque qui on veut, dès que la cible possède au moins une carte
jouable, et le match se résout immédiatement.

  - L'ATTAQUANT est présent : il compose son équipe, puis lance l'attaque.
  - Le DÉFENSEUR est absent : son équipe est sa COMPO AUTOMATIQUE, c'est-à-dire
    ses meilleures cartes de la saison en cours (`auto_lineup`). Elle se met donc
    à jour toute seule quand sa collection grandit, et elle est impossible à
    saboter — personne ne peut laisser une défense fantoche pour offrir des
    victoires à ses amis.
  - Le défenseur NE RISQUE RIEN et NE GAGNE RIEN : son Elo ne bouge pas, il ne
    perd aucun pack, et sa défense ne lui en rapporte aucun. Elle protège son
    classement, elle ne le fait pas monter. Il reçoit un compte-rendu en MP.
    Se faire attaquer pendant son sommeil est donc neutre — jamais une punition,
    jamais un revenu passif non plus.

RÉCOMPENSES : un match ne rapporte QUE de l'Elo. Les packs tombent une fois par
jour, par paliers (cf duel_engine.DAILY_PACK_LADDER et la boucle
daily_packs_loop), et le barème compte des **adversaires DISTINCTS battus en
attaque** — rebattre la même cible dans la journée ne compte qu'une fois. C'est
l'anti-farm : les 2 packs exigent cinq adversaires différents, hors de portée en
matraquant les deux ou trois collections les plus faibles du serveur. Corollaire :
la 2ᵉ attaque autorisée sur une même cible est un droit à la REVANCHE, utile
seulement si on a perdu la première — et comme le palier haut est à 5 sur 6
matchs, la journée garde exactement un match de marge pour cette revanche.

ÉTAT (handoff) :
  ✅ Moteur d'équilibrage   -> duel_engine.py (testé Monte-Carlo)
  ✅ Fonctions DB           -> database.py (elo, record_duel, anti-farm, leaderboard)
  ✅ Flux ASYNCHRONE        -> /defi @membre [amical] : composition -> match -> Elo
  ✅ Compo du défenseur     -> auto_lineup() sur sa collection, figée au lancement de l'attaque
  ✅ Composition MANUELLE   -> DuelPrepView / LineupPicker : l'attaquant ajuste son équipe
     slot par slot dans un menu privé (éphémère), avec un bouton « Compo automatique ».
  ✅ Elo asymétrique        -> elo_apply_attacker() : seul l'attaquant met son Elo en jeu
  ✅ Compte-rendu défenseur -> MP après chaque attaque subie (plafonné)
  ✅ Classement             -> /classement_duel
  ✅ Historique             -> /historique_duel [membre] · /defenses [membre]
  ✅ Bande Elo DOUCE        -> classé hors bande autorisé, mais K réduit
  ✅ Packs quotidiens        -> daily_packs_loop : bilan de la veille -> packs (idempotent)
  ✅ Compo préremplie       -> dernière compo jouée (cartes encore possédées), sinon compo auto
  ✅ Narration              -> coup d'envoi → mi-temps → résultat (éditions successives)
  ✅ Entraînement solo      -> /defi en visant le BOT (testeurs uniquement) : équipe synthétique
     tirée de cards.json (paramètre `difficulte`), et RIEN n'est écrit en base.

Gating : beta_guard (visible-mais-bloqué jusqu'au 25 août, cf beta.py).
"""
import asyncio
import os
import random
from datetime import date, datetime, timedelta

import discord
import pytz
from discord import app_commands
from discord.ext import commands, tasks

import database
import duel_engine as E
from beta import beta_guard, BetaLocked, is_tester
from cogs.collection_cog import load_cards_data, RARITY_EMOJI, saison_de, saison_en_cours

PARIS = pytz.timezone("Europe/Paris")

# --- Réglages anti-farm (surchargeables via .env) ---
DUEL_ELO_BAND = int(os.getenv("DUEL_ELO_BAND", str(E.ELO_BAND)))   # au-delà : classé « hors bande »
DUEL_SOFT_K = int(os.getenv("DUEL_SOFT_K", str(E.ELO_K_SOFT)))     # K réduit hors bande

# Plafond DUR : passé DAILY_MATCH_CAP attaques classées, la journée est terminée.
# Ce n'est plus un plafond de récompenses (les gains par match n'existent plus),
# c'est la LONGUEUR de la journée de jeu — et donc ce qui borne les paliers de packs.
DAILY_MATCH_CAP = int(os.getenv("DUEL_DAILY_MATCH_CAP", "6"))      # attaques classées/jour/joueur
# Deux attaques par cible et par jour. Depuis que le barème compte des adversaires
# DISTINCTS, ce plafond n'est plus l'anti-farm principal : il sert surtout de droit
# à la revanche (la 2ᵉ attaque ne rapporte un pack que si la 1ʳᵉ a été perdue) et
# de garde-fou contre le harcèlement d'une seule cible.
DAILY_PAIR_CAP = int(os.getenv("DUEL_DAILY_PAIR_CAP", "2"))        # duels classés/jour entre 2 mêmes joueurs

# La défense ne rapporte plus rien — elle protège l'Elo, elle ne le fait pas monter.
# Ce plafond ne limite donc PAS les attaques subies (on ne peut pas empêcher les
# autres de nous défier), seulement les MP de compte-rendu qu'elles déclenchent.
DEFENSE_DM_CAP = int(os.getenv("DUEL_DEFENSE_DM_CAP", "5"))           # MP de compte-rendu/jour

# --- Distribution quotidienne des packs ---
# Boucle de POLLING plutôt qu'un rendez-vous fixe à minuit : un bot redémarré à
# 00 h 02 raterait le rendez-vous, et personne ne serait payé ce jour-là. Ici on
# repasse régulièrement et on solde les journées en retard — le bot peut tomber
# une nuit entière, la journée sera payée à son réveil.
DAILY_PACKS_CHECK_MINUTES = int(os.getenv("DUEL_DAILY_PACKS_CHECK_MINUTES", "15"))
DAILY_PACKS_CATCHUP_DAYS = int(os.getenv("DUEL_DAILY_PACKS_CATCHUP_DAYS", "7"))

# --- Entraînement solo : /defi en visant le bot (testeurs uniquement, cf beta.is_tester).
# Le bot n'est pas un joueur : son équipe est synthétique et RIEN n'est écrit en base.
SPAR_RARITIES = ["Commun", "Peu Commun", "Rare", "Épique", "Légendaire"]
SPAR_DEFAULT_RARITY = "Rare"

# Verrou : un joueur ne peut préparer qu'une attaque à la fois.
# Seuls les ATTAQUANTS y figurent — un défenseur absent n'a rien à verrouiller,
# et plusieurs joueurs peuvent parfaitement attaquer la même cible en parallèle.
ACTIVE_DUELISTS = set()


def _midnight_utc(jour: date) -> str:
    """Minuit (Europe/Paris) d'un jour donné, au format comparable par SQLite (UTC)."""
    minuit = PARIS.localize(datetime(jour.year, jour.month, jour.day))
    return minuit.astimezone(pytz.utc).strftime("%Y-%m-%d %H:%M:%S")


def _today_start_iso():
    """Minuit (Europe/Paris) du jour courant, au format comparable par SQLite (UTC)."""
    return _midnight_utc(datetime.now(PARIS).date())


def _day_bounds(jour: date):
    """(début inclus, fin exclue) d'une journée de Paris, en UTC.

    La borne haute est calculée à partir de la DATE du lendemain, jamais par
    `début + 24 h` : les deux nuits de changement d'heure durent 23 h et 25 h, et
    un décalage d'une heure ferait basculer les duels de fin de soirée dans la
    mauvaise journée — donc dans le mauvais décompte de victoires.
    """
    return _midnight_utc(jour), _midnight_utc(jour + timedelta(days=1))


def _fmt_date(created_at):
    """created_at SQLite (UTC) -> 'jj/mm' heure de Paris."""
    try:
        dt = datetime.strptime(str(created_at), "%Y-%m-%d %H:%M:%S")
        return pytz.utc.localize(dt).astimezone(PARIS).strftime("%d/%m")
    except (ValueError, TypeError):
        return "?"


class DuelSession:
    """État d'un duel asymétrique pendant la phase de préparation.

    Seul l'attaquant est présent : l'équipe du défenseur est déjà figée au moment
    du `/defi`, pour que la puissance annoncée à l'attaquant soit exactement celle
    qu'il affrontera, même s'il met dix minutes à composer.
    """

    def __init__(self, attacker, defender, ranked, lineup_a, lineup_d, sparring=False):
        self.attacker = attacker          # discord.Member (présent)
        self.defender = defender          # discord.Member (absent, ou le bot en entraînement)
        self.ranked = ranked
        self.sparring = sparring          # adversaire = le bot : aucune lecture/écriture en base
        self.lineup_a = lineup_a if lineup_a is not None else {s: None for s in E.SLOTS}
        self.lineup_d = lineup_d if lineup_d is not None else {s: None for s in E.SLOTS}
        self.cancelled = False            # annulation/expiration : invalide le picker ouvert

    def defender_power(self):
        return E.team_power(self.lineup_d)[0]


class LineupPicker(discord.ui.View):
    """Sélecteur privé (éphémère) : l'attaquant compose son équipe poste par poste.
    Choix d'un slot (1) → parcours d'un club (2) → placement d'une carte (3).
    Une même carte ne peut occuper qu'un seul slot (déplacée automatiquement)."""

    def __init__(self, cog, session, main_view):
        super().__init__(timeout=180)
        self.cog = cog
        self.s = session
        self.main_view = main_view
        self.user_id = session.attacker.id
        self.current_slot = E.SLOTS[0]
        self.current_club = None
        self._refresh_components()

    async def interaction_check(self, interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Ce n'est pas ton équipe.", ephemeral=True)
            return False
        if self.s.cancelled:
            await interaction.response.send_message("⌛ Cette attaque a été annulée ou a expiré.", ephemeral=True)
            self.stop()
            return False
        return True

    def lineup(self):
        return self.s.lineup_a

    def _grouped_owned(self):
        """{club: [card_dict, ...]} des cartes jouables possédées (dédupliquées par carte)."""
        clubs, seen = {}, set()
        for cid in database.get_user_collection(self.user_id):
            if cid in seen:
                continue
            seen.add(cid)
            card = self.cog.get_card(cid)
            if self.cog.jouable(card):
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
        e.add_field(name="🛡️ Défense adverse",
                    value=f"puissance **{round(self.s.defender_power())}**", inline=False)
        e.set_footer(text=f"{filled}/7 postes · ta puissance {round(pow_)} · ✓ = à son poste (×{E.POSTE_BONUS})")
        return e

    async def _apply(self, interaction):
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

    @discord.ui.button(label="Lancer l'attaque", emoji="⚔️", style=discord.ButtonStyle.green, row=3)
    async def launch_btn(self, interaction, button):
        if not any(self.lineup().values()):
            return await interaction.response.send_message(
                "Aligne au moins une carte (ou clique « Compo automatique »).", ephemeral=True)
        e = self._embed()
        e.title = "⚔️ Équipe validée — le match se joue…"
        e.color = discord.Color.green()
        await interaction.response.edit_message(embed=e, view=None)
        self.stop()
        await self.main_view.launch()


class DuelPrepView(discord.ui.View):
    """Message public de préparation : seul l'attaquant y touche.

    Il n'y a plus de phase d'acceptation ni d'attente : le défenseur est absent,
    son équipe est déjà connue, et le match part au clic.
    """

    def __init__(self, cog, session):
        super().__init__(timeout=300)
        self.cog = cog
        self.s = session
        self.message = None
        self.launched = False

    async def interaction_check(self, interaction):
        if interaction.user.id != self.s.attacker.id:
            await interaction.response.send_message("Ce n'est pas ton attaque.", ephemeral=True)
            return False
        return True

    def build_embed(self):
        s = self.s
        pow_a = round(E.team_power(s.lineup_a)[0])
        pow_d = round(s.defender_power())
        if s.sparring:
            title = "🤖 Entraînement"
            desc = ("L'équipe du bot est prête.\n"
                    "Clique **Attaquer** pour valider ta compo préremplie, ou **Composer mon équipe** "
                    "pour l'ajuster dans un menu privé.")
            mode = "🤖 Entraînement — rien n'est enregistré"
            def_label = "🤖 Équipe du bot"
        else:
            title = f"⚔️ Attaque sur {s.defender.display_name}"
            desc = (f"{s.defender.display_name} n'a pas besoin d'être connecté : sa défense est "
                    f"son **équipe automatique**, ses meilleures cartes de la saison.\n\n"
                    "Ta compo est **préremplie** (dernière compo jouée, sinon compo auto). "
                    "Clique **Attaquer** pour la valider telle quelle, ou **Composer mon équipe** "
                    "pour l'ajuster dans un menu privé.")
            mode = f"Mode : {'🏆 Classé' if s.ranked else '🤝 Amical'} · seul ton Elo est en jeu"
            def_label = f"🛡️ Défense de {s.defender.display_name}"
        e = discord.Embed(title=title, description=desc, color=discord.Color.blurple())
        e.add_field(name=f"⚔️ {s.attacker.display_name}", value=f"puissance **{pow_a}**", inline=True)
        e.add_field(name=def_label, value=f"puissance **{pow_d}**", inline=True)
        e.set_footer(text=mode)
        return e

    def _cleanup(self):
        ACTIVE_DUELISTS.discard(self.s.attacker.id)

    async def refresh(self):
        if self.message:
            try:
                await self.message.edit(embed=self.build_embed(), view=self)
            except discord.HTTPException:
                pass

    @discord.ui.button(label="Composer mon équipe", emoji="🛠️", style=discord.ButtonStyle.blurple, row=0)
    async def compose_btn(self, interaction, button):
        picker = LineupPicker(self.cog, self.s, self)
        await interaction.response.send_message(embed=picker._embed(), view=picker, ephemeral=True)

    @discord.ui.button(label="Attaquer", emoji="⚔️", style=discord.ButtonStyle.green, row=0)
    async def attack_btn(self, interaction, button):
        """Valide la compo préremplie sans ouvrir le picker."""
        if not any(self.s.lineup_a.values()):
            return await interaction.response.send_message(
                "Tu n'as aucune carte alignée : clique « Composer mon équipe ».", ephemeral=True)
        await interaction.response.defer()
        await self.launch()

    @discord.ui.button(label="Annuler", emoji="❌", style=discord.ButtonStyle.red, row=0)
    async def cancel_btn(self, interaction, button):
        if self.launched:
            return await interaction.response.defer()
        self.s.cancelled = True
        self._cleanup()
        e = self.build_embed()
        e.title = "❌ Attaque annulée"
        e.color = discord.Color.red()
        await interaction.response.edit_message(embed=e, view=None)
        self.stop()

    async def launch(self):
        """Lance le match. Idempotent : un double-clic ne joue pas deux duels."""
        if self.s.cancelled or self.launched:
            return
        self.launched = True
        self.s.cancelled = True   # invalide un picker resté ouvert
        await self.cog.play_match(self)

    async def on_timeout(self):
        if self.launched:
            return
        self.s.cancelled = True
        self._cleanup()
        if self.message:
            e = self.build_embed()
            e.title = "⌛ Attaque expirée (composition trop longue)"
            e.color = discord.Color.greyple()
            try:
                await self.message.edit(embed=e, view=None)
            except discord.HTTPException:
                pass


class DuelCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.all_cards = load_cards_data()
        # Saison EFFECTIVE, pas la constante : si les cartes de la saison n'ont pas
        # encore ete publiees, on suit celle qui l'est (cf saison_en_cours).
        self.saison = saison_en_cours(self.all_cards)
        self.card_map = {}
        for c in self.all_cards:
            self.card_map[c["id"]] = c
            self.card_map[str(c["id"])] = c
        # Journées déjà soldées pendant CETTE session : évite de re-scanner la même
        # semaine tous les quarts d'heure. Volontairement pas persisté — après un
        # redémarrage on repasse dessus, et le verrou SQL refuse le double paiement.
        self._settled_days = set()

    def jouable(self, card):
        """Une carte est alignable en duel si elle est de la saison EN COURS.

        Les cartes des saisons passees restent dans la collection — c'est l'archive —
        mais ne descendent plus sur le terrain : sinon un ancien alignerait d'entree
        une equipe complete heritee de la saison 1, contre laquelle un nouveau ne
        peut rien. Les cartes Noel restent exclues, c'est une promo."""
        return bool(card) and card.get("rarete") != "Noël" and saison_de(card) == self.saison

    def get_card(self, cid):
        return self.card_map.get(cid) or self.card_map.get(str(cid))

    def has_playable_cards(self, user_id):
        """True si le joueur possède au moins une carte alignable — le seul critère
        pour être défiable en duel asynchrone."""
        return any(self.jouable(self.get_card(c))
                   for c in database.get_user_collection(user_id))

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

    # --- Compositions ---
    def auto_lineup(self, user_id):
        """Aligne la meilleure carte possédée sur chaque poste (glouton par note de poste).
        Retourne {slot: card_dict | None}.

        C'est AUSSI la compo de défense en duel asymétrique : elle ne demande aucune
        action au joueur, suit sa collection sans qu'il y pense, et ne peut pas être
        bradée volontairement pour offrir des victoires."""
        seen, cards = set(), []
        for cid in database.get_user_collection(user_id):
            if cid in seen:
                continue
            seen.add(cid)
            card = self.get_card(cid)
            if self.jouable(card):
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

    def defense_lineup(self, user_id):
        """Équipe qui défend quand ce joueur est attaqué en son absence."""
        return self.auto_lineup(user_id)

    def sparring_lineup(self, rarete):
        """Équipe synthétique du bot pour un entraînement : une carte de `rarete`
        par poste, à son poste naturel quand le pool le permet (donc bonus ×1.4).
        Les cartes sortent de cards.json, pas d'une collection : rien en base."""
        pool = [c for c in self.all_cards if c.get("rarete") == rarete and self.jouable(c)]
        lineup = {s: None for s in E.SLOTS}
        used = set()
        for slot in E.SLOTS:
            # d'abord une carte dont c'est le poste naturel, sinon n'importe laquelle
            for natural in (True, False):
                picks = [c for c in pool if id(c) not in used
                         and (E.normalize_poste(c.get("poste")) == slot) == natural]
                if picks:
                    card = random.choice(picks)
                    lineup[slot] = card
                    used.add(id(card))
                    break
        return lineup

    def initial_lineup(self, user_id):
        """Compo préremplie de l'attaquant : dernière compo jouée (cartes encore
        possédées), sinon compo auto."""
        last = database.get_last_duel_lineup(user_id) or {}
        owned = {str(cid) for cid in database.get_user_collection(user_id)}
        lineup = {s: None for s in E.SLOTS}
        for slot in E.SLOTS:
            cid = last.get(slot)
            card = self.get_card(cid) if cid is not None else None
            if self.jouable(card) and str(card["id"]) in owned:
                lineup[slot] = card
        if not any(lineup.values()):
            lineup = self.auto_lineup(user_id)
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

    @app_commands.command(name="defi", description="Attaquer un autre joueur — il n'a pas besoin d'être connecté.")
    @app_commands.describe(membre="Le joueur à attaquer (les testeurs peuvent viser le bot pour s'entraîner)",
                           amical="Match amical : ni Elo, ni pack, et hors quota quotidien",
                           difficulte="Entraînement contre le bot : rareté de son équipe")
    @app_commands.choices(difficulte=[app_commands.Choice(name=r, value=r) for r in SPAR_RARITIES])
    @beta_guard()
    async def defi(self, interaction: discord.Interaction, membre: discord.Member,
                   amical: bool = False, difficulte: app_commands.Choice[str] = None):
        attacker, defender = interaction.user, membre
        # Viser le bot lance un ENTRAÎNEMENT solo, réservé aux testeurs : il joue une
        # équipe synthétique, et rien n'est enregistré.
        sparring = defender.id == self.bot.user.id and is_tester(attacker.id)
        if defender.bot and not sparring:
            return await interaction.response.send_message("Tu ne peux pas défier un bot.", ephemeral=True)
        if defender.id == attacker.id:
            return await interaction.response.send_message("Tu ne peux pas te défier toi-même.", ephemeral=True)
        # Seul l'attaquant est verrouillé : la cible, absente, n'a rien à bloquer et
        # peut parfaitement être attaquée par plusieurs joueurs en même temps.
        if attacker.id in ACTIVE_DUELISTS:
            return await interaction.response.send_message(
                "Tu prépares déjà une attaque, termine-la d'abord.", ephemeral=True)

        if not self.has_playable_cards(attacker.id):
            return await interaction.response.send_message("Tu n'as pas encore de cartes jouables.", ephemeral=True)

        if sparring:
            rarete = difficulte.value if difficulte else SPAR_DEFAULT_RARITY
            session = DuelSession(attacker, defender, ranked=False,
                                  lineup_a=self.initial_lineup(attacker.id),
                                  lineup_d=self.sparring_lineup(rarete),
                                  sparring=True)
            ACTIVE_DUELISTS.add(attacker.id)
            view = DuelPrepView(self, session)
            await self._open_prep(interaction, view, view.build_embed())
            return

        # Le SEUL prérequis côté cible : posséder des cartes jouables.
        if not self.has_playable_cards(defender.id):
            return await interaction.response.send_message(
                f"{defender.display_name} n'a aucune carte jouable de la saison en cours : "
                f"impossible de l'attaquer.", ephemeral=True)

        ranked = not amical
        soft_note = ""
        if ranked:
            elo_a, elo_d = database.get_user_elo(attacker.id), database.get_user_elo(defender.id)
            if not E.within_band(elo_a, elo_d, DUEL_ELO_BAND):
                # Bande DOUCE : le duel classé reste possible, mais avec un K réduit.
                soft_note = (f"\n⚖️ Écart d'Elo important ({elo_a} vs {elo_d}, bande ±{DUEL_ELO_BAND}) : "
                             f"attaque **hors bande** — gain d'Elo réduit.")
            since = _today_start_iso()

            # Le plafond de la JOURNÉE passe avant celui de la cible : inutile de
            # renvoyer le joueur vers un autre adversaire s'il n'a plus de match à jouer.
            matchs, battus, _packs = self._daily_status(attacker.id, since)
            if matchs >= DAILY_MATCH_CAP:
                return await interaction.response.send_message(
                    f"🏁 Journée terminée : tes **{DAILY_MATCH_CAP} matchs classés** sont joués.\n"
                    f"{self._daily_progress_text(matchs, battus)}\n\n"
                    f"Tu peux continuer en **amical** — sans Elo, et sans pack.", ephemeral=True)

            if database.count_ranked_attacks_between(attacker.id, defender.id, since) >= DAILY_PAIR_CAP:
                return await interaction.response.send_message(
                    f"🚫 Tu as déjà attaqué {defender.display_name} {DAILY_PAIR_CAP} fois en classé "
                    f"aujourd'hui. Vise quelqu'un d'autre, ou joue en **amical**.", ephemeral=True)

            # Avertissement, pas blocage : la 2ᵉ attaque reste utile pour l'Elo, et
            # c'est la seule façon de rattraper une cible qu'on avait ratée. Mais si
            # on l'a DÉJÀ battue, le match ne rapportera aucun pack — mieux vaut le
            # savoir avant de composer son équipe qu'après le coup de sifflet final.
            if database.count_ranked_attacks_between(
                    attacker.id, defender.id, since, wins_only=True):
                soft_note += (f"\n♻️ Tu as déjà battu {defender.display_name} aujourd'hui : "
                              f"ce match jouera l'Elo, mais **ne comptera pas** pour les packs "
                              f"(un adversaire ne compte qu'une fois).")

        ACTIVE_DUELISTS.add(attacker.id)
        # La défense est FIGÉE ici : la puissance annoncée est celle qui sera jouée,
        # même si la cible ouvre un pack pendant que l'attaquant compose.
        session = DuelSession(attacker, defender, ranked,
                              lineup_a=self.initial_lineup(attacker.id),
                              lineup_d=self.defense_lineup(defender.id))
        view = DuelPrepView(self, session)
        e = view.build_embed()
        if soft_note:
            e.description += f"\n{soft_note}"
        await self._open_prep(interaction, view, e)

    async def _open_prep(self, interaction, view, embed):
        """Publie le message de préparation. Si Discord refuse l'envoi, on RELÂCHE le
        verrou : sans ça l'attaquant resterait bloqué jusqu'au redémarrage du bot,
        sans aucun message à annuler."""
        try:
            await interaction.response.send_message(embed=embed, view=view)
            view.message = await interaction.original_response()
        except discord.HTTPException:
            view._cleanup()
            view.stop()
            raise

    async def play_match(self, view: "DuelPrepView"):
        """Simule, applique l'Elo de l'attaquant, enregistre, affiche, et envoie son
        compte-rendu au défenseur absent.

        Aucun crédit ici : les packs se calculent sur le bilan du jour et sont versés
        par `daily_packs_loop`. Ce que le joueur voit en fin de match n'est donc qu'un
        état d'avancement, pas un gain."""
        s = view.s
        try:
            a, d = s.attacker, s.defender
            lu_a, lu_d = s.lineup_a, s.lineup_d
            pow_a, det_a = E.team_power(lu_a)
            pow_d, det_d = E.team_power(lu_d)

            s_a, s_d, half, overtime = E.simulate_match(pow_a, pow_d, allow_draw=False)
            winner = a.id if s_a > s_d else d.id if s_d > s_a else None
            mode = "🤖 Entraînement" if s.sparring else ("🏆 Classé" if s.ranked else "🤝 Amical")

            # --- Narration : coup d'envoi → mi-temps → résultat ---
            # view=None : dès le coup d'envoi les boutons DISPARAISSENT du message.
            # Les composants Discord sont attachés au message, pas au lecteur : impossible
            # de les montrer au seul attaquant. Les griser laissait donc trois boutons
            # morts sous chaque résultat, pour tout le salon.
            kick = discord.Embed(
                title="🟢 Coup d'envoi !",
                description=f"**{a.display_name}** (puissance {round(pow_a)}) attaque "
                            f"**{d.display_name}** (puissance {round(pow_d)})…",
                color=discord.Color.blurple())
            kick.set_footer(text=mode)
            await view.message.edit(embed=kick, view=None)
            await asyncio.sleep(2.5)

            ht = discord.Embed(
                title=f"⏸️ Mi-temps : {half[0]} - {half[1]}",
                description=f"**{a.display_name}** {half[0]} · {half[1]} **{d.display_name}**",
                color=discord.Color.blurple())
            ht.set_footer(text=mode)
            await view.message.edit(embed=ht, view=None)
            await asyncio.sleep(2.5)

            if s.sparring:
                # Entraînement : on ne touche pas à la base (get_user_elo créerait
                # une ligne `users` pour le bot via check_user()).
                elo_a0 = elo_d0 = E.ELO_START
            else:
                elo_a0, elo_d0 = database.get_user_elo(a.id), database.get_user_elo(d.id)
            elo_a1 = elo_a0
            soft = False

            if s.ranked:
                # Bande douce : hors bande, le duel compte mais avec un K réduit. Les
                # packs, eux, ne dépendent pas de l'écart d'Elo — une victoire vaut
                # une victoire, quelle que soit la cible.
                soft = not E.within_band(elo_a0, elo_d0, DUEL_ELO_BAND)
                k = DUEL_SOFT_K if soft else E.ELO_K
                result_a = 1.0 if winner == a.id else 0.0 if winner == d.id else 0.5
                # ASYMÉTRIE : seul l'attaquant met son Elo en jeu. Le défenseur n'a
                # pas choisi ce match, il ne peut pas le perdre.
                elo_a1 = E.elo_apply_attacker(elo_a0, elo_d0, result_a, k=k)
                database.set_user_elo(a.id, elo_a1)

            if not s.sparring:
                # elo2_before == elo2_after : la trace montre explicitement que le
                # défenseur n'a rien mis en jeu.
                database.record_duel(a.id, d.id, s_a, s_d, winner, s.ranked,
                                     elo_a0, elo_d0, elo_a1, elo_d0,
                                     self._lineup_card_ids(lu_a), self._lineup_card_ids(lu_d))

            # --- Embed résultat ---
            ms = " (mort subite)" if overtime else ""
            if winner is None:
                title = f"🤝 Match nul {s_a} - {s_d}"
                color = discord.Color.greyple()
            else:
                win_member = a if winner == a.id else d
                title = f"🏆 {win_member.display_name} l'emporte {max(s_a, s_d)} - {min(s_a, s_d)}{ms} !"
                color = discord.Color.gold()
            e = discord.Embed(title=title, color=color)
            e.add_field(name=f"⚔️ {a.display_name}", value=self._team_summary(lu_a, det_a, s_a), inline=True)
            e.add_field(name=f"🛡️ {d.display_name}", value=self._team_summary(lu_d, det_d, s_d), inline=True)
            mvp = self._mvp(lu_a if s_a >= s_d else lu_d)
            if mvp:
                e.add_field(name="⭐ Homme du match", value=f"{RARITY_EMOJI.get(mvp['rarete'], '🔹')} {mvp['nom']}", inline=False)
            if s.ranked:
                elo_note = " · ⚖️ hors bande (gain d'Elo réduit)" if soft else ""
                e.add_field(name="📊 Elo",
                            value=f"{a.display_name} : {elo_a0} → **{elo_a1}**{elo_note}\n"
                                  f"{d.display_name} : {elo_d0} (inchangé — en défense, on ne risque rien)",
                            inline=False)
                # Le duel vient d'être enregistré en base : ce décompte inclut donc
                # le match qu'on est en train d'afficher.
                matchs, battus, _packs = self._daily_status(a.id)
                bilan = self._daily_progress_text(matchs, battus)
                # Une victoire sur une cible déjà battue laisse le compteur immobile.
                # Sans un mot ici, ça se lit comme un bug — c'est exactement le
                # moment où il faut expliquer la règle, pas dans une page d'aide.
                if winner == a.id and database.count_ranked_attacks_between(
                        a.id, d.id, _today_start_iso(), wins_only=True) > 1:
                    bilan += ("\n⚠️ Tu avais **déjà battu ce joueur aujourd'hui** : cette "
                              "victoire compte pour l'Elo, pas pour les packs.")
                e.add_field(name="🎁 Packs du jour", value=bilan, inline=False)
            else:
                e.set_footer(text="🤖 Entraînement — rien n'est enregistré (ni Elo, ni historique)."
                                  if s.sparring else "Match amical — aucun impact sur l'Elo.")
            await view.message.edit(embed=e, view=None)
            view.stop()

            if not s.sparring:
                await self._notify_defender(a, d, s_a, s_d, winner, s.ranked)
        finally:
            # Quoi qu'il arrive (exception comprise), on libère l'attaquant.
            view._cleanup()

    # === LA JOURNÉE DE DUEL : plafond de matchs, paliers de packs ===
    #
    # Un match ne crédite plus rien au moment où il se joue — il ne bouge que l'Elo.
    # Ce qui paie, c'est le BILAN de la journée, versé en packs la nuit suivante
    # (cf _settle_day). Les deux helpers ci-dessous sont donc en LECTURE SEULE : ils
    # affichent où en est le joueur, ils ne créditent jamais.

    def _daily_status(self, user_id, since=None):
        """(matchs joués, adversaires distincts battus, packs mérités en l'état).

        Le second terme n'est PAS le nombre de victoires brutes : rebattre la même
        cible dans la journée ne compte qu'une fois (cf `count_beaten_opponents_for`).
        """
        since = since or _today_start_iso()
        matchs = database.count_ranked_attacks_for(user_id, since)
        battus = database.count_beaten_opponents_for(user_id, since)
        return matchs, battus, E.packs_for_wins(battus)

    @staticmethod
    def _daily_progress_text(matchs, battus):
        """Bilan du jour + prochain palier, chiffré en ADVERSAIRES BATTUS.

        On dit « adversaires battus » et jamais « victoires » : c'est ce que le
        barème compte réellement, et le joueur qui vient de rebattre la même cible
        doit comprendre du premier coup d'œil pourquoi son compteur n'a pas bougé.
        Quand les matchs restants ne suffisent plus à décrocher le palier, on le
        dit — mieux vaut une journée annoncée close qu'un espoir entretenu.
        """
        packs = E.packs_for_wins(battus)
        restants = max(0, DAILY_MATCH_CAP - matchs)
        s_b = "s" if battus > 1 else ""
        s_p = "s" if packs > 1 else ""
        lignes = [f"**{battus} adversaire{s_b} battu{s_b}** en {matchs}/{DAILY_MATCH_CAP} matchs "
                  f"→ **{packs} pack{s_p}** cette nuit"]
        palier = E.next_pack_tier(battus)
        if not restants:
            lignes.append("🏁 Journée close : ce bilan est définitif.")
        elif palier is None:
            lignes.append("🏅 Palier maximum : les matchs restants ne jouent plus que l'Elo.")
        else:
            manque, cible = palier
            s_m = "s" if manque > 1 else ""
            s_c = "s" if cible > 1 else ""
            if manque <= restants:
                lignes.append(f"Encore **{manque} adversaire{s_m} différent{s_m}** "
                              f"→ {cible} pack{s_c}.")
            else:
                lignes.append("Le palier suivant n'est plus atteignable aujourd'hui.")
        return "\n".join(lignes)

    # === DISTRIBUTION QUOTIDIENNE DES PACKS ===

    async def cog_load(self):
        self.daily_packs_loop.start()

    async def cog_unload(self):
        self.daily_packs_loop.cancel()

    @tasks.loop(minutes=DAILY_PACKS_CHECK_MINUTES)
    async def daily_packs_loop(self):
        """Solde les journées écoulées : compte les victoires, crédite les packs.

        JAMAIS la journée en cours : tant qu'il reste des matchs à jouer, le bilan
        n'est pas définitif. On part donc de la veille et on remonte jusqu'à
        DAILY_PACKS_CATCHUP_DAYS jours en arrière — c'est ce qui rattrape les nuits
        où le bot était éteint, sans quoi une coupure à minuit volerait une journée
        de jeu à tout le monde.
        """
        aujourdhui = datetime.now(PARIS).date()
        for recul in range(DAILY_PACKS_CATCHUP_DAYS, 0, -1):
            jour = aujourdhui - timedelta(days=recul)
            if jour in self._settled_days:
                continue
            try:
                await self._settle_day(jour)
            except Exception as err:
                # Une journée qui échoue ne doit ni bloquer les autres, ni tuer la
                # boucle : on la laisse hors de _settled_days, elle sera retentée.
                print(f"❌ (DUEL) Packs du {jour} : {type(err).__name__} : {err}")
                continue
            self._settled_days.add(jour)

    @daily_packs_loop.before_loop
    async def before_daily_packs_loop(self):
        await self.bot.wait_until_ready()

    async def _settle_day(self, jour):
        """Crédite les packs mérités par chaque attaquant pour la journée `jour`."""
        debut, fin = _day_bounds(jour)
        for user_id in database.get_ranked_attackers_between(debut, fin):
            battus = database.count_beaten_opponents_for(user_id, debut, fin)
            packs = E.packs_for_wins(battus)
            if not packs:
                continue
            # grant_duel_daily_packs EST le verrou : il ne renvoie True qu'au premier
            # crédit de la journée. Relancer la boucle ne peut donc pas payer deux fois,
            # et le MP ne part qu'avec le crédit réel.
            if database.grant_duel_daily_packs(user_id, jour.isoformat(), battus, packs):
                await self._notify_daily_packs(user_id, jour, battus, packs)

    async def _notify_daily_packs(self, user_id, jour, battus, packs):
        """MP de bilan. Un joueur injoignable (MP fermés, compte parti) ne fait pas
        échouer la distribution : les packs sont déjà crédités, il les verra au
        prochain /ouvrir."""
        try:
            user = self.bot.get_user(user_id) or await self.bot.fetch_user(user_id)
        except (discord.NotFound, discord.HTTPException):
            return
        s_b = "s" if battus > 1 else ""
        s_p = "s" if packs > 1 else ""
        quand = jour.strftime("%d/%m")
        e = discord.Embed(
            title=f"🎁 {packs} pack{s_p} pour ta journée de duels",
            description=f"**{battus} adversaire{s_b} différent{s_b} battu{s_b}** le {quand} "
                        f"→ **{packs} pack{s_p}** crédité{s_p}. À ouvrir avec `/ouvrir`.",
            color=discord.Color.gold())
        e.set_footer(text=f"{DAILY_MATCH_CAP} matchs classés par jour · {E.ladder_text()}")
        try:
            await user.send(embed=e)
        except (discord.Forbidden, discord.HTTPException):
            pass

    async def _notify_defender(self, a, d, s_a, s_d, winner, ranked):
        """Compte-rendu en MP au défenseur absent.

        Plafonné à DEFENSE_DM_CAP par jour : une cible populaire ne doit pas se
        réveiller avec trente MP. Un joueur MP fermés ne fait pas échouer le duel.
        """
        try:
            if database.count_defenses_for(d.id, _today_start_iso(), ranked_only=False) > DEFENSE_DM_CAP:
                return
            held = winner == d.id
            if held:
                title, color = "🛡️ Ta défense a tenu !", discord.Color.green()
            elif winner is None:
                title, color = "🤝 Ta défense a arraché le nul", discord.Color.greyple()
            else:
                title, color = "⚔️ Tu as été attaqué", discord.Color.red()
            desc = (f"**{a.display_name}** t'a attaqué pendant ton absence.\n"
                    f"Ton équipe automatique {'a gagné' if held else 'a fait match nul' if winner is None else 'a perdu'} "
                    f"**{s_d} - {s_a}**.")
            e = discord.Embed(title=title, description=desc, color=color)
            if ranked:
                e.add_field(
                    name="🛡️ Ce que tu risquais",
                    value="Rien. En défense ton Elo ne bouge pas et tu ne perds aucun pack — "
                          "mais une défense qui tient n'en rapporte pas non plus : "
                          "les packs se gagnent en **attaquant**.",
                    inline=False)
            e.set_footer(text="Enrichis ta collection pour renforcer ta défense automatique, "
                              "et lance tes propres attaques avec /defi.")
            await d.send(embed=e)
        except (discord.Forbidden, discord.HTTPException):
            pass   # MP fermés : tant pis, le résultat reste dans /defenses

    def _team_summary(self, lineup, details, score=None):
        """Résumé d'une équipe. `score=None` : hors match (consultation de sa défense)."""
        lines = []
        if score is not None:
            lines.append(f"**Score : {score}**")
        lines += [f"Puissance : {round(details['base_total'] * details['synergy'])}",
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

    @app_commands.command(name="ma_defense", description="Voir l'équipe qui te défend quand on t'attaque.")
    @beta_guard()
    async def ma_defense(self, interaction: discord.Interaction):
        """La défense étant automatique, il faut au moins pouvoir la consulter :
        c'est ce qui rend lisible le lien « ouvrir des packs → mieux se défendre »."""
        if not self.has_playable_cards(interaction.user.id):
            return await interaction.response.send_message(
                "Tu n'as aucune carte jouable : personne ne peut t'attaquer pour l'instant.", ephemeral=True)
        lineup = self.defense_lineup(interaction.user.id)
        pow_, det = E.team_power(lineup)
        e = discord.Embed(
            title="🛡️ Ta défense automatique",
            description="C'est cette équipe qui joue quand un autre joueur t'attaque, "
                        "même hors ligne. Elle se met à jour toute seule quand ta collection grandit.",
            color=discord.Color.blurple())
        e.add_field(name="Composition", value=self._team_summary(lineup, det), inline=False)
        e.set_footer(text=f"Puissance {round(pow_)} · Elo {database.get_user_elo(interaction.user.id)} "
                          f"· en défense, tu ne perds jamais d'Elo")
        await interaction.response.send_message(embed=e, ephemeral=True)

    @app_commands.command(name="defenses", description="Les dernières attaques subies par un joueur.")
    @app_commands.describe(membre="Joueur à consulter (toi par défaut)")
    @beta_guard()
    async def defenses(self, interaction: discord.Interaction, membre: discord.Member = None):
        target = membre or interaction.user
        duels = database.get_user_defenses(target.id, limit=10)
        if not duels:
            return await interaction.response.send_message(
                f"{target.display_name} n'a encore subi aucune attaque.", ephemeral=True)
        held = sum(1 for d in duels if d["gagnant"] == target.id)
        lines = []
        for d in duels:
            att = interaction.guild.get_member(d["joueur1"]) if interaction.guild else None
            att_name = att.display_name if att else "Inconnu"
            res = "🟢" if d["gagnant"] == target.id else "⚪" if d["gagnant"] is None else "🔴"
            mode = "🏆" if d["classe"] else "🤝"
            lines.append(f"{res} {mode} `{_fmt_date(d['created_at'])}` attaqué par **{att_name}** — "
                         f"{d['score2']}-{d['score1']}")
        e = discord.Embed(title=f"🛡️ Défenses de {target.display_name}",
                          description="\n".join(lines), color=discord.Color.blurple())
        e.set_footer(text=f"{held}/{len(duels)} défenses tenues · l'Elo ne bouge jamais en défense")
        await interaction.response.send_message(embed=e)

    @app_commands.command(name="historique_duel", description="Les derniers duels d'un joueur.")
    @app_commands.describe(membre="Joueur à consulter (toi par défaut)")
    @beta_guard()
    async def historique_duel(self, interaction: discord.Interaction, membre: discord.Member = None):
        target = membre or interaction.user
        duels = database.get_user_duels(target.id, limit=10)
        if not duels:
            return await interaction.response.send_message(
                f"{target.display_name} n'a encore joué aucun duel.", ephemeral=True)
        lines = []
        for d in duels:
            is_att = d["joueur1"] == target.id
            my_score = d["score1"] if is_att else d["score2"]
            opp_score = d["score2"] if is_att else d["score1"]
            opp_id = d["joueur2"] if is_att else d["joueur1"]
            opp = interaction.guild.get_member(opp_id) if interaction.guild else None
            opp_name = opp.display_name if opp else "Inconnu"
            res = "🟢" if d["gagnant"] == target.id else "⚪" if d["gagnant"] is None else "🔴"
            mode = "🏆" if d["classe"] else "🤝"
            role = "⚔️ vs" if is_att else "🛡️ attaqué par"
            delta = ""
            if d["classe"] and d["elo1_after"] is not None:
                diff = (d["elo1_after"] - d["elo1_before"]) if is_att else (d["elo2_after"] - d["elo2_before"])
                delta = f" · {'+' if diff >= 0 else ''}{diff} Elo" if diff else ""
            lines.append(f"{res} {mode} `{_fmt_date(d['created_at'])}` {role} **{opp_name}** — "
                         f"{my_score}-{opp_score}{delta}")
        e = discord.Embed(title=f"📜 Derniers duels de {target.display_name}",
                          description="\n".join(lines), color=discord.Color.blurple())
        e.set_footer(text=f"Elo actuel : {database.get_user_elo(target.id)} · ⚔️ attaque · 🛡️ défense")
        await interaction.response.send_message(embed=e)

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
            defense = (f" · 🛡️ {row['defenses_tenues']}/{row['defenses']}"
                       if row.get("defenses") else "")
            desc += (f"{medal} **{name}** — {row['elo']} Elo "
                     f"(⚔️ {row['victoires']}/{row['matchs']} V{defense})\n")
        e = discord.Embed(title="🏆 Classement des duels", description=desc, color=discord.Color.gold())
        e.set_footer(text=f"L'Elo et les packs se gagnent à l'attaque · "
                          f"🛡️ défenses tenues (elles ne rapportent rien, elles protègent) · "
                          f"{E.ladder_text()}")
        await interaction.response.send_message(embed=e)


async def setup(bot):
    await bot.add_cog(DuelCog(bot))
