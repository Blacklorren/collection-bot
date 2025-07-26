import os
import requests
import json
import discord
import textwrap
from discord.ext import commands
from bs4 import BeautifulSoup

LNH_URL = "https://www.lnh.fr/liquimoly-starligue/calendrier"

class EventsCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    def _scrape_lnh_results(self, journee_number: int) -> list | str:
        BROWSERLESS_TOKEN = os.getenv('BROWSERLESS_TOKEN')
        if not BROWSERLESS_TOKEN:
            return "Erreur de configuration : Le token Browserless est manquant."

        print(f"🌍 (API /content) Lancement du scraping pour la journée n°{journee_number}...")

        # Configuration pour l'endpoint /content
        api_url = f"https://production-sfo.browserless.io/content?token={BROWSERLESS_TOKEN}"
        headers = {'Content-Type': 'application/json'}
        
        # Payload pour /content
        data = {
            "url": LNH_URL,
            "waitFor": {
                "selector": 'a[class*="Calendarstyles__StyledLink"]',
                "timeout": 30000
            },
            "actions": [
                {
                    "type": "cookies",
                    "action": "accept",
                    "selector": "#axeptio_btn_acceptAll"
                },
                {
                    "type": "click",
                    "selector": "//button[contains(., 'Toutes les journées')]",
                    "xpath": True,
                    "waitForNavigation": True
                },
                {
                    "type": "click",
                    "selector": f"//li[contains(., 'Journée {str(journee_number).zfill(2)}')]",
                    "xpath": True,
                    "waitForNavigation": True,
                    "waitFor": {
                        "selector": 'a[class*="Calendarstyles__StyledLink"]',
                        "timeout": 15000
                    }
                }
            ]
        }

        # --- LOGS DE DÉBOGAGE DU PAYLOAD ---
        print("\n" + "="*25 + " PAYLOAD ENVOYÉ À BROWSERLESS " + "="*25)
        print("--- Payload JSON complet ---")
        print(json.dumps(data, indent=2, ensure_ascii=False))
        print("="*78 + "\n")
        
        try:
            response = requests.post(api_url, headers=headers, data=json.dumps(data, ensure_ascii=False), timeout=60)
            
            # --- LOGS DE DÉBOGAGE DE LA RÉPONSE ---
            print("\n" + "="*25 + " RÉPONSE REÇUE DE BROWSERLESS " + "="*25)
            print(f"Status Code: {response.status_code}")
            print(f"Headers: {response.headers}")
            print(f"Taille du contenu: {len(response.text)} caractères")
            
            if response.status_code != 200:
                print(f"--- Début du contenu d'erreur ---\n{response.text[:500]}\n--- Fin du contenu d'erreur ---")
                return f"Erreur de l'API Browserless (Code {response.status_code})"
            
            print("✅ Scraping réussi, analyse du HTML...")
            soup = BeautifulSoup(response.text, 'html.parser')
            match_elements = soup.select('a[class*="Calendarstyles__StyledLink"]')
            
            if not match_elements: 
                return f"Aucun match trouvé pour la journée {journee_number}."
            
            scraped_matches = []
            for match_element in match_elements:
                teams = match_element.select('span[class*="TeamName"]')
                scores = match_element.select('div[class*="Score"]')
                if len(teams) == 2 and len(scores) == 2:
                    scraped_matches.append({
                        "team1": teams[0].get_text(strip=True),
                        "team2": teams[1].get_text(strip=True),
                        "score1": scores[0].get_text(strip=True),
                        "score2": scores[1].get_text(strip=True)
                    })
            
            print(f"🔢 {len(scraped_matches)} matchs trouvés")
            return scraped_matches

        except requests.exceptions.RequestException as e:
            return f"Impossible de contacter le service de scraping : {str(e)}"
        except Exception as e:
            return f"Une erreur inattendue est survenue : {str(e)}"

    @commands.command(name='results')
    @commands.has_permissions(manage_guild=True)
    async def results_command(self, ctx, journee: int):
        thinking_message = await ctx.send(f"🔍 **Recherche en cours...** Je consulte le site de la LNH pour les résultats de la journée n°{journee}.")
        matches_or_error = await self.bot.loop.run_in_executor(None, self._scrape_lnh_results, journee)
        
        if isinstance(matches_or_error, str): 
            await thinking_message.edit(content=f"❌ **Erreur :** {matches_or_error}")
        elif not matches_or_error: 
            await thinking_message.edit(content=f"ℹ️ Aucun match trouvé pour la journée {journee}.")
        else:
            embed = discord.Embed(
                title=f"🏆 Résultats - Liqui Moly Starligue - Journée {journee}",
                color=0x006eff
            )
            description = []
            for match in matches_or_error:
                try:
                    score1 = int(match['score1']) if match['score1'].isdigit() else None
                    score2 = int(match['score2']) if match['score2'].isdigit() else None
                    
                    if score1 is not None and score2 is not None:
                        if score1 > score2: 
                            team1_display, team2_display = f"**{match['team1']}**", match['team2']
                        elif score2 > score1: 
                            team1_display, team2_display = match['team1'], f"**{match['team2']}**"
                        else: 
                            team1_display, team2_display = match['team1'], match['team2']
                    else:
                        team1_display, team2_display = match['team1'], match['team2']
                except (ValueError, TypeError): 
                    team1_display, team2_display = match['team1'], match['team2']
                
                score_display = f"{match['score1']} - {match['score2']}"
                description.append(f"{team1_display} `{score_display}` {team2_display}")
            
            embed.description = "\n".join(description)
            embed.set_footer(text="Données extraites de lnh.fr")
            embed.set_thumbnail(url="https://www.lnh.fr/images/logos/logo-lnh-simple-512.png")
            await thinking_message.edit(content=None, embed=embed)

async def setup(bot):
    await bot.add_cog(EventsCog(bot))
