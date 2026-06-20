"""
Gating des fonctionnalités « Saison 2 » (échanges, duels).

Tant qu'on est AVANT la date d'ouverture publique :
  - seuls les testeurs (BETA_TESTER_IDS) peuvent utiliser les commandes,
  - et uniquement dans le salon de test (BETA_CHANNEL_ID).
À partir de PUBLIC_LAUNCH, tout s'ouvre automatiquement à la communauté,
sans aucune intervention.

Les IDs et la date sont surchargeables via le .env :
  PUBLIC_LAUNCH=2026-08-01
  BETA_TESTER_IDS=133711821214449665,autre_id
  BETA_CHANNEL_ID=441230079100715008
"""
import os
from datetime import datetime

import pytz
from discord import app_commands

PARIS = pytz.timezone("Europe/Paris")


def _parse_launch(raw):
    if raw:
        try:
            return PARIS.localize(datetime.strptime(raw.strip(), "%Y-%m-%d"))
        except ValueError:
            pass
    return PARIS.localize(datetime(2026, 8, 1, 0, 0, 0))


def _parse_ids(raw, default):
    if not raw:
        return set(default)
    out = set()
    for part in raw.replace(";", ",").split(","):
        part = part.strip()
        if part.isdigit():
            out.add(int(part))
    return out or set(default)


# Date d'ouverture publique : bascule automatique des nouveautés pour tous.
PUBLIC_LAUNCH = _parse_launch(os.getenv("PUBLIC_LAUNCH"))

# Testeurs autorisés pendant la bêta (défaut : l'admin du serveur).
BETA_TESTER_IDS = _parse_ids(os.getenv("BETA_TESTER_IDS"), {133711821214449665})

# Salon de test privé (0 = pas de restriction de salon).
BETA_CHANNEL_ID = int(os.getenv("BETA_CHANNEL_ID", "441230079100715008") or 0)


def is_live(now=None):
    """True si la date d'ouverture publique est atteinte."""
    now = now or datetime.now(PARIS)
    return now >= PUBLIC_LAUNCH


def beta_access(interaction):
    """Version non-levante de la garde : True si l'utilisateur a accès aux
    fonctionnalités Saison 2 (public après la sortie, sinon testeur dans le salon)."""
    if is_live():
        return True
    if interaction.user.id not in BETA_TESTER_IDS:
        return False
    if BETA_CHANNEL_ID and interaction.channel_id != BETA_CHANNEL_ID:
        return False
    return True


class BetaLocked(app_commands.CheckFailure):
    """Levée quand une commande bêta est utilisée hors du contexte autorisé.
    Le message est destiné à être affiché à l'utilisateur."""

    def __init__(self, message):
        super().__init__(message)
        self.user_message = message


def beta_guard():
    """Décorateur de check pour les commandes en bêta privée."""
    async def predicate(interaction):
        if is_live():
            return True  # ouvert à toute la communauté après la sortie
        if interaction.user.id not in BETA_TESTER_IDS:
            raise BetaLocked(
                "🔒 Cette fonctionnalité arrive à la **saison prochaine** ! "
                "Elle est en test privé pour le moment."
            )
        if BETA_CHANNEL_ID and interaction.channel_id != BETA_CHANNEL_ID:
            raise BetaLocked(
                "🔒 Les fonctionnalités en test ne sont jouables que dans le **salon de test**."
            )
        return True

    return app_commands.check(predicate)
