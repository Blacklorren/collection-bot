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

        print(f"🌍 Lancement du scraping pour la journée n°{journee_number}...")
        
        # On utilise l'endpoint /function avec la signature correcte
        api_url = f"https://production-sfo.browserless.io/function?token={BROWSERLESS_TOKEN}"
        headers = {'Content-Type': 'application/json'}
        
        # Script JavaScript corrigé
        journee_str = str(journee_number).zfill(2)
        puppeteer_script = f"""
        async function runScraping(page) {{
            let step = 'Initialisation';
            
            try {{
                step = '1. Navigation vers la page';
                await page.goto('{LNH_URL}', {{ waitUntil: 'networkidle2', timeout: 30000 }});
                
                step = '2. Gestion de la bannière de cookies';
                try {{
                    await page.waitForSelector('#axeptio_btn_acceptAll', {{ timeout: 5000 }});
                    await page.click('#axeptio_btn_acceptAll');
                    await page.waitForTimeout(1000);
                }} catch (e) {{
                    console.log("Bannière de cookies non trouvée");
                }}
                
                step = '3. Clic sur le menu déroulant';
                const dropdownXPath = "//button[contains(., 'Toutes les journées')]";
                await page.waitForXPath(dropdownXPath, {{ timeout: 10000 }});
                const [dropdownButton] = await page.$x(dropdownXPath);
                if (!dropdownButton) throw new Error('Menu déroulant introuvable');
                await dropdownButton.click();
                
                step = '4. Sélection de la journée';
                const journeeText = "Journée {journee_str}";
                const listItemXPath = `//li[contains(., "${{journeeText}}")]`;
                await page.waitForXPath(listItemXPath, {{ timeout: 10000 }});
                const [journeeListItem] = await page.$x(listItemXPath);
                if (!journeeListItem) throw new Error('Élément journée introuvable');
                await journeeListItem.click();
                
                step = '5. Attente des résultats';
                await page.waitForSelector('a[class*="Calendarstyles__StyledLink"]', {{ 
                    visible: true, 
                    timeout: 15000 
                }});
                
                step = '6. Extraction du HTML';
                return await page.content();
                
            }} catch (error) {{
                return {{ 
                    error: `Échec à l'étape: ${{step}}`, 
                    details: error.message 
                }};
            }}
        }}
        
        module.exports = {{ runScraping }};
        """.strip()

        data = {"code": puppeteer_script}
        
        print("\n" + "="*25 + " PAYLOAD ENVOYÉ À BROWSERLESS " + "="*25)
        print(f"Code length: {len(puppeteer_script)} characters")
        print("="*78 + "\n")
        
        try:
            response = requests.post(api_url, headers=headers, json=data, timeout=60)
            
            print("\n" + "="*25 + " RÉPONSE DE BROWSERLESS " + "="*25)
            print(f"Status Code: {response.status_code}")
            
            if response.status_code != 200:
                print(f"Erreur: {response.text[:500]}")
                return f"Erreur Browserless (HTTP {response.status_code})"
            
            result = response.json()
            
            if isinstance(result, dict) and 'error' in result:
                error_details = result.get('details', 'Pas de détails supplémentaires')
                return f"Erreur scraping: {result['error']} - {error_details}"
                
            print("✅ HTML reçu, analyse en cours...")
            soup = BeautifulSoup(result, 'html.parser')
            match_elements = soup.select('a[class*="Calendarstyles__StyledLink"]')
            
            if not match_elements:
                return f"Aucun match trouvé pour la journée {journee_number}"
            
            scraped_matches = []
            for match in match_elements:
                teams = match.select('span[class*="TeamName"]')
                scores = match.select('div[class*="Score"]')
                
                if len(teams) == 2 and len(scores) == 2:
                    scraped_matches.append({
                        "team1": teams[0].get_text(strip=True),
                        "team2": teams[1].get_text(strip=True),
                        "score1": scores[0].get_text(strip=True),
                        "score2": scores[1].get_text(strip=True)
                    })
            
            print(f"🔢 {len(scraped_matches)} matchs trouvés")
            return scraped_matches

        except Exception as e:
            return f"Erreur: {str(e)}"

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
            
            for match in matches_or_error:
                try:
                    score1 = match['score1']
                    score2 = match['score2']
                    team1 = match['team1']
                    team2 = match['team2']
                    
                    # Formatage basique sans mise en évidence du gagnant
                    embed.add_field(
                        name=f"{team1} vs {team2}",
                        value=f"{score1} - {score2}",
                        inline=False
                    )
                except Exception:
                    continue
            
            embed.set_footer(text="Données extraites de lnh.fr")
            embed.set_thumbnail(url="https://www.lnh.fr/images/logos/logo-lnh-simple-512.png")
            await thinking_message.edit(content=None, embed=embed)

async def setup(bot):
    await bot.add_cog(EventsCog(bot))
