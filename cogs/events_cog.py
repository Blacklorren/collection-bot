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
        BROWSERLESS_TOKEN = os.getenv('BROWSERLESS_TOKEN')
        if not BROWSERLESS_TOKEN:
            print("❌ ERREUR : Le token Browserless est manquant.")
            return "Erreur de configuration du bot : Le token Browserless est manquant."

        print(f"🌍 (API /function) Lancement du scraping pour la journée n°{journee_number}...")

        # Script JS avec journal de bord pour un débogage précis
        puppeteer_script = textwrap.dedent("""
            async ({ page, context }) => {
                const { LNH_URL, journee_number } = context;
                let step = 'Initialisation'; // Variable pour suivre l'étape en cours

                try {
                    step = '1. Navigation vers la page';
                    console.log(step);
                    await page.goto(LNH_URL, { waitUntil: 'networkidle2', timeout: 30000 });
                    
                    step = '2. Gestion de la bannière de cookies';
                    console.log(step);
                    try {
                        const cookieButtonSelector = '#axeptio_btn_acceptAll';
                        await page.waitForSelector(cookieButtonSelector, { timeout: 5000 });
                        await page.click(cookieButtonSelector);
                        console.log(' -> Bannière de cookies acceptée.');
                        await page.waitForTimeout(500);
                    } catch (e) {
                        console.log(" -> Bannière de cookies non trouvée ou déjà gérée.");
                    }

                    step = '3. Attente et clic sur le menu déroulant';
                    console.log(step);
                    const dropdownXPath = "//button[contains(., 'Toutes les journées')]";
                    await page.waitForXPath(dropdownXPath, { timeout: 10000 });
                    const [dropdownButton] = await page.$x(dropdownXPath);
                    if (!dropdownButton) throw new Error('Élément du menu déroulant introuvable.');
                    await dropdownButton.click();
                    console.log(' -> Menu déroulant cliqué.');

                    step = '4. Sélection de la journée';
                    const journeeTextToFind = `Journée ${String(journee_number).padStart(2, '0')}`;
                    const listItemXPath = `//li[contains(., "${journeeTextToFind}")]`;
                    console.log(`${step} (recherche de "${journeeTextToFind}")`);
                    await page.waitForXPath(listItemXPath, { visible: true, timeout: 10000 });
                    const [journeeListItem] = await page.$x(listItemXPath);
                    if (!journeeListItem) throw new Error(`Élément de la journée '${journeeTextToFind}' introuvable.`);
                    await journeeListItem.click();
                    console.log(` -> Journée '${journeeTextToFind}' cliquée.`);

                    step = '5. Attente du rechargement des matchs';
                    console.log(step);
                    const matchContainerSelector = 'a[class*="Calendarstyles__StyledLink"]';
                    await page.waitForSelector(matchContainerSelector, { visible: true, timeout: 15000 });
                    console.log(' -> Conteneur des matchs rechargé.');

                    step = '6. Récupération du HTML final';
                    console.log(step);
                    const content = await page.content();
                    return content;

                } catch (error) {
                    console.error(`ERREUR FATALE pendant le script Puppeteer.`);
                    console.error(` -> Étape de l'échec: ${step}`);
                    console.error(` -> Message d'erreur: ${error.message}`);
                    const errorContent = await page.content().catch(() => 'Impossible de récupérer le HTML après erreur.');
                    // Retourne un objet d'erreur détaillé
                    return { 
                        error: `Échec à l'étape : ${step}`, 
                        errorMessage: error.message,
                        htmlOnFailure: errorContent 
                    };
                }
            }
        """).lstrip()
        
        api_url = f"https://production-sfo.browserless.io/function?token={BROWSERLESS_TOKEN}"
        headers = { 'Content-Type': 'application/json' }
        data = {"code": puppeteer_script,"context": {"LNH_URL": LNH_URL,"journee_number": journee_number}}

        print("\n" + "="*25 + " PAYLOAD ENVOYÉ À BROWSERLESS " + "="*25)
        print(repr(data['code']))
        print("="*78 + "\n")
        
        try:
            response = requests.post(api_url, headers=headers, json=data, timeout=60)
            
            # --- LOGS DE DÉBOGAGE MAXIMUM DE LA RÉPONSE ---
            print("\n" + "="*25 + " RÉPONSE REÇUE DE BROWSERLESS " + "="*25)
            print(f"Status Code: {response.status_code}")
            print(f"Headers: {response.headers}")
            print("--- Contenu brut de la réponse ---")
            print(response.text)
            print("------------------------------------")
            print("="*79 + "\n")
            # --- FIN DES LOGS DE DÉBOGAGE ---

            response.raise_for_status()
            
            # On tente de parser la réponse comme du JSON
            try:
                result = response.json()
                if isinstance(result, dict) and 'error' in result:
                    error_details = result.get('errorMessage', 'Aucun détail.')
                    print(f"❌ Erreur DÉTAILLÉE retournée par Puppeteer: {result['error']} | Détails: {error_details}")
                    return f"Erreur lors du scraping : {result['error']}"
                page_source = result
            except json.JSONDecodeError:
                # Si la réponse n'est pas du JSON, c'est probablement le code HTML directement
                print("✅ Réponse non-JSON, interprétée comme le code HTML de la page.")
                page_source = response.text

            print("✅ Scraping terminé, analyse du HTML...")
            soup = BeautifulSoup(page_source, 'html.parser')
            match_elements = soup.select('a[class*="Calendarstyles__StyledLink"]')
            
            if not match_elements: return f"Aucun match trouvé pour la journée {journee_number}."
            scraped_matches = []
            for match_element in match_elements:
                teams = match_element.select('span[class*="TeamName"]')
                scores = match_element.select('div[class*="Score"]')
                if len(teams) == 2 and len(scores) == 2:
                    scraped_matches.append({"team1": teams[0].get_text(strip=True),"team2": teams[1].get_text(strip=True),"score1": scores[0].get_text(strip=True),"score2": scores[1].get_text(strip=True)})
            
            if not scraped_matches: return f"La journée {journee_number} a été trouvée, mais le format des matchs est inattendu."
            return scraped_matches

        except requests.exceptions.HTTPError as e:
            return f"Browserless a retourné une erreur HTTP {e.response.status_code}. Le contenu de la réponse a été logué dans la console."
        except requests.exceptions.RequestException as e:
            return f"Impossible de contacter le service de scraping : {e}"
        except Exception as e:
            return f"Une erreur inattendue est survenue : {e}"

    @commands.command(name='results')
    @commands.has_permissions(manage_guild=True)
    async def results_command(self, ctx, journee: int):
        thinking_message = await ctx.send(f"🔍 **Recherche en cours...** Je consulte le site de la LNH pour les résultats de la journée n°{journee}.")
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
