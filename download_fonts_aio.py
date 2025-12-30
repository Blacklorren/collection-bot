import aiohttp
import asyncio
import os

async def download_font(url, filename):
    print(f"Downloading {url}...")
    headers = {"User-Agent": "Mozilla/5.0"}
    async with aiohttp.ClientSession(headers=headers) as session:
        async with session.get(url) as response:
            if response.status == 200:
                with open(filename, 'wb') as f:
                    f.write(await response.read())
                print(f"Success: {filename}")
            else:
                print(f"Error {response.status} downloading {filename}")

async def main():
    base_url = "https://github.com/google/fonts/raw/main/apache/roboto/static/"
    fonts = ["Roboto-Bold.ttf", "Roboto-Regular.ttf"]
    
    tasks = []
    for font in fonts:
        url = base_url + font
        target = os.path.join("utils", font)
        tasks.append(download_font(url, target))
    
    await asyncio.gather(*tasks)

if __name__ == "__main__":
    if os.name == 'nt':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
