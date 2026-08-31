"""Extraction des matchs livescore.in.

Le site a refondu son DOM (design interne « wcl ») : la classe `event__time`,
qui portait la date/heure de chaque match, s'appelle désormais
`event__stageTime`. Les conteneurs `event__match--scheduled` et les
participants n'ont pas bougé, d'où un scraping qui remontait bien 13 lignes
mais 0 match exploitable.

Tout ce qui dépend de la structure du site est regroupé ici : les sélecteurs
(avec les anciens noms gardés en secours), le parsing de l'horaire et la
détection du fuseau. Ce module ne dépend ni de discord ni de la base, il est
donc testable seul.
"""

import re
from datetime import date, datetime, timedelta, timezone

import pytz

# --- Sélecteurs, du plus spécifique au plus ancien ---------------------------
# Un site qui change ses classes ne doit casser qu'une ligne de cette liste.
ROW_SELECTORS = ("div.event__match--scheduled", "div.event__match")
TIME_SELECTORS = (
    '[data-testid="wcl-stageTime"]',
    ".event__stageTime",
    ".event__time",  # DOM d'avant la refonte
)
HOME_SELECTORS = (".event__homeParticipant", ".event__participant--home")
AWAY_SELECTORS = (".event__awayParticipant", ".event__participant--away")

# « 04.09. 20:00 », « 04.09.2026 20:00 », « 04.09 20:00 »
DATE_TIME_RE = re.compile(r"(\d{1,2})\.(\d{1,2})\.?(\d{4})?\D+?(\d{1,2}):(\d{2})")
# « 20:00 » seul (match du jour sur certaines vues)
TIME_ONLY_RE = re.compile(r"^(\d{1,2}):(\d{2})$")

EVENT_ID_PREFIX_RE = re.compile(r"^g_\d+_")

# Injecté par Browserless (`addScriptTag`) : livescore.in rend les horaires dans
# le fuseau du navigateur, qu'on ne contrôle pas depuis l'API REST. On fait donc
# écrire au navigateur son propre fuseau, qu'on relit dans le HTML rendu.
TZ_PROBE_SCRIPT = (
    "try{var d=document.documentElement;"
    "d.setAttribute('data-hn-tz',Intl.DateTimeFormat().resolvedOptions().timeZone||'');"
    "d.setAttribute('data-hn-offset',String(new Date().getTimezoneOffset()));}"
    "catch(e){}"
)


def _select_first(container, selectors):
    for selector in selectors:
        found = container.select_one(selector)
        if found is not None:
            return found
    return None


def localize(naive_dt, tz):
    """pytz veut `localize()`, les fuseaux fixes veulent `replace()`."""
    if hasattr(tz, "localize"):
        return tz.localize(naive_dt)
    return naive_dt.replace(tzinfo=tz)


def detect_source_timezone(soup):
    """Fuseau dans lequel le navigateur de scraping a rendu les horaires.

    Retourne (tzinfo, libellé) ou (None, None) si la sonde n'a pas tourné.
    """
    root = soup.find("html")
    if root is None:
        return None, None

    name = (root.get("data-hn-tz") or "").strip()
    if name:
        try:
            return pytz.timezone(name), name
        except Exception:
            pass

    raw_offset = (root.get("data-hn-offset") or "").strip()
    if raw_offset:
        try:
            # getTimezoneOffset() est l'inverse de l'offset UTC.
            minutes = -int(raw_offset)
            return timezone(timedelta(minutes=minutes)), f"UTC{minutes // 60:+03d}:{abs(minutes) % 60:02d}"
        except Exception:
            pass

    return None, None


def parse_naive_datetime(time_text, today):
    """« 04.09. 20:00 » -> datetime naïf, dans le fuseau d'affichage du site.

    `today` sert à déduire l'année, absente des pages de calendrier.
    """
    text = (time_text or "").strip()
    if not text:
        return None

    time_only = TIME_ONLY_RE.match(text)
    if time_only:
        hour, minute = int(time_only.group(1)), int(time_only.group(2))
        if hour > 23 or minute > 59:
            return None
        return datetime(today.year, today.month, today.day, hour, minute)

    found = DATE_TIME_RE.search(text)
    if not found:
        return None

    day, month, year, hour, minute = (
        int(found.group(1)),
        int(found.group(2)),
        found.group(3),
        int(found.group(4)),
        int(found.group(5)),
    )
    if hour > 23 or minute > 59:
        return None

    if year:
        year = int(year)
    else:
        year = today.year

    explicit_year = bool(found.group(3))

    def build(candidate_year):
        try:
            return date(candidate_year, month, day)
        except ValueError:
            return None  # 31.02, ou 29.02 sur une année non bissextile

    match_date = build(year)
    # Les pages « fixtures » ne listent que du futur : une date absente du
    # calendrier de l'année en cours (29.02) ou déjà passée signifie qu'on a
    # franchi le 31 décembre.
    if not explicit_year and (match_date is None or match_date < today):
        match_date = build(year + 1) or match_date
    if match_date is None:
        return None

    return datetime(match_date.year, match_date.month, match_date.day, hour, minute)


def find_match_rows(soup):
    """Lignes de match de la page, en tolérant que le modificateur change."""
    for selector in ROW_SELECTORS:
        rows = soup.select(selector)
        if rows:
            return rows, selector
    return [], None


def extract_event_id(container):
    return EVENT_ID_PREFIX_RE.sub("", container.get("id", "") or "").strip()


def parse_match_row(container, today):
    """-> dict des données brutes d'une ligne, ou dict d'erreur si illisible.

    On ne convertit pas en UTC ici : l'appelant applique le fuseau détecté.
    """
    time_elem = _select_first(container, TIME_SELECTORS)
    if time_elem is None:
        return {"error": "horaire introuvable"}

    raw_time = time_elem.get_text(strip=True)
    naive_dt = parse_naive_datetime(raw_time, today)
    if naive_dt is None:
        return {"error": f"horaire illisible : '{raw_time}'"}

    home = _select_first(container, HOME_SELECTORS)
    away = _select_first(container, AWAY_SELECTORS)
    if home is None or away is None:
        return {"error": "équipes introuvables"}

    team1 = home.get_text(strip=True)
    team2 = away.get_text(strip=True)
    event_id = extract_event_id(container)
    if not team1 or not team2 or not event_id:
        return {"error": f"données incomplètes (team1={team1!r}, team2={team2!r}, id={event_id!r})"}

    return {
        "team1": team1,
        "team2": team2,
        "event_id": event_id,
        "naive_dt": naive_dt,
        "raw_time": raw_time,
    }


def describe_row(container, limit=600):
    """Empreinte d'une ligne, à logguer quand plus rien ne matche."""
    classes = []
    for node in container.find_all(True, class_=True, limit=12):
        classes.append(f"{node.name}.{'.'.join(node.get('class', []))}")
    return f"classes={classes} | html={str(container)[:limit]}"
