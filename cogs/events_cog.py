import os
import requests
import json
import discord
from discord.ext import commands
from bs4 import BeautifulSoup

# URL cible pour le scraping des résultats
LNH_URL = "https://www.lnh.fr/liquimoly-starligue/calendrier"

class EventsCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    def _scrape_lnh_results(self, journee_number: int) -> list | str:
        """
        Scrape les résultats d'une journée spécifique sur le site de la LNH en utilisant Browserless.io.
        
        Cette fonction exécute un script Puppeteer distant pour simuler la navigation,
        cliquer sur la bonne journée, et récupérer le HTML résultant.
        """
        BROWSERLESS_TOKEN = os.getenv('BROWSERLESS_TOKEN')
        if not BROWSERLESS_TOKEN:
            print("❌ ERREUR : Le token Browserless est manquant dans les variables d'environnement.")
            return "Erreur de configuration du bot : Le token Browserless est manquant."

        print(f"🌍 (API /function) Lancement du scraping pour la journée n°{journee_number}...")

        # --- SCRIPT PUPPETEER CORRIGÉ ET AMÉLIORÉ ---
        # Ce script est plus robuste :
        # 1. Gère la popup de cookies.
        # 2. Utilise des sélecteurs XPath robustes basés sur le texte des éléments.
        # 3. Remplace les attentes fixes (timeouts) par des attentes intelligentes (waitForSelector/XPath).
        puppeteer_script = """
        async ({ page, context }) => {
            const { LNH_URL, journee_number } = context;
            console.log(`Début du script Puppeteer pour la journée ${journee_number} sur ${LNH_URL}`);

            try {
                await page.goto(LNH_URL, { waitUntil: 'networkidle2' });
                console.log('Page chargée avec succès.');

                // Gérer la bannière de cookies qui peut bloquer les clics
                try {
                    const cookieButtonSelector = '#axeptio_btn_acceptAll';
                    await page.waitForSelector(cookieButtonSelector, { timeout: 5000 });
                    await page.click(cookieButtonSelector);
                    console.log('Bannière de cookies acceptée.');
                    await page.waitForTimeout(500); // Petite pause après le clic
                } catch (e) {
                    console.log("Bannière de cookies non trouvée, on continue.");
                }

                // Clic sur le menu déroulant des journées via XPath
                const dropdownXPath = "//button[contains(., 'Toutes les journées')]";
                await page.waitForXPath(dropdownXPath);
                const [dropdownButton] = await page.$x(dropdownXPath);
                if (!dropdownButton) throw new Error('Bouton du menu déroulant introuvable.');
                
                await dropdownButton.click();
                console.log('Menu déroulant ouvert.');

                // Sélection de la journée voulue via XPath
                const journeeTextToFind = `Journée ${String(journee_number).padStart(2, '0')}`;
                const listItemXPath = `//li[normalize-space() = '${journeeTextToFind}']`;
                
                await page.waitForXPath(listItemXPath, { visible: true });
                const [journeeListItem] = await page.$x(listItemXPath);
                if (!journeeListItem) throw new Error(`Impossible de trouver la journée : '${journeeTextToFind}'`);

                await journeeListItem.click();
                console.log(`Journée '${journeeTextToFind}' sélectionnée.`);

                // Attendre que le contenu se mette à jour de manière fiable
                const matchContainerSelector = 'a[class*="Calendarstyles__StyledLink"]';
                await page.waitForSelector(matchContainerSelector, { visible: true, timeout: 10000 });
                console.log('Le contenu des matchs a été rechargé.');

                // Récupérer le HTML final
                const content = await page.content();
                console.log(`HTML final récupéré (${content.length} caractères).`);
                return content;

            } catch (error) {
                console.error('Erreur dans le script Puppeteer:', error.message);
                // En cas d'erreur, on retourne un objet avec le message d'erreur pour un meilleur débogage
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
            
            result = response.json()
            # Si le script Puppeteer a renvoyé un objet d'erreur
            if isinstance(result, dict) and 'error' in result:
                print(f"❌ Erreur retournée par le script Puppeteer: {result['error']}")
                return f"Une erreur est survenue lors du scraping : {result['error']}"

            # Si tout s'est bien passé, le résultat est le code source HTML
            page_source = result
            print("✅ (API /function) Succès ! HTML final récupéré.")
            
            soup = BeautifulSoup(page_source, 'html.parser')
            
            # Les matchs sont dans des balises <a> avec une classe qui commence par "Calendarstyles__StyledLink"
            match_elements = soup.select('a[class*="Calendarstyles__StyledLink"]')
            
            if not match_elements:
                print("Aucun élément de match trouvé avec le sélecteur CSS. Le site a peut-être changé.")
                return f"Aucun match trouvé pour la journée {journee_number}. La structure du site a peut-être changé."

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
                else:
                    print(f"Données incomplètes pour un match. Teams: {len(teams)}, Scores: {len(scores)}")
            
            if not scraped_matches:
                return f"La journée {journee_number} a été trouvée, mais le format des matchs semble avoir changé."

            print(f"✅ Total de {len(scraped_matches)} matches scrapés avec succès.")
            return scraped_matches

        except requests.exceptions.HTTPError as e:
            error_message = f"Browserless a retourné une erreur HTTP {e.response.status_code}. Réponse : {e.response.text}"
            print(f"❌ {error_message}")
            return error_message
        except requests.exceptions.RequestException as e:
            error_message = f"Impossible de contacter le service de scraping : {e}"
            print(f"❌ {error_message}")
            return error_message
        except Exception as e:
            error_message = f"Une erreur inattendue est survenue : {e}"
            print(f"❌ {error_message}")
            return error_message

    @commands.command(name='results')
    @commands.has_permissions(manage_guild=True)
    async def results_command(self, ctx, journee: int):
        """
        Affiche les résultats d'une journée de Liqui Moly Starligue.
        """
        thinking_message = await ctx.send(f"🔍 **Recherche en cours...** Je consulte le site de la LNH pour les résultats de la journée n°{journee}.")
        
        # Exécute la fonction de scraping (qui peut être longue) dans un thread séparé
        matches_or_error = await self.bot.loop.run_in_executor(None, self._scrape_lnh_results, journee)
        
        # Si la fonction a retourné une chaîne de caractères, c'est une erreur
        if isinstance(matches_or_error, str):
            await thinking_message.edit(content=f"❌ **Erreur :** {matches_or_error}")
            return
            
        if not matches_or_error:
            await thinking_message.edit(content=f"ℹ️ Aucun match trouvé pour la journée {journee}.")
            return
            
        embed = discord.Embed(
            title=f"🏆 Résultats - Liqui Moly Starligue - Journée {journee}",
            color=0x006eff
        )
        description = []
        
        for match in matches_or_error:
            # Met en gras l'équipe gagnante
            try:
                score1, score2 = int(match['score1']), int(match['score2'])
                if score1 > score2: 
                    team1_display, team2_display = f"**{match['team1']}**", match['team2']
                elif score2 > score1: 
                    team1_display, team2_display = match['team1'], f"**{match['team2']}**"
                else: 
                    team1_display, team2_display = match['team1'], match['team2'] # Match nul
            except (ValueError, TypeError): # Si les scores ne sont pas des nombres (ex: "N/A" ou match à venir)
                team1_display, team2_display = match['team1'], match['team2']
                
            description.append(f"{team1_display} `{match['score1']} - {match['score2']}` {team2_display}")
            
        embed.description = "\n".join(description)
        embed.set_footer(text="Données extraites de lnh.fr")
        embed.set_thumbnail(url="https://www.lnh.fr/images/logos/logo-lnh-simple-512.png") # Ajout du logo LNH
        
        await thinking_message.edit(content=None, embed=embed)

async def setup(bot):
    """
    Fonction appelée par discord.py pour charger le Cog.
    """
    await bot.add_cog(EventsCog(bot))
