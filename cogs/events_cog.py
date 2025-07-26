import os
import re
import requests
import json
from discord.ext import commands
from bs4 import BeautifulSoup

# URL cible
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

        # Script Puppeteer - format simple fonction async
        puppeteer_script = """async ({ page, context }) => {
    try {
        console.log('Début du script Puppeteer');
        console.log('URL cible:', context.LNH_URL);
        console.log('Journée recherchée:', context.journee_number);
        
        // Aller à l'URL fournie dans le contexte
        await page.goto(context.LNH_URL, { waitUntil: 'networkidle2' });
        console.log('Page chargée avec succès');
        
        // Attendre et cliquer sur le menu déroulant - utiliser un sélecteur plus simple
        // Chercher le bouton qui contient le texte "Toutes les journées"
        const buttons = await page.$('button');
        let dropdownButton = null;
        for (const button of buttons) {
            const text = await page.evaluate(el => el.textContent, button);
            if (text && text.includes('Toutes les journées')) {
                dropdownButton = button;
                break;
            }
        }
        
        if (!dropdownButton) {
            throw new Error('Bouton dropdown non trouvé');
        }
        
        console.log('Bouton dropdown trouvé, clic...');
        await dropdownButton.click();
        console.log('Menu déroulant ouvert');
        
        // Attendre que le menu soit visible
        await page.waitForTimeout(1000);
        
        // Formater le numéro de journée en JS avec un '0' devant si besoin.
        const journeeTextToFind = `Journée ${String(context.journee_number).padStart(2, '0')}`;
        console.log('Recherche de:', journeeTextToFind);
        
        // Utiliser un sélecteur XPath, très robuste pour trouver par texte.
        const [journeeListItem] = await page.$x(`//li[contains(., "${journeeTextToFind}")]`);
        
        if (journeeListItem) {
            await journeeListItem.click();
            console.log('Journée sélectionnée avec succès');
        } else {
            // Log des éléments li disponibles pour debug
            const allListItems = await page.$eval('li', items => items.map(item => item.textContent));
            console.log('Éléments de liste disponibles:', allListItems);
            throw new Error(`Impossible de trouver l'élément de liste pour : '${journeeTextToFind}'`);
        }
        
        // Attendre que le contenu se recharge
        console.log('Attente du rechargement du contenu...');
        await page.waitForTimeout(2000); // Attendre un peu avant de chercher
        
        // Essayer plusieurs sélecteurs possibles
        const possibleSelectors = [
            'div[class*="Calendarstyles__StyledContainer"]',
            'div[class*="Calendar"]',
            '[class*="match"]',
            '[class*="game"]'
        ];
        
        let found = false;
        for (const selector of possibleSelectors) {
            try {
                await page.waitForSelector(selector, { timeout: 3000 });
                console.log(`Sélecteur trouvé: ${selector}`);
                found = true;
                break;
            } catch (e) {
                console.log(`Sélecteur ${selector} non trouvé`);
            }
        }
        
        if (!found) {
            console.log('Aucun sélecteur de match trouvé, récupération du HTML quand même...');
        }
        
        await page.waitForTimeout(1000);
        
        console.log('Récupération du HTML final...');
        const content = await page.content();
        console.log('HTML récupéré avec succès');
        return content;
        
    } catch (error) {
        console.error('Erreur dans le script Puppeteer:', error.message);
        
        // Essayer de récupérer le HTML même en cas d'erreur
        try {
            const content = await page.content();
            console.log('HTML récupéré malgré l\'erreur');
            return content;
        } catch (e) {
            throw error;
        }
    }
}"""

        # --- LOGS POUR LE DÉBOGAGE ---
        print("\n--- SCRIPT JS PRÊT À ÊTRE ENVOYÉ ---")
        print(puppeteer_script)
        print("------------------------------------\n")

        api_url = f"https://production-sfo.browserless.io/function?token={BROWSERLESS_TOKEN}"
        headers = { 'Content-Type': 'application/json' }
        
        # Le corps de la requête avec le script et le contexte
        data = {
            "code": puppeteer_script,
            "context": {
                "LNH_URL": LNH_URL,
                "journee_number": journee_number
            }
        }

        # Log du payload complet
        print("\n--- PAYLOAD COMPLET ---")
        print(json.dumps(data, indent=2))
        print("----------------------\n")

        try:
            response = requests.post(api_url, headers=headers, data=json.dumps(data), timeout=45)
            
            # Log détaillé de la réponse
            print(f"Status Code: {response.status_code}")
            print(f"Response Headers: {dict(response.headers)}")
            
            if response.status_code != 200:
                print(f"Response Body: {response.text}")
            
            response.raise_for_status()
            page_source = response.text
            
            print("✅ (API /function) Succès ! HTML final récupéré.")
            print(f"Taille du HTML récupéré: {len(page_source)} caractères")
            
            soup = BeautifulSoup(page_source, 'html.parser')
            match_elements = soup.select('div[class^="Calendarstyles__StyledContainer"]')
            
            print(f"Nombre d'éléments de match trouvés: {len(match_elements)}")
            
            if not match_elements:
                # Log pour debug - voir la structure HTML
                print("\n--- STRUCTURE HTML (premiers 2000 caractères) ---")
                print(page_source[:2000])
                print("--------------------------------------------------\n")
                return f"Aucun match trouvé pour la journée {journee_number}. Le site a peut-être changé."

            scraped_matches = []
            for idx, match_element in enumerate(match_elements):
                print(f"\nAnalyse du match {idx + 1}...")
                
                team1_elem = match_element.select_one('div[class*="StyledTeamContainer"]:nth-of-type(1) span')
                team2_elem = match_element.select_one('div[class*="StyledTeamContainer"]:nth-of-type(3) span')
                score1_elem = match_element.select_one('div[class*="StyledScore"]:nth-of-type(1)')
                score2_elem = match_element.select_one('div[class*="StyledScore"]:nth-of-type(2)')
                
                if team1_elem and team2_elem:
                    match_data = {
                        "team1": team1_elem.get_text(strip=True), 
                        "team2": team2_elem.get_text(strip=True),
                        "score1": score1_elem.get_text(strip=True) if score1_elem else "N/A",
                        "score2": score2_elem.get_text(strip=True) if score2_elem else "N/A",
                    }
                    print(f"Match trouvé: {match_data}")
                    scraped_matches.append(match_data)
                else:
                    print(f"Équipes non trouvées pour le match {idx + 1}")
            
            if not scraped_matches:
                return f"La journée {journee_number} a été sélectionnée, mais aucun match n'est affiché."

            print(f"\n✅ Total de {len(scraped_matches)} matches trouvés")
            return scraped_matches

        except requests.exceptions.HTTPError as e:
            error_text = e.response.text
            print(f"❌ (API) Erreur HTTP de Browserless : {e.response.status_code}")
            print(f"Message d'erreur complet : {error_text}")
            
            # Tentative de parser l'erreur JSON si possible
            try:
                error_json = json.loads(error_text)
                print(f"Erreur parsée : {json.dumps(error_json, indent=2)}")
            except:
                pass
                
            return f"Browserless a retourné une erreur {e.response.status_code} : {error_text}"
        except requests.exceptions.RequestException as e:
            print(f"❌ (API) Erreur de connexion : {type(e).__name__} - {e}")
            return "Impossible de contacter le service de scraping."
        except Exception as e:
            print(f"❌ Erreur inattendue : {type(e).__name__} - {e}")
            return f"Erreur inattendue : {str(e)}"

    @commands.command(name='results')
    @commands.has_permissions(manage_guild=True)
    async def results_command(self, ctx, journee: int):
        import discord  # N'oubliez pas d'importer discord !
        
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
                if score1 > score2: 
                    team1_display, team2_display = f"**{match['team1']}**", match['team2']
                elif score2 > score1: 
                    team1_display, team2_display = match['team1'], f"**{match['team2']}**"
                else: 
                    team1_display, team2_display = match['team1'], match['team2']
            except (ValueError, TypeError):
                team1_display, team2_display = match['team1'], match['team2']
                
            description.append(f"{team1_display} `{match['score1']} - {match['score2']}` {team2_display}")
            
        embed.description = "\n".join(description)
        embed.set_footer(text="Résultats scrapés depuis lnh.fr")
        await thinking_message.edit(content=None, embed=embed)

async def setup(bot):
    await bot.add_cog(EventsCog(bot))
