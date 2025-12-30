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

# Fonction utilitaire pour télécharger les polices si absentes
async def ensure_fonts():
    base_path = os.path.dirname(os.path.abspath(__file__))
    font_files = {
        "Roboto-Bold.ttf": "https://github.com/google/fonts/raw/main/apache/roboto/static/Roboto-Bold.ttf",
        "Roboto-Regular.ttf": "https://github.com/google/fonts/raw/main/apache/roboto/static/Roboto-Regular.ttf"
    }
    
    headers = {"User-Agent": "Mozilla/5.0"}
    
    async with aiohttp.ClientSession(headers=headers) as session:
        for filename, url in font_files.items():
            filepath = os.path.join(base_path, filename)
            if not os.path.exists(filepath):
                print(f"DEBUG: Downloading font {filename}...")
                try:
                    async with session.get(url) as response:
                        if response.status == 200:
                            content = await response.read()
                            with open(filepath, 'wb') as f:
                                f.write(content)
                            print(f"DEBUG: Successfully downloaded {filename}")
                        else:
                            print(f"DEBUG: Failed to download {filename} (status {response.status})")
                except Exception as e:
                    print(f"DEBUG: Error downloading {filename}: {e}")

def create_placeholder(card_name, rarity):
    """Crée une carte 'cachée' visuellement."""
    img = Image.new('RGB', (CARD_WIDTH, CARD_HEIGHT), color=PLACEHOLDER_COLOR)
    draw = ImageDraw.Draw(img)
    # Chargement des polices incluses dans le projet (compatible Railway/Linux/Windows)
    # Les fichiers doivent être dans le même dossier 'utils' ou à la racine
    # Ici on suppose qu'ils sont dans utils/
    base_path = os.path.dirname(os.path.abspath(__file__))
    font_bold_path = os.path.join(base_path, "Roboto-Bold.ttf")
    font_reg_path = os.path.join(base_path, "Roboto-Regular.ttf")

    try:
        font_large = ImageFont.truetype(font_bold_path, 92)
        font_small = ImageFont.truetype(font_bold_path, 68)
        font_rarity = ImageFont.truetype(font_bold_path, 46)
        print(f"DEBUG: SUCCESS loading local font from {font_bold_path}")
    except IOError:
        print(f"DEBUG: FAILURE loading local font {font_bold_path}. Fallback to default.")
        font_large = ImageFont.load_default()
        font_small = ImageFont.load_default()
        font_rarity = ImageFont.load_default()

    # Dessin du cadre
    draw.rectangle([0, 0, CARD_WIDTH-1, CARD_HEIGHT-1], outline=(100, 100, 100), width=2)
    
    # Texte centré (Nom)
    text_bbox = draw.textbbox((0, 0), "MANQUANTE", font=font_large)
    text_w = text_bbox[2] - text_bbox[0]
    # Texte centré (Nom)
    text_bbox = draw.textbbox((0, 0), "MANQUANTE", font=font_large)
    text_w = text_bbox[2] - text_bbox[0]
    # On ajuste le décalage vertical proportionnellement à la nouvelle hauteur
    draw.text(((CARD_WIDTH - text_w) / 2, CARD_HEIGHT / 2 - 120), "MANQUANTE", fill=(255, 80, 80), font=font_large)

    # Nom de la carte
    # On coupe si trop long
    name_to_draw = card_name if len(card_name) < 20 else card_name[:17] + "..."
    text_bbox = draw.textbbox((0, 0), name_to_draw, font=font_small)
    text_w = text_bbox[2] - text_bbox[0]
    draw.text(((CARD_WIDTH - text_w) / 2, CARD_HEIGHT / 2), name_to_draw, fill=(255, 255, 255), font=font_small)
    
    # Rareté
    text_bbox = draw.textbbox((0, 0), rarity, font=font_rarity)
    text_w = text_bbox[2] - text_bbox[0]
    # Rareté
    text_bbox = draw.textbbox((0, 0), rarity, font=font_rarity)
    text_w = text_bbox[2] - text_bbox[0]
    # Plus bas
    draw.text(((CARD_WIDTH - text_w) / 2, CARD_HEIGHT / 2 + 100), rarity, fill=(200, 200, 200), font=font_rarity)

    return img

async def generate_club_album(club_name, all_cards_in_club, owned_card_ids):
    """
    Génère une image composite pour l'album d'un club.
    
    :param club_name: Nom du club (str)
    :param all_cards_in_club: Liste de dicts des cartes du club
    :param owned_card_ids: Set ou liste des ID possédés par l'user
    :return: io.BytesIO de l'image finale
    """
    # Téléchargement des polices si nécessaire
    await ensure_fonts()

    # 1. Calcul des dimensions de l'image finale
    total_cards = len(all_cards_in_club)
    rows = math.ceil(total_cards / GRID_COLUMNS)
    
    img_width = (GRID_COLUMNS * CARD_WIDTH) + ((GRID_COLUMNS + 1) * PADDING)
    img_height = (rows * CARD_HEIGHT) + ((rows + 1) * PADDING) + 60 # +60 pour le titre
    
    final_img = Image.new('RGB', (img_width, img_height), color=BG_COLOR)
    draw = ImageDraw.Draw(final_img)
    
    # 2. Titre du Club
    try:
        title_font = ImageFont.truetype(font_bold_path, 100)
    except:
        title_font = ImageFont.load_default()
        
    draw.text((PADDING, PADDING), f"Album : {club_name}", fill=TEXT_COLOR, font=title_font)
    
    # 3. Traitement des cartes
    # Ajout d'un User-Agent pour éviter le blocage par Imgur
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
            y = 120 + PADDING + (row * (CARD_HEIGHT + PADDING)) # Offset titre doublé (60->120)
            
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
