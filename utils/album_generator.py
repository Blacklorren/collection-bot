import asyncio
import io
import math
from PIL import Image, ImageDraw, ImageFont, ImageOps
import aiohttp

# Dimensions standard pour la grille
CARD_WIDTH = 250
CARD_HEIGHT = 370
GRID_COLUMNS = 4
PADDING = 20
BG_COLOR = (44, 47, 51)  # Discord Dark Mode Grey
TEXT_COLOR = (255, 255, 255)
PLACEHOLDER_COLOR = (60, 60, 60)

async def fetch_image(session, url):
    """Télécharge une image depuis une URL."""
    try:
        async with session.get(url) as response:
            if response.status == 200:
                data = await response.read()
                return Image.open(io.BytesIO(data))
            else:
                print(f"DEBUG: Failed to fetch {url}, status: {response.status}")
    except Exception as e:
        print(f"Erreur téléchargement image {url}: {e}")
    return None

def create_placeholder(card_name, rarity):
    """Crée une carte 'cachée' visuellement."""
    img = Image.new('RGB', (CARD_WIDTH, CARD_HEIGHT), color=PLACEHOLDER_COLOR)
    draw = ImageDraw.Draw(img)
    
    # Tentative de chargement de font, sinon défaut
    try:
        # Essayer d'utiliser une font BOLD système si possible
        # Windows/Linux ont souvent arialbd.ttf
        font_large = ImageFont.truetype("arialbd.ttf", 46)
        font_small = ImageFont.truetype("arialbd.ttf", 34)
        font_rarity = ImageFont.truetype("arialbd.ttf", 28)
    except IOError:
        try:
            # Fallback sur Arial normal
            font_large = ImageFont.truetype("arial.ttf", 46) 
            font_small = ImageFont.truetype("arial.ttf", 34)
            font_rarity = ImageFont.truetype("arial.ttf", 28)
        except IOError:
            font_large = ImageFont.load_default()
            font_small = ImageFont.load_default()
            font_rarity = ImageFont.load_default()

    # Dessin du cadre
    draw.rectangle([0, 0, CARD_WIDTH-1, CARD_HEIGHT-1], outline=(100, 100, 100), width=2)
    
    # Texte centré (Nom)
    text_bbox = draw.textbbox((0, 0), "MANQUANTE", font=font_large)
    text_w = text_bbox[2] - text_bbox[0]
    draw.text(((CARD_WIDTH - text_w) / 2, CARD_HEIGHT / 2 - 70), "MANQUANTE", fill=(255, 80, 80), font=font_large)

    # Nom de la carte
    # On coupe si trop long
    name_to_draw = card_name if len(card_name) < 20 else card_name[:17] + "..."
    text_bbox = draw.textbbox((0, 0), name_to_draw, font=font_small)
    text_w = text_bbox[2] - text_bbox[0]
    draw.text(((CARD_WIDTH - text_w) / 2, CARD_HEIGHT / 2), name_to_draw, fill=(255, 255, 255), font=font_small)
    
    # Rareté
    text_bbox = draw.textbbox((0, 0), rarity, font=font_rarity)
    text_w = text_bbox[2] - text_bbox[0]
    draw.text(((CARD_WIDTH - text_w) / 2, CARD_HEIGHT / 2 + 50), rarity, fill=(200, 200, 200), font=font_rarity)

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
    img_height = (rows * CARD_HEIGHT) + ((rows + 1) * PADDING) + 60 # +60 pour le titre
    
    final_img = Image.new('RGB', (img_width, img_height), color=BG_COLOR)
    draw = ImageDraw.Draw(final_img)
    
    # 2. Titre du Club
    try:
        title_font = ImageFont.truetype("arialbd.ttf", 60)
    except:
        title_font = ImageFont.load_default()
        
    draw.text((PADDING, PADDING), f"Album : {club_name}", fill=TEXT_COLOR, font=title_font)
    
    # 3. Traitement des cartes
    async with aiohttp.ClientSession() as session:
        tasks = []
        
        # On prépare les tâches pour télécharger les images possédées
        for i, card in enumerate(all_cards_in_club):
            card_id = card['id']
            # Support int/str
            is_owned = (card_id in owned_card_ids) or (str(card_id) in [str(x) for x in owned_card_ids])
            
            # DEBUG
            if is_owned:
                print(f"DEBUG: Found owned card {card['nom']} (ID: {card_id}). Fetching image...")
            
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
            y = 60 + PADDING + (row * (CARD_HEIGHT + PADDING)) # Offset titre
            
            card_img = results[i]
            
            if card_img:
                # C'est une carte possédée et téléchargée
                # Redimensionnement propre
                card_img = card_img.resize((CARD_WIDTH, CARD_HEIGHT), Image.Resampling.LANCZOS)
                final_img.paste(card_img, (x, y))
            elif isinstance(results[i], Image.Image):
                # Cas peu probable où fetch renverrait une image directement sans redim, safety check
                final_img.paste(results[i], (x, y))
            else:
                # C'est une carte manquante ou erreur de download -> Placeholder
                placeholder = create_placeholder(card['nom'], card['rarete'])
                final_img.paste(placeholder, (x, y))
                
    # 5. Export
    output_buffer = io.BytesIO()
    final_img.save(output_buffer, format='PNG')
    output_buffer.seek(0)
    return output_buffer
