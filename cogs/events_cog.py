import os
import requests
import json
import discord
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

        print(f"🌍 (API /scrape) Lancement du scraping pour la journée n°{journee_number}...")

        # On utilise maintenant l'endpoint /scrape qui supporte les actions
        api_url = f"https://production-sfo.browserless.io/scrape?token={BROWSERLESS_TOKEN}"
        headers = {'Content-Type': 'application/json'}
        
        # Payload corrigé (sans point-virgule et avec le bon endpoint)
        data = {
            "url": LNH_URL,  # CORRECTION: pas de point-virgule ici
            "actions": [
                {
                    "type": "cookies",
                    "action": "accept",
                    "selector": "#axeptio_btn_acceptAll",
                    "timeout": 5000
                },
                {
                    "type": "click",
                    "selector": "//button[contains(., 'Toutes les journées')]",
                    "xpath": True,
                    "timeout": 10000
                },
                {
                    "type": "click",
                    "selector": f"//li[contains(., 'Journée {str(journee_number).zfill(2)}')]",
                    "xpath": True,
                    "timeout": 10000,
                    "waitFor": {
                        "selector": 'a[class*="Calendarstyles__StyledLink"]',
                        "timeout": 15000
                    }
                }
            ],
            "waitFor": {
                "selector": 'a[class*="Calendarstyles__StyledLink"]',
                "timeout": 30000
            }
        }

        print("\n" + "="*25 + " PAYLOAD ENVOYÉ À BROWSERLESS " + "="*25)
        print(json.dumps(data, indent=2, ensure_ascii=False))
        print("="*78 + "\n")
        
        try:
            response = requests.post(api_url, headers=headers, data=json.dumps(data, ensure_ascii=False), timeout=60)
            
            print("\n" + "="*25 + " RÉPONSE REÇUE DE BROWSERLESS " + "="*25)
            print(f"Status Code: {response.status_code}")
            print(f"Headers: {response.headers}")
            
            if response.status_code != 200:
                print(f"Erreur: {response.text[:500]}")
                return f"Erreur Browserless (Code {response.status_code})"
            
            # Browserless renvoie un objet JSON avec le HTML dans la clé 'data'
            result = response.json()
            html_content = result.get('data', '')
            
            if not html_content:
                return "Aucun contenu HTML retourné par Browserless"
                
            print("✅ Scraping réussi, analyse du HTML...")
            soup = BeautifulSoup(html_content, 'html.parser')
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
            return f"Erreur réseau : {str(e)}"
        except Exception as e:
            return f"Erreur inattendue : {str(e)}"

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
                            team1_display = f"**{match['team1']}**"
                            team2_display = match['team2']
                        elif score2 > score1: 
                            team1_display = match['team1']
                            team2_display = f"**{match['team2']}**"
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
