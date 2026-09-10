"""
Échange de cartes entre joueurs (Saison 2).

Refonte ergonomique. L'ancienne fenêtre demandait aux deux joueurs de composer
chacun son offre à l'aveugle, sans jamais pouvoir dire « c'est CETTE carte que je
veux ». Deux inconnues, deux paniers, et une négociation qui se faisait en réalité
dans le salon, à côté du bot.

Le principe est désormais celui d'une **proposition complète** :

- `/echange @membre` ouvre un composeur **privé** où l'on écrit les DEUX côtés :
  ce qu'on donne, et ce qu'on veut recevoir de l'autre.
- La proposition part ensuite dans le salon, lisible d'un coup d'œil. Le
  destinataire n'a plus qu'à cliquer **Accepter** — un seul clic pour échanger.
- S'il n'est pas d'accord, **Modifier** rouvre le même composeur de son point de
  vue, prérempli : c'est une contre-proposition, pas un nouvel échange.

Ce qui a été conservé :

- Pas de cadeau : les deux côtés doivent contenir au moins une carte.
- Double validation : l'échange ne part que si les DEUX ont accepté l'état affiché.
- Anti-arnaque : toute modification validée efface les acceptations précédentes,
  et n'engage que son auteur (`Deal.sign`).
- L'échange est ATOMIQUE (`database.execute_trade`) : aucune carte dupliquée/perdue.

Ce qui a été ajouté :

- **Seule la saison en cours s'échange** (`TradeCog.echangeable`). Les cartes des
  saisons passées sont une archive figée : `tools/migration_s2.py` n'en a laissé
  qu'UN exemplaire par carte, et elles ne sont plus ni tirables en pack ni
  créables au craft. Les échanger était donc à double sens interdit — céder la
  sienne était définitif, et en recevoir une seconde cassait l'exemplaire unique
  sur lequel repose l'archive.

Ce qui a changé sous le capot :

- Les paniers contiennent des **cartes** (`card_id`), plus des exemplaires précis.
  Les exemplaires ne sont choisis qu'au dernier moment (`TradeCog.resolve_rowids`),
  au clic sur Accepter : entre la proposition et l'accord, un exemplaire a pu
  partir ailleurs, et c'est la collection au moment de l'échange qui fait foi.
- Le composeur travaille sur un **brouillon**. Tant qu'on n'a pas validé, l'autre
  ne voit rien bouger et les acceptations tiennent : on peut se tromper de menu
  sans casser la négociation en cours.
"""
import unicodedata
from collections import Counter

import discord
from discord import app_commands
from discord.ext import commands

import database
from beta import beta_guard, BetaLocked
from cogs.collection_cog import load_cards_data, saison_de, saison_en_cours

RARITY_EMOJI = {
    "Commun": "⬜", "Peu Commun": "🟩", "Rare": "🟦",
    "Épique": "🟪", "Légendaire": "🟨", "Noël": "🎄",
}
# Sert uniquement à ranger les menus : les fortes en haut, à intérêt égal.
RARITY_RANK = {"Commun": 0, "Peu Commun": 1, "Rare": 2, "Noël": 2, "Épique": 3, "Légendaire": 4}

MAX_PER_SIDE = 6
PAGE_SIZE = 25          # plafond Discord pour un menu déroulant

# Une proposition attend une VRAIE personne, pas un joueur déjà devant son écran :
# cinq minutes suffisaient à faire expirer la moitié des échanges avant réponse.
PROPOSAL_TIMEOUT = 900
PICKER_TIMEOUT = 300

# Verrou : un joueur ne peut être dans qu'un seul échange à la fois. Le
# destinataire n'y entre qu'à l'ENVOI de la proposition : composer dans son coin,
# puis abandonner, ne doit bloquer personne d'autre que soi.
ACTIVE_TRADERS = set()


def release(deal):
    """Referme l'échange et libère les deux joueurs.

    Un verrou oublié bloque ses deux joueurs jusqu'au redémarrage du bot :
    toute sortie d'échange — accord, refus, expiration, erreur — passe par ici."""
    deal.closed = True
    deal.editors.clear()
    ACTIVE_TRADERS.discard(deal.a.id)
    ACTIVE_TRADERS.discard(deal.b.id)


def _fold(text):
    """Minuscules sans accents : « Rémili » se trouve en tapant « remili »."""
    text = unicodedata.normalize("NFD", str(text or ""))
    return "".join(c for c in text if unicodedata.category(c) != "Mn").lower()


