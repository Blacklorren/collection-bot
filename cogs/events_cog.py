import os
import re
import requests  # Remplaçant de Selenium
import json      # Pour préparer la requête API
from discord.ext import commands
from bs4 import BeautifulSoup

# --- CONFIGURATION ---
LNH_URL = "https://www.lnh.fr/liquimoly-starligue/calendrier"

class EventsCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    def _scrape_lnh_results(self, journee_number: int) -> list | str:
        """
        Scrape la page LNH en exécutant un script distant sur Browserless
        qui clique sur le bon menu déroulant pour afficher la bonne journée.
        """
        BROWSERLESS_TOKEN = os.getenv('BROWSERLESS_TOKEN')
        if not BROWSERLESS_TOKEN:
            print("❌ ERREUR : Le token Browserless est manquant dans les variables d'environnement.")
            return "Erreur de configuration du bot : Le token Browserless est manquant."

        print(f"🌍 (API /function) Exécution du script de clic distant pour la journée {journee_number}...")

        # Le script JavaScript (Puppeteer) que Browserless va exécuter pour nous.
        # Il reproduit exactement les actions d'un utilisateur.
        puppeteer_script = f"""
        async ({{ page, context }}) => {{
            // 1. Aller sur la page
            await page.goto(context.LNH_URL);
            
            // 2. Attendre que le bouton principal du menu "Toutes les journées" soit présent et cliquable
            const dropdownButtonSelector = 'button:has-text("Toutes les journées")';
            await page.waitForSelector(dropdownButtonSelector);
            
            // 3. Cliquer sur ce bouton pour ouvrir le menu
            await page.click(dropdownButtonSelector);
            
            // 4. Construire le sélecteur pour le lien de la journée voulue (ex: Journée 1)
            // L'URL dans le 'href' est la manière la plus fiable de le trouver.
            const journeeLinkSelector = 'a[href="/liquimoly-starligue/calendrier?day={journee_number}"]';
            await page.waitForSelector(journeeLinkSelector);
            
            // 5. Cliquer sur le lien de la journée
            await page.click(journeeLinkSelector);
            
            // 6. Attendre que la page se mette à jour et que les nouveaux matchs s'affichent
            await page.waitForSelector('div[class^="Calendarstyles__StyledContainer"]');
            await page.waitForTimeout(1500); // Petite attente de sécurité pour la fin du rendu

            // 7. Renvoyer le code HTML final de la page, prêt à être parsé
            return await page.content();
        }}
        """

        # L'URL de l'API /function de Browserless
         api_url = f"https://production-sfo.browserless.io/function?token={BROWSERLESS_TOKEN}"
        
        headers = { 'Content-Type': 'application/json' }
        
        # Le corps de notre requête : le script et le contexte (les variables pour le script)
        data = {
            "code": puppeteer_script,
            "context": { "LNH_URL": LNH_URL }
        }

        try:
            # Envoi de la requête à Browserless
            response = requests.post(api_url, headers=headers, data=json.dumps(data), timeout=45)
            response.raise_for_status() # Lève une erreur si le statut est 4xx ou 5xx
            
            # Le HTML final est dans la réponse
            page_source = response.text
            
            print("✅ (API /function) HTML final récupéré. Début du parsing avec BeautifulSoup.")
            
            # --- Le code de parsing ci-dessous reste valide ---
            soup = BeautifulSoup(page_source, 'html.parser')
            
            match_elements = soup.select('div[class^="Calendarstyles__StyledContainer"]')
            
            if not match_elements:
                return f"Aucun match trouvé pour la journée {journee_number} après le clic. Le site a peut-être changé."

            scraped_matches = []
            for match_element in match_elements:
                team1_elem = match_element.select_one('div[class*="StyledTeamContainer"]:nth-of-type(1) span')
                team2_elem = match_element.select_one('div[class*="StyledTeamContainer"]:nth-of-type(3) span')
                score1_elem = match_element.select_one('div[class*="StyledScore"]:nth-of-type(1)')
                score2_elem = match_element.select_one('div[class*="StyledScore"]:nth-of-type(2)')
                
                if team1_elem and team2_elem:
                    scraped_matches.append({
                        "team1": team1_elem.get_text(strip=True),
                        "team2": team2_elem.get_text(strip=True),
                        "score1": score1_elem.get_text(strip=True) if score1_elem else "N/A",
                        "score2": score2_elem.get_text(strip=True) if score2_elem else "N/A",
                    })
            
            if not scraped_matches:
                return f"La journée {journee_number} a bien été sélectionnée, mais aucun match n'est affiché."

            return scraped_matches

        except requests.exceptions.HTTPError as e:
            error_text = e.response.text
            print(f"❌ (API) Erreur HTTP de Browserless : {e.response.status_code} - {error_text}")
            if "timeout" in error_text.lower():
                return "Le script de scraping a mis trop de temps à s'exécuter. Le site est peut-être lent."
            return f"Browserless a retourné une erreur {e.response.status_code}. Vérifiez le token ou le statut du service."
        except requests.exceptions.RequestException as e:
            print(f"❌ (API) Erreur de connexion : {e}")
            return "Impossible de contacter le service de scraping."

    @commands.command(name='results')
    @commands.has_permissions(manage_guild=True)
    async def results_command(self, ctx, journee: int):
        """
        Scrape et affiche les résultats d'une journée de Liqui Moly Starligue.
        Utilisation : !results <numéro_de_la_journée>
        """
        thinking_message = await ctx.send(f"🔍 **Recherche en cours...** Je consulte le site de la LNH pour les résultats de la journée n°{journee}.")

        matches_or_error = await self.bot.loop.run_in_executor(
            None, self._scrape_lnh_results, journee
        )

        if isinstance(matches_or_error, str):
            await thinking_message.edit(content=f"❌ **Erreur :** {matches_or_error}")
            return

        if not matches_or_error:
            await thinking_message.edit(content=f"ℹ️ Aucun match trouvé pour la journée {journee}.")
            return

        embed = discord.Embed(
            title=f"🏆 Résultats - Journée {journee}",
            color=0x006eff
        )
        
        description = []
        for match in matches_or_error:
            try:
                score1 = int(match['score1'])
                score2 = int(match['score2'])
                if score1 > score2:
                    team1_display, team2_display = f"**{match['team1']}**", match['team2']
                elif score2 > score1:
                    team1_display, team2_display = match['team1'], f"**{match['team2']}**"
                else:
                    team1_display, team2_display = match['team1'], match['team2']
            except (ValueError, TypeError):
                team1_display, team2_display = match['team1'], match['team2']
            
            description.append(
                f"{team1_display} `{match['score1']} - {match['score2']}` {team2_display}"
            )

        embed.description = "\n".join(description)
        embed.set_footer(text="Résultats scrapés depuis lnh.fr")
        
        await thinking_message.edit(content=None, embed=embed)

async def setup(bot):
    """Fonction requise par discord.py pour charger le Cog."""
    await bot.add_cog(EventsCog(bot))
