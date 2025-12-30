import asyncio
import io
import math
import os
from PIL import Image, ImageDraw, ImageFont, ImageOps
import aiohttp

# Dimensions standard pour la grille
# Aspect ratio 992x1200 conservé
# On part sur une base large pour la qualité
CARD_WIDTH = 496  # 992 / 2
CARD_HEIGHT = 600 # 1200 / 2
GRID_COLUMNS = 4
PADDING = 20
BG_COLOR = (44, 47, 51)  # Discord Dark Mode Grey
TEXT_COLOR = (255, 255, 255)
PLACEHOLDER_COLOR = (60, 60, 60)

# Chemins possibles pour les polices (Linux Railway / Windows local)
FONT_PATHS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",  # Linux (Railway)
    "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",   # Linux alternative
    "C:/Windows/Fonts/arialbd.ttf",                           # Windows
    "C:/Windows/Fonts/arial.ttf",                             # Windows fallback
]

def get_font(size):
    """Retourne une police à la taille demandée, avec fallback automatique."""
    for path in FONT_PATHS:
        try:
            return ImageFont.truetype(path, size)
        except (IOError, OSError):
            continue
    # Dernier recours : police par défaut (sera petite)
    return ImageFont.load_default()

async def fetch_image(session, url):
    """Télécharge une image depuis une URL."""
    try:
        async with session.get(url) as response:
            if response.status == 200:
                data = await response.read()
                return Image.open(io.BytesIO(data))
            else:
                pass  # Image non téléchargée
    except Exception as e:
        pass  # Erreur silencieuse
    return None

def create_placeholder(card_name, rarity):
    """Crée une carte 'cachée' visuellement."""
    # WORKAROUND: La police par défaut de Pillow est minuscule et non-redimensionnable.
    # On dessine sur une petite image puis on l'agrandit.
    SCALE_FACTOR = 4
    small_w = CARD_WIDTH // SCALE_FACTOR
    small_h = CARD_HEIGHT // SCALE_FACTOR
    
    img = Image.new('RGB', (small_w, small_h), color=PLACEHOLDER_COLOR)
    draw = ImageDraw.Draw(img)
    
    # Charger la police (sera petite mais on upscale après)
    font = get_font(16)  # Taille cible après upscale: 16*4 = 64px
    small_font = get_font(12)  # 12*4 = 48px
    tiny_font = get_font(10)  # 10*4 = 40px

    # Dessin du cadre
    draw.rectangle([0, 0, small_w-1, small_h-1], outline=(100, 100, 100), width=1)
    
    # Texte "MANQUANTE"
    text = "MANQUANTE"
    text_bbox = draw.textbbox((0, 0), text, font=font)
    text_w = text_bbox[2] - text_bbox[0]
    draw.text(((small_w - text_w) / 2, small_h / 2 - 30), text, fill=(255, 80, 80), font=font)

    # Nom de la carte (sur 2 lignes si trop long)
    max_chars_per_line = 18
    if len(card_name) <= max_chars_per_line:
        # Tient sur une ligne
        text_bbox = draw.textbbox((0, 0), card_name, font=small_font)
        text_w = text_bbox[2] - text_bbox[0]
        draw.text(((small_w - text_w) / 2, small_h / 2), card_name, fill=(255, 255, 255), font=small_font)
    else:
        # Diviser sur 2 lignes (au milieu approximativement)
        words = card_name.split()
        mid = len(words) // 2
        line1 = " ".join(words[:mid]) if mid > 0 else words[0]
        line2 = " ".join(words[mid:]) if mid > 0 else ""
        
        # Ligne 1
        text_bbox = draw.textbbox((0, 0), line1, font=small_font)
        text_w = text_bbox[2] - text_bbox[0]
        draw.text(((small_w - text_w) / 2, small_h / 2 - 8), line1, fill=(255, 255, 255), font=small_font)
        
        # Ligne 2
        if line2:
            text_bbox = draw.textbbox((0, 0), line2, font=small_font)
            text_w = text_bbox[2] - text_bbox[0]
            draw.text(((small_w - text_w) / 2, small_h / 2 + 8), line2, fill=(255, 255, 255), font=small_font)
    
    # Rareté
    text_bbox = draw.textbbox((0, 0), rarity, font=tiny_font)
    text_w = text_bbox[2] - text_bbox[0]
    draw.text(((small_w - text_w) / 2, small_h / 2 + 25), rarity, fill=(200, 200, 200), font=tiny_font)

    # Agrandissement final avec antialiasing
    img = img.resize((CARD_WIDTH, CARD_HEIGHT), Image.Resampling.LANCZOS)
    return img

async def generate_club_album(club_name, all_cards_in_club, owned_card_ids):
    """
    Génère une image composite pour l'album d'un club.
    
    :param club_name: Nom du club (str)
    :param all_cards_in_club: Liste de dicts des cartes du club
    :param owned_card_ids: Set ou liste des ID possédés par l'user
    :return: io.BytesIO de l'image finale
    """
    # 1. Calcul des dimensions de l'image finale
    total_cards = len(all_cards_in_club)
    rows = math.ceil(total_cards / GRID_COLUMNS)
    
    img_width = (GRID_COLUMNS * CARD_WIDTH) + ((GRID_COLUMNS + 1) * PADDING)
    img_height = (rows * CARD_HEIGHT) + ((rows + 1) * PADDING) + 120 # +120 pour le titre
    
    final_img = Image.new('RGB', (img_width, img_height), color=BG_COLOR)
    draw = ImageDraw.Draw(final_img)
    
    # 2. Titre du Club
    title_font = get_font(100)
    draw.text((PADDING, PADDING), f"Album : {club_name}", fill=TEXT_COLOR, font=title_font)
    
    # 3. Traitement des cartes
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    }
    async with aiohttp.ClientSession(headers=headers) as session:
        tasks = []
        
        # On prépare les tâches pour télécharger les images possédées
        for i, card in enumerate(all_cards_in_club):
            card_id = card['id']
            # Support int/str
            is_owned = (card_id in owned_card_ids) or (str(card_id) in [str(x) for x in owned_card_ids])
            
            if is_owned:
                tasks.append(fetch_image(session, card['image_url']))
            else:
                # Pas de tâche réseau pour les placeholders
                tasks.append(asyncio.sleep(0, result=None)) 
        
        # Exécution des téléchargements
        results = await asyncio.gather(*tasks)
        
        # 4. Collage sur le canvas
        for i, card in enumerate(all_cards_in_club):
            col = i % GRID_COLUMNS
            row = i // GRID_COLUMNS
            
            x = PADDING + (col * (CARD_WIDTH + PADDING))
            y = 140 + PADDING + (row * (CARD_HEIGHT + PADDING)) # Offset titre
            
            card_img = results[i]
            
            if card_img:
                # C'est une carte possédée et téléchargée
                # Redimensionnement propre
                card_img = card_img.resize((CARD_WIDTH, CARD_HEIGHT), Image.Resampling.LANCZOS)
                final_img.paste(card_img, (x, y))
            else:
                # C'est une carte manquante ou erreur de download -> Placeholder
                placeholder = create_placeholder(card['nom'], card['rarete'])
                final_img.paste(placeholder, (x, y))
                
    # 5. Export
    output_buffer = io.BytesIO()
    final_img.save(output_buffer, format='PNG')
    output_buffer.seek(0)
    return output_buffer
