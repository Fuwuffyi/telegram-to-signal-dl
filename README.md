# Telegram sticker downloader
A simple telegram bot written in python to download a sticker set.
### Instructions
- Create a telegram bot using [BotFather](https://t.me/BotFather)
- Create a `.env` file copying the template, replacing the `BOT_TOKEN` with the bot token provided by [BotFather](https://t.me/BotFather)
- Create a python virtual environment using `python -m venv ./venv` and enter it using `./venv/Scripts/activate`
- Install the bot's dependencies using `pip install -r requirements.txt`
- Run the bot simply by `python main.py`
You are now free to send a sticker to the bot and receive a zipped sticker pack from it.
### Converting sticker packs to signal
**WIP**  
This bot also allows for converting telegram sticker packs to signal sticker packs. This requires the environment variables `SIGNAL_USERNAME` and `SIGNAL_PASSWORD` to be set, as there isn't a way to upload sticker packs without an account on signal currently.