def owned_counts(user_id, garde=None):
    """{clé de carte: nombre d'exemplaires} — la base parle en exemplaires, l'écran en cartes.

    La clé est TOUJOURS `str(card_id)` : les identifiants de cartes sont mixtes
    (saison 1 numérotée `1, 2, 3…`, saison 2 en slugs `banke-gustaf`), et SQLite
    les rend tels qu'ils ont été écrits. Comparer sans normaliser ferait passer
    deux exemplaires de la même carte pour deux cartes différentes.

    `garde(clé)` restreint le comptage (l'archive des saisons passées ne s'échange
    pas) : ce qu'on ne peut pas échanger ne doit apparaître nulle part, ni dans
    les menus, ni dans le « 🆕 » qui juge un échange."""
    return Counter(str(cid) for cid in database.get_user_collection(user_id)
                   if garde is None or garde(cid))


def _group(keys):
    """[(carte, quantité)] dans l'ordre d'ajout : deux exemplaires font une ligne « ×2 »."""
    out, seen = [], {}
    for k in keys:
        if k in seen:
            card, n = out[seen[k]]
            out[seen[k]] = (card, n + 1)
        else:
            seen[k] = len(out)
            out.append((k, 1))
    return out


class Deal:
    """L'échange tel qu'il est affiché : qui donne quoi, et qui a déjà dit oui.

    Les paniers sont des listes de `card_id` (doublons autorisés : on peut donner
    deux exemplaires de la même carte). Voir l'en-tête du module pour la raison.
    """

    def __init__(self, member_a, member_b):
        self.a = member_a
        self.b = member_b
        self.give = {"a": [], "b": []}
        self.accepted = {"a": False, "b": False}
        self.editors = {}          # côté -> composeur ouvert (le plus récent fait foi)
        self.published = False
        self.closed = False

    def side_of(self, user_id):
        return "a" if user_id == self.a.id else "b"

    def member(self, side):
        return self.a if side == "a" else self.b

    @staticmethod
    def other(side):
        return "b" if side == "a" else "a"

    def ready(self):
        """Pas de cadeau : un échange à sens unique n'en est pas un."""
        return bool(self.give["a"]) and bool(self.give["b"])

    def sign(self, side):
        """Enregistre une modification : elle n'engage plus que son auteur.

        Sans cet effacement, on pourrait accepter une offre puis la vider avant
        qu'elle ne parte. Ce qui s'exécute est toujours ce que les deux ont vu."""
        self.accepted = {"a": False, "b": False}
        self.accepted[side] = True


def _fmt_basket(cog, keys, giver_counts, receiver_counts):
    """Le contenu d'un côté, avec les deux seules informations qui changent la décision.

    ⚠️ : celui qui la donne s'en sépare pour de bon, c'est son dernier exemplaire.
    🆕 : celui qui la reçoit ne l'a pas du tout — c'est ce qui fait la valeur d'un
    échange, et c'est précisément ce qu'on ne pouvait pas savoir avant.
    """
    if not keys:
        return "_(rien pour l'instant)_"
    lines = []
    for key, n in _group(keys):
        card = cog.get_card(key)
        emoji = RARITY_EMOJI.get(card["rarete"], "🔹") if card else "🔹"
        nom = card["nom"] if card else f"#{key}"
        bout = f"{emoji} **{nom}**"
        if n > 1:
            bout += f" ×{n}"
        if card and card.get("club"):
            bout += f" · {card['club']}"
        marques = []
        if giver_counts.get(key, 0) - n <= 0:
            marques.append("⚠️")
        if receiver_counts.get(key, 0) == 0:
            marques.append("🆕")
        if marques:
            bout += " " + "".join(marques)
        lines.append(bout)
    return "\n".join(lines)[:1024]


class SearchModal(discord.ui.Modal, title="Chercher une carte"):
    """Filtre le menu par nom de joueur OU par club.

    C'est ce qui remplace l'ancienne étape « club » obligatoire. Le club restait
    utile pour retrouver une carte, jamais pour décider laquelle donner : il
    devient un critère de recherche parmi d'autres, pas un péage."""

    q = discord.ui.TextInput(
        label="Nom du joueur ou club",
        placeholder="Remili, Nantes, PSG…",
        required=False, max_length=40,
    )

    def __init__(self, picker):
        super().__init__()
        self.picker = picker

    async def on_submit(self, interaction):
        self.picker.query = _fold(self.q.value)
        self.picker.page = 0
        self.picker._refresh_components()
        await interaction.response.edit_message(embed=self.picker._embed(), view=self.picker)


