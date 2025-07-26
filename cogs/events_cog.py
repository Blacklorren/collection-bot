import os
import re
import time
from discord.ext import commands  
from selenium import webdriver
from selenium.webdriver.chrome.options import Options as ChromeOptions
from bs4 import BeautifulSoup

# URL cible
LNH_URL = "https://www.lnh.fr/liquimoly-starligue/calendrier"

class EventsCog(commands.Cog): 
    def __init__(self, bot):
        self.bot = bot
        # ... (le reste de votre __init__)

    def _scrape_lnh_results(self, journee_number: int) -> list | str:
        """
        Scrape la page LNH avec Browserless pour trouver les résultats d'une journée.
        Retourne une liste de matchs ou un message d'erreur (str).
        """
        BROWSERLESS_TOKEN = os.getenv('BROWSERLESS_TOKEN')
        if not BROWSERLESS_TOKEN:
            print("❌ ERREUR : Le token Browserless est manquant dans les variables d'environnement.")
            return "Erreur de configuration du bot : Le token Browserless est manquant."

        print(f"🌍 (SELENIUM) Connexion à Browserless pour la journée {journee_number}...")
        
        chrome_options = ChromeOptions()
           
        driver = None
        try:
            connection_url = f"https://production-sfo.browserless.io/webdriver?token={BROWSERLESS_TOKEN}"
            driver = webdriver.Remote(command_executor=connection_url)

            driver.get(LNH_URL)
            # Attendre que le contenu JavaScript se charge. 5 secondes est une attente "brute".
            # Une approche plus robuste utiliserait WebDriverWait, mais c'est parfait pour un test.
            time.sleep(5) 
            
            page_source = driver.page_source
            soup = BeautifulSoup(page_source, 'html.parser')
            
            print("✅ (SELENIUM) Page récupérée. Début du parsing avec BeautifulSoup.")
            
            # 1. Trouver le titre de la journée (ex: "Journée 1")
            # On utilise une expression régulière pour être flexible
            journee_header = soup.find('h2', string=re.compile(f'Journée {journee_number}', re.IGNORECASE))
            
            if not journee_header:
                return f"Désolé, je n'ai pas trouvé la journée n°{journee_number} sur la page."
            
            # 2. Trouver le conteneur des matchs qui suit directement ce titre
            journee_container = journee_header.find_next_sibling('div')
            
            if not journee_container:
                return f"Un problème est survenu lors de l'analyse de la page pour la journée {journee_number}."

            # 3. Sélectionner tous les blocs de match à l'intérieur de ce conteneur
            match_elements = journee_container.select('div[class^="Calendarstyles__StyledContainer"]')
            
            if not match_elements:
                return f"Aucun match trouvé pour la journée {journee_number}. Il est possible qu'elle n'ait pas encore eu lieu."

            scraped_matches = []
            for match_element in match_elements:
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
                    scraped_matches.append(match_data)
            
            return scraped_matches

        except Exception as e:
            print(f"❌ (SELENIUM) Une erreur est survenue : {e}")
            return "Une erreur technique est survenue lors du scraping de la page."
        finally:
            # Très important : toujours fermer la session du navigateur !
            if driver:
                driver.quit()
                print("👍 (SELENIUM) Connexion à Browserless fermée.")


    @commands.command(name='results')
    @commands.has_permissions(manage_guild=True) # Sécurité : réservé aux admins pour le test
    async def results_command(self, ctx, journee: int):
        """
        Scrape et affiche les résultats d'une journée de Liqui Moly Starligue.
        Utilisation : !results <numéro_de_la_journée>
        """
        
        # 1. Envoyer un message d'attente
        thinking_message = await ctx.send(f"🔍 **Recherche en cours...** Je consulte le site de la LNH pour les résultats de la journée n°{journee}.")

        # 2. Lancer le scraping (qui est une fonction bloquante) dans un thread séparé
        # pour ne pas geler le bot. C'est crucial.
        matches_or_error = await self.bot.loop.run_in_executor(
            None, self._scrape_lnh_results, journee
        )

        # 3. Traiter le résultat
        if isinstance(matches_or_error, str):
            # C'est un message d'erreur
            await thinking_message.edit(content=f"❌ **Erreur :** {matches_or_error}")
            return

        if not matches_or_error:
            # C'est une liste vide
            await thinking_message.edit(content=f"ℹ️ Aucun match trouvé pour la journée {journee}.")
            return

        # 4. Construire un bel embed avec les résultats
        embed = discord.Embed(
            title=f"🏆 Résultats - Journée {journee}",
            color=0x006eff # Un bleu LNH
        )
        
        description = []
        for match in matches_or_error:
            # Déterminer le gagnant pour le mettre en gras
            try:
                score1 = int(match['score1'])
                score2 = int(match['score2'])
                if score1 > score2:
                    team1_display, team2_display = f"**{match['team1']}**", match['team2']
                elif score2 > score1:
                    team1_display, team2_display = match['team1'], f"**{match['team2']}**"
                else: # Match nul
                    team1_display, team2_display = match['team1'], match['team2']
            except (ValueError, TypeError): # Si scores sont "N/A"
                team1_display, team2_display = match['team1'], match['team2']
            
            description.append(
                f"{team1_display} `{match['score1']} - {match['score2']}` {team2_display}"
            )

        embed.description = "\n".join(description)
        embed.set_footer(text="Résultats scrapés depuis lnh.fr")
        
        # 5. Modifier le message d'attente pour afficher le résultat final
        await thinking_message.edit(content=None, embed=embed)

async def setup(bot):
    """Fonction requise par discord.py pour charger le Cog."""
    await bot.add_cog(EventsCog(bot))
