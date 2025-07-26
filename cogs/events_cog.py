import os
import requests
import json
import discord
import textwrap
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
        """
        BROWSERLESS_TOKEN = os.getenv('BROWSERLESS_TOKEN')
        if not BROWSERLESS_TOKEN:
            print("❌ ERREUR : Le token Browserless est manquant.")
            return "Erreur de configuration du bot : Le token Browserless est manquant."

        print(f"🌍 (API /function) Lancement du scraping pour la journée n°{journee_number}...")

        # --- Les commentaires JS ont été retirés de cette section pour éviter les erreurs de parsing ---
        puppeteer_script = textwrap.dedent("""
            async ({ page, context }) => {
                const { LNH_URL, journee_number } = context;
                console.log(`Début du script Puppeteer pour la journée ${journee_number} sur ${LNH_URL}`);

                try {
                    await page.goto(LNH_URL, { waitUntil: 'networkidle2' });
                    console.log('Page chargée avec succès.');

                    try {
                        const cookieButtonSelector = '#axeptio_btn_acceptAll';
                        await page.waitForSelector(cookieButtonSelector, { timeout: 5000 });
                        await page.click(cookieButtonSelector);
                        console.log('Bannière de cookies acceptée.');
                        await page.waitForTimeout(500);
                    } catch (e) {
                        console.log("Bannière de cookies non trouvée, on continue.");
                    }

                    const dropdownXPath = "//button[contains(., 'Toutes les journées')]";
                    await page.waitForXPath(dropdownXPath);
                    const [dropdownButton] = await page.$x(dropdownXPath);
                    if (!dropdownButton) throw new Error('Bouton du menu déroulant introuvable.');
                    
                    await dropdownButton.click();
                    console.log('Menu déroulant ouvert.');

                    const journeeTextToFind = `Journée ${String(journee_number).padStart(2, '0')}`;
                    const listItemXPath = `//li[contains(., "${journeeTextToFind}")]`;
                    
                    await page.waitForXPath(listItemXPath, { visible: true });
                    const [journeeListItem] = await page.$x(listItemXPath);
                    if (!journeeListItem) throw new Error(`Impossible de trouver la journée : '${journeeTextToFind}'`);

                    await journeeListItem.click();
                    console.log(`Journée '${journeeTextToFind}' sélectionnée.`);

                    const matchContainerSelector = 'a[class*="Calendarstyles__StyledLink"]';
                    await page.waitForSelector(matchContainerSelector, { visible: true, timeout: 10000 });
                    console.log('Le contenu des matchs a été rechargé.');

                    const content = await page.content();
                    console.log(`HTML final récupéré (${content.length} caractères).`);
                    return content;

                } catch (error) {
                    console.error('Erreur dans le script Puppeteer:', error.message);
                    const errorContent = await page.content();
                    return { error: error.message, html: errorContent };
                }
            }
        """)
        
        api_url = f"https://production-sfo.browserless.io/function?token={BROWSERLESS_TOKEN}"
        headers = { 'Content-Type': 'application/json' }
        data = {
            "code": puppeteer_script,
            "context": {
                "LNH_URL": LNH_URL,
                "journee_number": journee_number
            }
        }

        print("\n" + "="*25 + " PAYLOAD POUR BROWSERLESS " + "="*25)
        print(f"URL de l'API: {api_url}")
        print("--- SCRIPT JS ENVOYÉ (repr) ---")
        print(repr(data['code']))
        print("---------------------------------")
        print("="*70 + "\n")
        
        try:
            response = requests.post(api_url, headers=headers, json=data, timeout=60)
            response.raise_for_status()
            
            result = response.json()
            if isinstance(result, dict) and 'error' in result:
                print(f"❌ Erreur retournée par l'exécution du script Puppeteer: {result['error']}")
                return f"Une erreur est survenue lors du scraping sur le site : {result['error']}"

            page_source = result
            print("✅ (API /function) Succès ! HTML final récupéré.")
            
            soup = BeautifulSoup(page_source, 'html.parser')
            
            match_elements = soup.select('a[class*="Calendarstyles__StyledLink"]')
            
            if not match_elements:
                return f"Aucun match trouvé pour la journée {journee_number}. La structure du site a peut-être changé."

            scraped_matches = []
            for match_element in match_elements:
                teams = match_element.select('span[class*="TeamName"]')
                scores = match_element.select('div[class*="Score"]')
                
                if len(teams) == 2 and len(scores) == 2:
                    scraped_matches.append({
                        "team1": teams[0].get_text(strip=True), 
                        "team2": teams[1].get_text(strip=True),
                        "score1": scores[0].get_text(strip=True),
                        "score2": scores[1].get_text(strip=True),
                    })
            
            if not scraped_matches:
                return f"La journée {journee_number} a été trouvée, mais le format des matchs semble avoir changé."

            print(f"✅ Total de {len(scraped_matches)} matches scrapés avec succès.")
            return scraped_matches

        except requests.exceptions.HTTPError as e:
            error_text = e.response.text
            if e.response.status_code == 400 and 'code is not a function' in error_text:
                error_message = ("L'API Browserless indique que le script JavaScript est mal formaté. "
                                 "Vérifiez les logs de la console (le problème est probablement des commentaires ou une syntaxe invalide dans la chaîne de caractères du script).")
                print(f"❌ ERREUR SPÉCIFIQUE DÉTECTÉE : {error_message}")
                return error_message
            else:
                error_message = f"Browserless a retourné une erreur HTTP {e.response.status_code}. Réponse : {error_text}"
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
        thinking_message = await ctx.send(f"🔍 **Recherche en cours...** Je consulte le site de la LNH pour les résultats de la journée n°{journee}.")
        
        matches_or_error = await self.bot.loop.run_in_executor(None, self._scrape_lnh_results, journee)
        
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
        embed.set_footer(text="Données extraites de lnh.fr")
        embed.set_thumbnail(url="https://www.lnh.fr/images/logos/logo-lnh-simple-512.png")
        
        await thinking_message.edit(content=None, embed=embed)

async def setup(bot):
    await bot.add_cog(EventsCog(bot))