class TradePicker(discord.ui.View):
    """Composeur privé (éphémère) : on y écrit LES DEUX côtés de l'échange.

    Il n'y a plus d'étape « club » imposée. Elle demandait de deviner dans quel
    club dort le doublon dont on veut se défaire, alors qu'on pense « ce Remili
    en trop », jamais « un Nantais ». La liste est donc unique, triée par ce qui
    sert vraiment la décision — ses doublons d'abord quand on donne, ce qui
    manque à sa collection d'abord quand on demande — et un bouton 🔎 filtre par
    nom ou par club quand la collection déborde des 25 lignes d'un menu.

    Chaque ligne dit ce qu'on ne pouvait pas savoir avant de cliquer : combien
    d'exemplaires on possède, si c'est le dernier, et si l'autre l'a déjà.
    """

    def __init__(self, cog, deal, side, main_view=None):
        super().__init__(timeout=PICKER_TIMEOUT)
        self.cog = cog
        self.deal = deal
        self.side = side
        self.main_view = main_view
        self.user_id = deal.member(side).id
        self.message = None
        self.target = "mine"      # le côté qu'on est en train de remplir
        self.query = ""
        self.page = 0
        self.done = False
        # Les collections ne bougent pas pendant qu'on compose : les lire une fois
        # évite une requête par clic pour un résultat identique. L'archive des
        # saisons passées est écartée dès ici : elle ne s'échange pas.
        self.counts = {s: owned_counts(deal.member(s).id, cog.echangeable_id)
                       for s in ("a", "b")}
        # Sans ce compte, une collection pleine de S1 s'affiche à moitié vide sans
        # que rien ne dise pourquoi.
        self.archive = (len(database.get_user_collection(self.user_id))
                        - sum(self.counts[side].values()))
        # Brouillon : rien n'est visible par l'autre tant qu'on n'a pas validé.
        self.draft = {"mine": list(deal.give[side]),
                      "theirs": list(deal.give[deal.other(side)])}
        self._refresh_components()

    # ------------------------------------------------------------------ accès

    async def interaction_check(self, interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Ce n'est pas ta proposition.", ephemeral=True)
            return False
        if self.deal.closed:
            await interaction.response.send_message(
                "⌛ Cet échange est terminé ou annulé.", ephemeral=True)
            self.stop()
            return False
        if self.deal.editors.get(self.side) is not self:
            await interaction.response.send_message(
                "Ce menu a été remplacé par un plus récent : utilise celui-là.", ephemeral=True)
            self.stop()
            return False
        return True

    def _owner_side(self):
        """Le côté dont on pioche la collection : la sienne pour donner, celle de l'autre pour demander."""
        return self.side if self.target == "mine" else self.deal.other(self.side)

    def _partner(self):
        return self.deal.member(self.deal.other(self.side))

    def _changed(self):
        return (self.draft["mine"] != self.deal.give[self.side]
                or self.draft["theirs"] != self.deal.give[self.deal.other(self.side)])

    def _ready(self):
        return bool(self.draft["mine"]) and bool(self.draft["theirs"])

    # ------------------------------------------------------------- catalogue

    def _candidates(self):
        """[(carte, possédés, dispo, manque_à_l_autre)] triées par utilité décroissante."""
        owner = self._owner_side()
        pool = self.counts[owner]
        en_face = self.counts[self.deal.other(owner)]
        pris = Counter(self.draft[self.target])
        donne = self.target == "mine"

        out = []
        for key, owned in pool.items():
            card = self.cog.get_card(key)
            if not card:
                continue
            if self.query and self.query not in _fold(card["nom"]) \
                    and self.query not in _fold(card.get("club", "")):
                continue
            out.append((card, owned, owned - pris[key], en_face.get(key, 0) == 0))

        if donne:
            # Ce qu'on peut céder sans rien perdre d'abord, et parmi ces doublons,
            # ceux qui manquent à l'autre : ce sont eux qui font accepter un échange.
            out.sort(key=lambda t: (0 if t[1] > 1 else 1, 0 if t[3] else 1,
                                    -RARITY_RANK.get(t[0]["rarete"], 0), _fold(t[0]["nom"])))
        else:
            # On cherche ce qui manque à SA collection, et de préférence dans les
            # doublons de l'autre : c'est ce qui se cède le plus facilement.
            out.sort(key=lambda t: (0 if t[3] else 1, 0 if t[1] > 1 else 1,
                                    -RARITY_RANK.get(t[0]["rarete"], 0), _fold(t[0]["nom"])))
        return out

    def _describe(self, card, owned, dispo, manque_a_l_autre):
        """La ligne de description d'une carte, du point de vue de celui qui compose."""
        if self.target == "mine":
            bits = [card["rarete"], f"tu en as {owned}" if owned > 1 else "⚠️ ton seul exemplaire"]
            if manque_a_l_autre:
                bits.append("🆕 pas dans sa collection")
            if dispo <= 0:
                bits.append("déjà dans l'offre")
        else:
            bits = [card["rarete"], f"{owned} exemplaire" + ("s" if owned > 1 else "")]
            bits.append("🆕 tu ne l'as pas" if manque_a_l_autre else "tu l'as déjà")
            if dispo <= 0:
                bits.append("déjà demandée")
        return " · ".join(bits)[:100]

    # ------------------------------------------------------------ composants

    def _refresh_components(self):
        mine, theirs = self.draft["mine"], self.draft["theirs"]

        # 1) Le côté qu'on remplit. Dit en « donner / recevoir » : c'est ainsi
        #    qu'on pense un échange, pas en « mon offre / son offre ».
        self.side_select.options = [
            discord.SelectOption(
                label=f"📤 Ce que tu donnes ({len(mine)}/{MAX_PER_SIDE})", value="mine",
                description="Piocher dans TA collection", default=(self.target == "mine")),
            discord.SelectOption(
                label=f"📥 Ce que tu reçois ({len(theirs)}/{MAX_PER_SIDE})"[:100], value="theirs",
                description=f"Piocher dans la collection de {self._partner().display_name}"[:100],
                default=(self.target == "theirs")),
        ]

        # 2) Le catalogue, page par page.
        cands = self._candidates()
        pages = max(1, (len(cands) + PAGE_SIZE - 1) // PAGE_SIZE)
        self.page = max(0, min(self.page, pages - 1))
        tranche = cands[self.page * PAGE_SIZE:(self.page + 1) * PAGE_SIZE]
        self._pages = pages
        self._total = len(cands)

        opts = [
            discord.SelectOption(
                label=card["nom"][:100], value=str(card["id"]),
                description=self._describe(card, owned, dispo, neuf),
                emoji=RARITY_EMOJI.get(card["rarete"], "🔹"))
            for card, owned, dispo, neuf in tranche
        ]
        vide = "(aucun résultat)" if self.query else "(aucune carte)"
        self.add_select.options = opts or [discord.SelectOption(label=vide, value="__none__")]
        self.add_select.disabled = not opts
        self.add_select.placeholder = ("➕ Ajoute une carte à donner…" if self.target == "mine"
                                       else "➕ Demande une carte…")

        # 3) Retirer ce qu'on vient de mettre, sans tout recommencer.
        rem = []
        for i, key in enumerate(self.draft[self.target]):
            card = self.cog.get_card(key)
            rem.append(discord.SelectOption(
                label=(card["nom"] if card else f"#{key}")[:100], value=f"{i}:{key}",
                description="Retirer de l'échange",
                emoji=RARITY_EMOJI.get(card["rarete"], "🔹") if card else "🔹"))
        self.remove_select.options = rem or [discord.SelectOption(label="(vide)", value="__none__")]
        self.remove_select.disabled = not rem
        self.remove_select.placeholder = ("🗑️ Retirer une carte que tu donnes…" if self.target == "mine"
                                          else "🗑️ Retirer une carte que tu demandes…")

        # 4) Navigation et sortie.
        self.clear_btn.disabled = not self.query
        self.prev_btn.disabled = self.page <= 0
        self.next_btn.disabled = self.page >= pages - 1
        self.submit_btn.disabled = not self._ready()
        self.submit_btn.label = ("Envoyer la proposition" if not self.deal.published
                                 else "Valider mes changements")
        self.abort_btn.label = ("Abandonner" if not self.deal.published
                                else "Annuler mes changements")

    def _embed(self):
        autre = self._partner()
        mine, theirs = self.draft["mine"], self.draft["theirs"]
        moi, lui = self.side, self.deal.other(self.side)

        e = discord.Embed(title="🛠️ Ta proposition d'échange", color=discord.Color.gold())
        e.description = (
            f"Compose **les deux côtés** : ce que tu donnes **et** ce que tu veux "
            f"recevoir de {autre.display_name}.\n"
            + (f"{autre.display_name} n'aura plus qu'à accepter." if not self.deal.published
               else "Tes changements ne seront visibles qu'une fois validés.")
        )
        e.add_field(name=f"📤 Tu donnes ({len(mine)}/{MAX_PER_SIDE})",
                    value=_fmt_basket(self.cog, mine, self.counts[moi], self.counts[lui]),
                    inline=True)
        e.add_field(name=f"📥 Tu reçois ({len(theirs)}/{MAX_PER_SIDE})",
                    value=_fmt_basket(self.cog, theirs, self.counts[lui], self.counts[moi]),
                    inline=True)
        if not self._ready():
            e.add_field(
                name="⚠️ Il manque un côté",
                value="Un échange va dans les deux sens : chacun doit donner **au moins une carte**.",
                inline=False)

        pied = []
        if self.query:
            pied.append(f"🔎 « {self.query} » — {self._total} carte(s)")
        if self._pages > 1:
            pied.append(f"page {self.page + 1}/{self._pages}")
        if self.archive:
            pied.append(f"🗄️ {self.archive} carte(s) d'archive non échangeable(s)")
        pied.append("⚠️ dernier exemplaire · 🆕 absente de sa collection")
        e.set_footer(text=" · ".join(pied)[:2048])
        return e

    async def _apply(self, interaction):
        self._refresh_components()
        await interaction.response.edit_message(embed=self._embed(), view=self)

    # ---------------------------------------------------------- interactions

    @discord.ui.select(placeholder="Que veux-tu composer ?", row=0)
    async def side_select(self, interaction, select):
        self.target = select.values[0]
        self.page = 0
        await self._apply(interaction)

    @discord.ui.select(placeholder="➕ Ajoute une carte…", row=1)
    async def add_select(self, interaction, select):
        val = select.values[0]
        if val == "__none__":
            return await interaction.response.defer()
        key = val                       # clé de carte : str, jamais int (cf owned_counts)
        panier = self.draft[self.target]
        if len(panier) >= MAX_PER_SIDE:
            return await interaction.response.send_message(
                f"Maximum {MAX_PER_SIDE} cartes par côté.", ephemeral=True)
        owner = self._owner_side()
        if self.counts[owner].get(key, 0) - panier.count(key) <= 0:
            return await interaction.response.send_message(
                "Tous les exemplaires de cette carte sont déjà dans l'échange.", ephemeral=True)
        panier.append(key)
        await self._apply(interaction)

    @discord.ui.select(placeholder="🗑️ Retirer une carte…", row=2)
    async def remove_select(self, interaction, select):
        val = select.values[0]
        if val == "__none__":
            return await interaction.response.defer()
        # Les slugs de carte ne contiennent pas de « : », le découpage est sûr.
        idx, _, key = val.partition(":")
        panier, idx = self.draft[self.target], int(idx)
        # L'index vient de l'affichage précédent : on ne s'y fie que s'il désigne
        # toujours la bonne carte, sinon on retire la première occurrence.
        if 0 <= idx < len(panier) and panier[idx] == key:
            panier.pop(idx)
        elif key in panier:
            panier.remove(key)
        await self._apply(interaction)

    @discord.ui.button(label="Chercher", emoji="🔎", style=discord.ButtonStyle.grey, row=3)
    async def search_btn(self, interaction, button):
        await interaction.response.send_modal(SearchModal(self))

    @discord.ui.button(label="Tout afficher", emoji="🧹", style=discord.ButtonStyle.grey, row=3)
    async def clear_btn(self, interaction, button):
        self.query = ""
        self.page = 0
        await self._apply(interaction)

    @discord.ui.button(emoji="◀", style=discord.ButtonStyle.grey, row=3)
    async def prev_btn(self, interaction, button):
        self.page -= 1
        await self._apply(interaction)

    @discord.ui.button(emoji="▶", style=discord.ButtonStyle.grey, row=3)
    async def next_btn(self, interaction, button):
        self.page += 1
        await self._apply(interaction)

    @discord.ui.button(label="Envoyer la proposition", emoji="📨",
                       style=discord.ButtonStyle.green, row=4)
    async def submit_btn(self, interaction, button):
        if not self._ready():
            return await interaction.response.send_message(
                "Chacun doit donner au moins une carte (pas de cadeau).", ephemeral=True)
        if self.deal.published and not self._changed():
            return await self._close(interaction, "Aucun changement : la proposition est inchangée.")

        self.deal.give[self.side] = list(self.draft["mine"])
        self.deal.give[self.deal.other(self.side)] = list(self.draft["theirs"])
        self.deal.sign(self.side)
        self.done = True
        self.deal.editors.pop(self.side, None)

        if self.deal.published:
            await self._close(interaction, "✅ Proposition mise à jour.")
            await self.main_view.refresh()
            await self.main_view.notify_turn(self.side)
        else:
            await self._close(interaction, "📨 Proposition envoyée !")
            await self.cog.publish(interaction, self.deal)
        self.stop()

    @discord.ui.button(label="Abandonner", emoji="❌", style=discord.ButtonStyle.red, row=4)
    async def abort_btn(self, interaction, button):
        self.done = True
        self.deal.editors.pop(self.side, None)
        if not self.deal.published:
            # Rien n'est parti : l'échange n'a jamais existé pour l'autre joueur.
            self.deal.closed = True
            ACTIVE_TRADERS.discard(self.user_id)
            await self._close(interaction, "❌ Échange abandonné.")
        else:
            await self._close(interaction, "↩️ Changements annulés — la proposition reste telle quelle.")
            await self.main_view.refresh()
        self.stop()

    async def _close(self, interaction, texte):
        e = discord.Embed(description=texte, color=discord.Color.greyple())
        await interaction.response.edit_message(embed=e, view=None)

    async def on_timeout(self):
        if self.done:
            return
        if self.deal.editors.get(self.side) is self:
            self.deal.editors.pop(self.side, None)
        if not self.deal.published:
            self.deal.closed = True
            ACTIVE_TRADERS.discard(self.user_id)
        if self.message:
            e = discord.Embed(
                description="⌛ Composition expirée — relance `/echange` quand tu veux.",
                color=discord.Color.greyple())
            try:
                await self.message.edit(embed=e, view=None)
            except discord.HTTPException:
                pass
        if self.main_view:
            await self.main_view.refresh()


class TradeView(discord.ui.View):
    """La proposition, dans le salon : ce que chacun donne, et où en est l'accord.

    Un seul clic suffit à conclure. « Modifier » n'ouvre pas un nouvel échange
    mais le même composeur, prérempli et vu de l'autre côté : la contre-proposition
    est une réponse, pas un redémarrage.
    """

    def __init__(self, cog, deal):
        super().__init__(timeout=PROPOSAL_TIMEOUT)
        self.cog = cog
        self.deal = deal
        self.message = None

    async def interaction_check(self, interaction):
        if interaction.user.id not in (self.deal.a.id, self.deal.b.id):
            await interaction.response.send_message("Ce n'est pas ton échange.", ephemeral=True)
            return False
        if self.deal.closed:
            await interaction.response.send_message("⌛ Cet échange est terminé.", ephemeral=True)
            return False
        return True

    # ------------------------------------------------------------- affichage

    def _status(self):
        d = self.deal
        if d.editors:
            qui = ", ".join(d.member(s).display_name for s in d.editors)
            return f"🛠️ **{qui}** est en train de modifier la proposition…"
        if not d.ready():
            return "⚠️ Chacun doit donner au moins une carte — clique **Modifier**."
        attente = [s for s in ("a", "b") if not d.accepted[s]]
        if len(attente) == 1:
            qui = d.member(attente[0]).display_name
            return (f"⏳ **{qui}**, à toi : **Accepter** pour conclure, "
                    f"ou **Modifier** pour contre-proposer.")
        return "⏳ En attente des deux joueurs."

    def build_embed(self, counts=None):
        d = self.deal
        counts = counts or {s: owned_counts(d.member(s).id, self.cog.echangeable_id)
                            for s in ("a", "b")}
        e = discord.Embed(
            title="🤝 Proposition d'échange",
            description="Chacun reçoit ce que l'autre donne.\n" + self._status(),
            color=discord.Color.blurple())
        for side in ("a", "b"):
            autre = d.other(side)
            coche = "✅" if d.accepted[side] else ("🛠️" if side in d.editors else "⬜")
            e.add_field(
                name=f"{coche} {d.member(side).display_name} donne ({len(d.give[side])})"[:256],
                value=_fmt_basket(self.cog, d.give[side], counts[side], counts[autre]),
                inline=True)
        e.set_footer(text="⚠️ dernier exemplaire · 🆕 absente de sa collection · expire dans 15 min")
        return e

    def _content(self):
        """Le texte hors embed : il nomme celui qui doit jouer.

        Une mention affichée n'est pas une notification — Discord n'en envoie
        aucune sur une édition — mais elle met le bon nom en évidence dans le
        salon, ce qui suffit tant que les deux joueurs regardent l'échange."""
        d = self.deal
        attente = [s for s in ("a", "b") if not d.accepted[s]]
        if len(attente) == 1:
            cible, auteur = d.member(attente[0]), d.member(d.other(attente[0]))
            return f"{cible.mention} — **{auteur.display_name}** attend ta réponse."
        return f"{d.a.mention} {d.b.mention} — proposition à compléter."

    async def refresh(self):
        if self.message and not self.deal.closed:
            try:
                await self.message.edit(content=self._content(),
                                        embed=self.build_embed(), view=self,
                                        allowed_mentions=discord.AllowedMentions.none())
            except discord.HTTPException:
                pass

    async def notify_turn(self, auteur_side):
        """Prévient l'autre qu'une contre-proposition l'attend.

        Modifier une offre ne se voit pas : le message d'échange est édité, et
        Discord ne notifie jamais une édition. Sans ce mot, on reste devant une
        proposition déjà remplacée en croyant que l'autre n'a pas répondu."""
        if not self.message or self.deal.closed:
            return
        d = self.deal
        cible = d.member(d.other(auteur_side))
        try:
            await self.message.channel.send(
                f"{cible.mention} — **{d.member(auteur_side).display_name}** a modifié "
                f"la proposition d'échange 👆",
                reference=self.message, mention_author=False,
                allowed_mentions=discord.AllowedMentions(users=[cible], replied_user=False))
        except discord.HTTPException:
            pass

    def _cleanup(self):
        release(self.deal)

    # ---------------------------------------------------------- interactions

    @discord.ui.button(label="Accepter l'échange", emoji="✅", style=discord.ButtonStyle.green, row=0)
    async def accept_btn(self, interaction, button):
        d = self.deal
        side = d.side_of(interaction.user.id)
        if not d.ready():
            return await interaction.response.send_message(
                "Chacun doit donner au moins une carte (pas de cadeau).", ephemeral=True)
        if d.accepted[side]:
            return await interaction.response.send_message(
                f"Tu as déjà accepté — on attend {d.member(d.other(side)).display_name}.",
                ephemeral=True)
        d.accepted[side] = True
        if all(d.accepted.values()):
            await self._finalize(interaction)
        else:
            # Passe par refresh() : la ligne qui nomme le joueur attendu doit
            # suivre l'embed, sinon elle continue de désigner celui qui vient
            # de cliquer.
            await interaction.response.defer()
            await self.refresh()

    @discord.ui.button(label="Modifier", emoji="🛠️", style=discord.ButtonStyle.blurple, row=0)
    async def modify_btn(self, interaction, button):
        side = self.deal.side_of(interaction.user.id)
        picker = TradePicker(self.cog, self.deal, side, main_view=self)
        self.deal.editors[side] = picker
        await interaction.response.send_message(embed=picker._embed(), view=picker, ephemeral=True)
        picker.message = await interaction.original_response()
        await self.refresh()

    @discord.ui.button(label="Annuler", emoji="❌", style=discord.ButtonStyle.red, row=0)
    async def cancel_btn(self, interaction, button):
        qui = interaction.user.display_name
        e = self.build_embed()
        self._cleanup()
        e.title = f"❌ Échange annulé par {qui}"
        e.description = "Aucune carte n'a changé de main."
        e.color = discord.Color.red()
        await interaction.response.edit_message(content=None, embed=e, view=None)
        self.stop()

    async def _finalize(self, interaction):
        d = self.deal
        # Les marqueurs ⚠️/🆕 décrivent la situation AVANT l'échange : les figer
        # évite un récapitulatif qui parlerait déjà des collections d'après.
        avant = {s: owned_counts(d.member(s).id, self.cog.echangeable_id)
                 for s in ("a", "b")}
        rows_a = self.cog.resolve_rowids(d.a.id, d.give["a"])
        rows_b = self.cog.resolve_rowids(d.b.id, d.give["b"])
        manquant = d.a if rows_a is None else (d.b if rows_b is None else None)
        ok = manquant is None and database.execute_trade(d.a.id, rows_a, d.b.id, rows_b)

        self._cleanup()
        e = self.build_embed(counts=avant)
        if ok:
            database.log_trade(d.a.id, d.give["a"], d.b.id, d.give["b"])
            e.title = "✅ Échange effectué !"
            e.description = (f"**{d.a.display_name}** reçoit {len(d.give['b'])} carte(s), "
                             f"**{d.b.display_name}** en reçoit {len(d.give['a'])}.")
            e.color = discord.Color.green()
        else:
            e.title = "❌ Échange impossible"
            e.description = (
                f"**{manquant.display_name}** ne possède plus toutes les cartes promises."
                if manquant else
                "Une des cartes a changé de propriétaire entre-temps. Rien n'a bougé.")
            e.color = discord.Color.red()
        await interaction.response.edit_message(content=None, embed=e, view=None)
        self.stop()

    async def on_timeout(self):
        if self.deal.closed:
            return
        e = self.build_embed()
        self._cleanup()
        e.title = "⌛ Proposition expirée"
        e.description = "Personne n'a répondu à temps. Relance `/echange` pour réessayer."
        e.color = discord.Color.greyple()
        if self.message:
            try:
                await self.message.edit(content=None, embed=e, view=None)
            except discord.HTTPException:
                pass


class TradeCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.all_cards = load_cards_data()
        # Saison EFFECTIVE, pas la constante : si les cartes de la saison n'ont pas
        # encore été publiées, on suit celle qui l'est (cf saison_en_cours).
        self.saison = saison_en_cours(self.all_cards)
        self.card_map = {}
        for c in self.all_cards:
            self.card_map[c["id"]] = c
            self.card_map[str(c["id"])] = c

    def echangeable(self, card):
        """Une carte s'échange si elle est de la saison EN COURS.

        Les saisons passées sont une archive figée : `tools/migration_s2.py` n'en
        a laissé qu'UN exemplaire par carte, et elles ne sont plus ni tirables en
        pack ni créables au craft. L'échange y serait interdit dans les deux sens
        — céder la sienne est définitif, en recevoir une seconde casse l'exemplaire
        unique qui fait l'archive. Contrairement au duel, aucune exclusion des
        cartes Noël : elles sont toutes S1 aujourd'hui, donc déjà écartées, et si
        une promo revient en saison courante elle sera doublonnable comme le reste."""
        return bool(card) and saison_de(card) == self.saison

    def echangeable_id(self, cid):
        """La même règle, mais depuis un `card_id` : c'est la garde d'`owned_counts`."""
        return self.echangeable(self.get_card(cid))

    def tradables(self, user_id):
        """Les exemplaires échangeables du joueur : [(rowid, card_id), ...]."""
        return [(r, cid) for r, cid in database.get_user_cards_with_rowid(user_id)
                if self.echangeable(self.get_card(cid))]

    def get_card(self, cid):
        return self.card_map.get(cid) or self.card_map.get(str(cid))

    def resolve_rowids(self, user_id, card_keys):
        """Traduit une liste de cartes en exemplaires précis, ou None s'il en manque.

        Résolue au dernier moment, juste avant l'échange : une proposition peut
        rester dix minutes à l'écran, et l'exemplaire visé partir ailleurs
        entre-temps. On prend n'importe quel exemplaire de la bonne carte — ils sont
        interchangeables — plutôt que celui repéré à la composition.

        Le filet de `tradables` compte : un panier composé juste avant une bascule
        de saison ne doit pas pouvoir déplacer une carte devenue archive."""
        pool = {}
        for rowid, cid in self.tradables(user_id):
            pool.setdefault(str(cid), []).append(rowid)
        out = []
        for key in card_keys:
            dispo = pool.get(key)
            if not dispo:
                return None
            out.append(dispo.pop())
        return out

    async def publish(self, interaction, deal):
        """Poste la proposition dans le salon et verrouille le destinataire."""
        if deal.b.id in ACTIVE_TRADERS:
            # Le destinataire s'est engagé ailleurs pendant la composition :
            # ce verrou appartient à l'autre échange, on ne relâche que le nôtre.
            deal.closed = True
            deal.editors.clear()
            ACTIVE_TRADERS.discard(deal.a.id)
            return await interaction.followup.send(
                f"{deal.b.display_name} vient d'entrer dans un autre échange. "
                "Ta proposition n'a pas été envoyée.", ephemeral=True)

        channel = interaction.channel or self.bot.get_channel(interaction.channel_id)
        if channel is None:
            release(deal)
            return await interaction.followup.send(
                "Impossible de poster la proposition dans ce salon.", ephemeral=True)

        ACTIVE_TRADERS.add(deal.b.id)
        deal.published = True
        view = TradeView(self, deal)
        try:
            view.message = await channel.send(
                content=f"{deal.b.mention} — **{deal.a.display_name}** te propose un échange !",
                embed=view.build_embed(), view=view,
                allowed_mentions=discord.AllowedMentions(users=[deal.b]))
        except discord.HTTPException:
            # Salon interdit au bot : sans message public, personne ne peut
            # répondre — on relâche les deux verrous plutôt que de les figer.
            view.stop()
            release(deal)
            await interaction.followup.send(
                "Je n'ai pas pu poster la proposition dans ce salon.", ephemeral=True)

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
        moi, lui = interaction.user, membre
        if lui.bot:
            return await interaction.response.send_message(
                "Tu ne peux pas échanger avec un bot.", ephemeral=True)
        if moi.id == lui.id:
            return await interaction.response.send_message(
                "Tu ne peux pas échanger avec toi-même.", ephemeral=True)
        if moi.id in ACTIVE_TRADERS:
            return await interaction.response.send_message(
                "Tu as déjà un échange en cours : termine-le d'abord.", ephemeral=True)
        if lui.id in ACTIVE_TRADERS:
            return await interaction.response.send_message(
                f"{lui.display_name} a déjà un échange en cours.", ephemeral=True)
        # Mieux vaut le dire tout de suite qu'après avoir composé une offre — et
        # dire POURQUOI : une collection pleine de cartes d'archive n'est pas une
        # collection vide, et le message « ouvre un /pack » y serait incompréhensible.
        for membre_, sujet in ((moi, "Tu n'as"), (lui, f"{lui.display_name} n'a")):
            if self.tradables(membre_.id):
                continue
            archive = bool(database.get_user_collection(membre_.id))
            return await interaction.response.send_message(
                (f"🗄️ {sujet} que des cartes des **saisons passées** : l'archive ne "
                 f"s'échange pas. Il faut des cartes de la saison en cours.")
                if archive else
                (f"{sujet} aucune carte à échanger"
                 + (" — ouvre d'abord un `/pack`." if membre_ is moi else ".")),
                ephemeral=True)

        ACTIVE_TRADERS.add(moi.id)
        deal = Deal(moi, lui)
        picker = TradePicker(self, deal, "a")
        deal.editors["a"] = picker
        await interaction.response.send_message(embed=picker._embed(), view=picker, ephemeral=True)
        picker.message = await interaction.original_response()


async def setup(bot):
    await bot.add_cog(TradeCog(bot))
