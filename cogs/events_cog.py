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

        # --- SCRIPT PUPPETEER AMÉLIORÉ ---
        # Ce script est plus robuste car il utilise des attentes explicites (waitForSelector)
        # au lieu de délais fixes (waitForTimeout) et des sélecteurs plus stables (XPath).
        puppeteer_script = """
async ({ page, context }) => {
    const { LNH_URL, journee_number } = context;
    console.log(`Début du script Puppeteer pour la journée ${journee_number} sur ${LNH_URL}`);

    try {
        await page.goto(LNH_URL, { waitUntil: 'networkidle2' });
        console.log('Page chargée avec succès.');

        // Attendre et fermer la bannière de cookies si elle existe
        try {
            const cookieButtonSelector = '#axeptio_btn_acceptAll';
            await page.waitForSelector(cookieButtonSelector, { timeout: 5000 });
            await page.click(cookieButtonSelector);
            console.log('Bannière de cookies acceptée.');
            await page.waitForTimeout(500); // Petite pause après le clic
        } catch (e) {
            console.log("Bannière de cookies non trouvée ou déjà acceptée, on continue.");
        }

        // Clic sur le menu déroulant des journées
        // Utilisation de XPath pour trouver le bouton par son texte, c'est plus robuste.
        const dropdownXPath = "//button[contains(., 'Toutes les journées')]";
        await page.waitForXPath(dropdownXPath);
        const [dropdownButton] = await page.$x(dropdownXPath);
        if (!dropdownButton) throw new Error('Bouton du menu déroulant introuvable.');
        
        await dropdownButton.click();
        console.log('Menu déroulant ouvert.');

        // Sélection de la journée voulue
        const journeeTextToFind = `Journée ${String(journee_number).padStart(2, '0')}`;
        const listItemXPath = `//li[normalize-space() = '${journeeTextToFind}']`;
        
        await page.waitForXPath(listItemXPath, { visible: true });
        const [journeeListItem] = await page.$x(listItemXPath);
        if (!journeeListItem) throw new Error(`Impossible de trouver la journée : '${journeeTextToFind}'`);

        await journeeListItem.click();
        console.log(`Journée '${journeeTextToFind}' sélectionnée.`);

        // Attendre que le contenu se mette à jour.
        // On attend qu'un conteneur de match soit de nouveau visible. C'est plus fiable qu'un timeout.
        const matchContainerSelector = 'div[class^="Calendarstyles__StyledContainer"]';
        await page.waitForSelector(matchContainerSelector, { visible: true, timeout: 10000 });
        console.log('Le contenu des matchs a été rechargé.');

        // Récupérer le HTML final
        const content = await page.content();
        console.log(`Récupération du HTML final (${content.length} caractères).`);
        return content;

    } catch (error) {
        console.error('Erreur dans le script Puppeteer:', error.message);
        // En cas d'erreur, on essaie quand même de retourner le HTML pour le débogage.
        const errorContent = await page.content();
        return { error: error.message, html: errorContent };
    }
}
"""
        
        api_url = f"https://production-sfo.browserless.io/function?token={BROWSERLESS_TOKEN}"
        headers = { 'Content-Type': 'application/json' }
        data = {
            "code": puppeteer_script,
            "context": {
                "LNH_URL": LNH_URL,
                "journee_number": journee_number
            }
        }

        try:
            response = requests.post(api_url, headers=headers, data=json.dumps(data), timeout=60)
            response.raise_for_status()
            
            # Browserless renvoie un objet JSON si le script renvoie un objet.
            # S'il y a une erreur dans Puppeteer, on la récupère ici.
            result = response.json()
            if isinstance(result, dict) and 'error' in result:
                print(f"❌ Erreur retournée par le script Puppeteer: {result['error']}")
                # Afficher le début du HTML pour aider au débogage
                if 'html' in result and result['html']:
                     print("\n--- DÉBUT DU HTML RÉCUPÉRÉ (POUR DÉBOGAGE) ---")
                     print(result['html'][:2000])
                     print("-------------------------------------------\n")
                return f"Une erreur est survenue lors du scraping : {result['error']}"

            # Si tout va bien, la réponse est le code HTML de la page.
            page_source = result
            print("✅ (API /function) Succès ! HTML final récupéré.")
            
            soup = BeautifulSoup(page_source, 'html.parser')
            
            # Le sélecteur `select` avec `[class^=...]` est une bonne pratique pour les classes dynamiques. [1, 12]
            match_elements = soup.select('a[class*="Calendarstyles__StyledLink"]')
            
            if not match_elements:
                return f"Aucun match trouvé pour la journée {journee_number}. Le site a peut-être changé."

            scraped_matches = []
            for match_element in match_elements:
                teams = match_element.select('span[class*="TeamName"]')
                scores = match_element.select('div[class*="Score"]')
                
                if len(teams) == 2 and len(scores) == 2:
                    match_data = {
                        "team1": teams[0].get_text(strip=True), 
                        "team2": teams[1].get_text(strip=True),
                        "score1": scores[0].get_text(strip=True),
                        "score2": scores[1].get_text(strip=True),
                    }
                    scraped_matches.append(match_data)
            
            if not scraped_matches:
                return f"La journée {journee_number} a été sélectionnée, mais le format des matchs est inattendu."

            return scraped_matches

        except requests.exceptions.HTTPError as e:
            return f"Browserless a retourné une erreur {e.response.status_code} : {e.response.text}"
        except requests.exceptions.RequestException as e:
            return f"Impossible de contacter le service de scraping : {e}"
        except Exception as e:
            return f"Erreur inattendue : {e}"```

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
