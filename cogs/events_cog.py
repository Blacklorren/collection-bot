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

        print(f"🌍 (API /function) Lancement du scraping pour la journée n°{journee_number}...")

        script_lines = [
            "async ({ page, context }) => {",
            "    const { LNH_URL, journee_number } = context;",
            "    let step = 'Initialisation';",
            "    try {",
            "        step = '1. Navigation vers la page';",
            "        await page.goto(LNH_URL, { waitUntil: 'networkidle2', timeout: 30000 });",
            "        step = '2. Gestion de la bannière de cookies';",
            "        try {",
            "            const cookieButtonSelector = '#axeptio_btn_acceptAll';",
            "            await page.waitForSelector(cookieButtonSelector, { timeout: 5000 });",
            "            await page.click(cookieButtonSelector);",
            "            await page.waitForTimeout(500);",
            "        } catch (e) {",
            "            console.log('-> Bannière de cookies non trouvée.');",
            "        }",
            "        step = '3. Clic sur le menu déroulant';",
            "        const dropdownXPath = \"//button[contains(., 'Toutes les journées')]\";",
            "        await page.waitForXPath(dropdownXPath, { timeout: 10000 });",
            "        const [dropdownButton] = await page.$x(dropdownXPath);",
            "        if (!dropdownButton) throw new Error('Élément du menu déroulant introuvable.');",
            "        await dropdownButton.click();",
            "        step = '4. Sélection de la journée';",
            "        const journeeTextToFind = `Journée ${String(journee_number).padStart(2, '0')}`;",
            "        const listItemXPath = `//li[contains(., \"${journeeTextToFind}\")]`;",
            "        await page.waitForXPath(listItemXPath, { visible: true, timeout: 10000 });",
            "        const [journeeListItem] = await page.$x(listItemXPath);",
            "        if (!journeeListItem) throw new Error(`Élément de la journée '${journeeTextToFind}' introuvable.`);",
            "        await journeeListItem.click();",
            "        step = '5. Attente du rechargement des matchs';",
            "        const matchContainerSelector = 'a[class*=\"Calendarstyles__StyledLink\"]';",
            "        await page.waitForSelector(matchContainerSelector, { visible: true, timeout: 15000 });",
            "        step = '6. Récupération du HTML final';",
            "        return await page.content();",
            "    } catch (error) {",
            "        console.error(`Échec à l'étape: ${step} | Erreur: ${error.message}`);",
            "        return { error: `Échec à l'étape : ${step}`, errorMessage: error.message };",
            "    }",
            "}"
        ]
        puppeteer_script = "\n".join(script_lines)
        
        api_url = f"https://production-sfo.browserless.io/function?token={BROWSERLESS_TOKEN}"
        headers = {'Content-Type': 'application/json; charset=utf-8'}
        
        # --- LA CORRECTION EST ICI ---
        # Le point-virgule a été retiré. Le JSON sera maintenant valide.
        data = {
            "code": puppeteer_script,
            "context": {
                "LNH_URL": LNH_URL, # PAS DE POINT-VIRGULE ICI
                "journee_number": journee_number
            }
        }

        print("\n" + "="*25 + " PAYLOAD ENVOYÉ À BROWSERLESS " + "="*25)
        print(json.dumps(data, indent=2, ensure_ascii=False))
        print("="*78 + "\n")
        
        try:
            response = requests.post(api_url, headers=headers, data=json.dumps(data).encode('utf-8'), timeout=60)
            
            print("\n" + "="*25 + " RÉPONSE REÇUE DE BROWSERLESS " + "="*25)
            print(f"Status Code: {response.status_code}")
            print("--- Contenu brut de la réponse ---")
            print(response.text)
            print("------------------------------------")
            print("="*79 + "\n")

            if response.status_code != 200:
                return f"Erreur de l'API Browserless (Code {response.status_code}): {response.text}"
            
            result = response.json()
            if isinstance(result, dict) and 'error' in result:
                return f"Erreur lors du scraping : {result.get('error', 'Inconnue')}"

            print("✅ Scraping réussi, analyse du HTML.")
            soup = BeautifulSoup(result, 'html.parser')
            match_elements = soup.select('a[class*="Calendarstyles__StyledLink"]')
            
            if not match_elements: return f"Aucun match trouvé pour la journée {journee_number}."
            
            scraped_matches = []
            for match_element in match_elements:
                teams = match_element.select('span[class*="TeamName"]')
                scores = match_element.select('div[class*="Score"]')
                if len(teams) == 2 and len(scores) == 2:
                    scraped_matches.append({"team1": teams[0].get_text(strip=True),"team2": teams[1].get_text(strip=True),"score1": scores[0].get_text(strip=True),"score2": scores[1].get_text(strip=True)})
            
            return scraped_matches

        except Exception as e:
            return f"Une erreur inattendue est survenue : {type(e).__name__} - {e}"

    @commands.command(name='results')
    @commands.has_permissions(manage_guild=True)
    async def results_command(self, ctx, journee: int):
        thinking_message = await ctx.send(f"🔍 **Recherche en cours...** Je consulte le site de la LNH pour la journée n°{journee}.")
        matches_or_error = await self.bot.loop.run_in_executor(None, self._scrape_lnh_results, journee)
        
        if isinstance(matches_or_error, str): await thinking_message.edit(content=f"❌ **Erreur :** {matches_or_error}")
        elif not matches_or_error: await thinking_message.edit(content=f"ℹ️ Aucun match trouvé pour la journée {journee}.")
        else:
            embed = discord.Embed(title=f"🏆 Résultats - Liqui Moly Starligue - Journée {journee}",color=0x006eff)
            description = []
            for match in matches_or_error:
                try:
                    score1, score2 = int(match['score1']), int(match['score2'])
                    if score1 > score2: team1_display, team2_display = f"**{match['team1']}**", match['team2']
                    elif score2 > score1: team1_display, team2_display = match['team1'], f"**{match['team2']}**"
                    else: team1_display, team2_display = match['team1'], match['team2']
                except (ValueError, TypeError): team1_display, team2_display = match['team1'], match['team2']
                description.append(f"{team1_display} `{match['score1']} - {match['score2']}` {team2_display}")
            embed.description = "\n".join(description)
            embed.set_footer(text="Données extraites de lnh.fr")
            embed.set_thumbnail(url="https://www.lnh.fr/images/logos/logo-lnh-simple-512.png")
            await thinking_message.edit(content=None, embed=embed)

async def setup(bot):
    await bot.add_cog(EventsCog(bot))
