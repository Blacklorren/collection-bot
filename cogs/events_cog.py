import os
import re
import requests
import json
from discord.ext import commands
from bs4 import BeautifulSoup
from textwrap import dedent

LNH_URL = "https://www.lnh.fr/liquimoly-starligue/calendrier"

class EventsCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    def _scrape_lnh_results(self, journee_number: int) -> list | str:
        BROWSERLESS_TOKEN = os.getenv('BROWSERLESS_TOKEN')
        if not BROWSERLESS_TOKEN:
            print("❌ ERREUR : Le token Browserless est manquant.")
            return "Erreur de configuration du bot : Le token Browserless est manquant."

        print(f"🌍 (API /function) Exécution du script de clic distant pour la journée {journee_number}...")

        # Le script est maintenant une chaîne de caractères standard, PAS un f-string.
        # Les variables sont UNIQUEMENT accédées via `context.variable`.
        puppeteer_script = dedent("""
            async ({ page, context }) => {
                // Aller sur la page cible
                await page.goto(context.LNH_URL);
                
                // Attendre et cliquer sur le menu déroulant principal
                const dropdownButtonSelector = 'button:has-text("Toutes les journées")';
                await page.waitForSelector(dropdownButtonSelector);
                await page.click(dropdownButtonSelector);
                
                // Formater le numéro de journée en JavaScript (ex: 1 -> "01")
                const journeeTextToFind = `Journée ${String(context.journee_number).padStart(2, '0')}`;
                
                // Utiliser un sélecteur XPath pour trouver le <li> par son texte.
                // C'est la méthode la plus robuste pour votre site.
                const [journeeListItem] = await page.$x(`//li[contains(., "${journeeTextToFind}")]`);
                
                if (journeeListItem) {
                    await journeeListItem.click();
                } else {
                    // Si on ne trouve pas l'élément, on lève une erreur claire.
                    throw new Error(`Impossible de trouver l'élément de liste pour : '${journeeTextToFind}'`);
                }
                
                // Attendre que le contenu se mette à jour et retourner le HTML final.
                await page.waitForSelector('div[class^="Calendarstyles__StyledContainer"]');
                await page.waitForTimeout(1500);
                return await page.content();
            }
        """)

        api_url = f"https://production-sfo.browserless.io/function?token={BROWSERLESS_TOKEN}"
        headers = { 'Content-Type': 'application/json' }
        
        # Le corps de la requête. Toutes les variables sont passées ici.
        data = {
            "code": puppeteer_script,
            "context": {
                "LNH_URL": LNH_URL,
                "journee_number": journee_number
            }
        }

        try:
            response = requests.post(api_url, headers=headers, data=json.dumps(data), timeout=45)
            response.raise_for_status()
            page_source = response.text
            
            print("✅ (API /function) Succès ! HTML final récupéré.")
            
            soup = BeautifulSoup(page_source, 'html.parser')
            match_elements = soup.select('div[class^="Calendarstyles__StyledContainer"]')
            
            if not match_elements:
                return f"Aucun match trouvé pour la journée {journee_number}. Le site a peut-être changé."

            scraped_matches = []
            for match_element in match_elements:
                team1_elem = match_element.select_one('div[class*="StyledTeamContainer"]:nth-of-type(1) span')
                team2_elem = match_element.select_one('div[class*="StyledTeamContainer"]:nth-of-type(3) span')
                score1_elem = match_element.select_one('div[class*="StyledScore"]:nth-of-type(1)')
                score2_elem = match_element.select_one('div[class*="StyledScore"]:nth-of-type(2)')
                
                if team1_elem and team2_elem:
                    scraped_matches.append({
                        "team1": team1_elem.get_text(strip=True), "team2": team2_elem.get_text(strip=True),
                        "score1": score1_elem.get_text(strip=True) if score1_elem else "N/A",
                        "score2": score2_elem.get_text(strip=True) if score2_elem else "N/A",
                    })
            
            if not scraped_matches:
                return f"La journée {journee_number} a été sélectionnée, mais aucun match n'est affiché."

            return scraped_matches

        except requests.exceptions.HTTPError as e:
            error_text = e.response.text
            print(f"❌ (API) Erreur HTTP de Browserless : {e.response.status_code} - {error_text}")
            return f"Browserless a retourné une erreur {e.response.status_code} : {error_text}"
        except requests.exceptions.RequestException as e:
            print(f"❌ (API) Erreur de connexion : {e}")
            return "Impossible de contacter le service de scraping."

    @commands.command(name='results')
    @commands.has_permissions(manage_guild=True)
    async def results_command(self, ctx, journee: int):
        thinking_message = await ctx.send(f"🔍 **Recherche en cours...** Je consulte le site de la LNH pour les résultats de la journée n°{journee}.")
        matches_or_error = await self.bot.loop.run_in_executor(None, self._scrape_lnh_results, journee)
        if isinstance(matches_or_error, str):
            await thinking_message.edit(content=f"❌ **Erreur :** {matches_or_error}")
            return
        if not matches_or_error:
            await thinking_message.edit(content=f"ℹ️ Aucun match trouvé pour la journée {journee}.")
            return
        embed = discord.Embed(title=f"🏆 Résultats - Journée {journee}", color=0x006eff)
        description = []
        for match in matches_or_error:
            try:
                score1, score2 = int(match['score1']), int(match['score2'])
                if score1 > score2: team1_display, team2_display = f"**{match['team1']}**", match['team2']
                elif score2 > score1: team1_display, team2_display = match['team1'], f"**{match['team2']}**"
                else: team1_display, team2_display = match['team1'], match['team2']
            except (ValueError, TypeError):
                team1_display, team2_display = match['team1'], match['team2']
            description.append(f"{team1_display} `{match['score1']} - {match['score2']}` {team2_display}")
        embed.description = "\n".join(description)
        embed.set_footer(text="Résultats scrapés depuis lnh.fr")
        await thinking_message.edit(content=None, embed=embed)

async def setup(bot):
    await bot.add_cog(EventsCog(bot))
