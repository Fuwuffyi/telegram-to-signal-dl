import os
import json
import shutil
import logging
import requests
from telegram import Update, Bot
from telegram.ext import filters, MessageHandler, ApplicationBuilder, ContextTypes, CommandHandler

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await context.bot.send_message(chat_id=update.effective_chat.id, text="Connection set up. Send a sticker to download the pack it.")

async def download_stickers(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Get the pack from the sticker
    await context.bot.send_message(chat_id=update.effective_chat.id, text=f"Gathering info for pack...")
    sticker_set_name = update.message.sticker.set_name
    sticker_set = await Bot.get_sticker_set(context.bot, sticker_set_name)
    # Get the pack's name and title
    set_name = sticker_set.name
    set_title = sticker_set.title
    await context.bot.send_message(chat_id=update.effective_chat.id, text=f"Downloading {set_name} - {set_title}...")
    # Create dir if not exists
    if not os.path.exists(f"downloads/{set_name}/"):
        os.makedirs(f"downloads/{set_name}/")
    # Loop over the pack's stickers
    emoji_dict = {}
    for (index, sticker) in enumerate(sticker_set.stickers):
        # Save the emoji related to each sticker
        index_str = f"{index:03d}"
        emoji_dict[index_str] = sticker.emoji
        # Save stickers to file if not exists
        if os.path.exists(f"downloads/{set_name}/{index_str}.webp"):
            continue
        sticker_url = (await Bot.get_file(context.bot, sticker)).file_path
        sticker_response = requests.get(sticker_url)
        with open(f"downloads/{set_name}/{index_str}.webp", "wb") as f:
            f.write(sticker_response.content)
    # Save the emojis to a json file
    with open(f"downloads/{set_name}/emoji.txt", "w", encoding="utf-8") as f:
        # Write the json file with indentation
        f.write(json.dumps(emoji_dict, indent=4, ensure_ascii=False))
    # Write the title file
    with open(f"downloads/{set_name}/title.txt", "w") as f:
        # Write the json file with indentation
        f.write(set_name)
    # Create a zip archive
    await context.bot.send_message(chat_id=update.effective_chat.id, text=f"Creating zip file for {set_name}...")
    archived = shutil.make_archive(f'downloads/{set_name}', 'zip', f'downloads/{set_name}/')
    # Send the zip archive to the user
    with open(archived, 'rb') as zip_file:
        await Bot.sendDocument(context.bot, chat_id=update.effective_chat.id, document=zip_file)

if __name__ == '__main__':
    # Create the bot
    application = ApplicationBuilder().token('6724118582:AAFkRb9DLmQDhyBJxGl4_FiAkdkJC2LRTHc').build()
    # Create the handlers for the commands
    start_handler = CommandHandler('start', start)
    download_stickers_handler = MessageHandler(filters.Sticker.ALL & (~filters.COMMAND), download_stickers)
    # Add the commands to the bot
    application.add_handler(start_handler)
    application.add_handler(download_stickers_handler)
    # Run the bot
    application.run_polling()