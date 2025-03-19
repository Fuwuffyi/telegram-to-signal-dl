import os
import json
import shutil
import asyncio
import logging
import aiohttp
from pathlib import Path
from dotenv import load_dotenv

from telegram import Update, Bot
from telegram.ext import filters, MessageHandler, ApplicationBuilder, ContextTypes, CommandHandler

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Constants
DOWNLOADS_DIR = Path("downloads")
STICKER_FILE_SUFFIX_LENGTH = 3
MESSAGES = {
    "start": "Connection established. Send me a sticker to download its pack!",
    "no_pack": "This sticker is not part of a pack.",
    "gathering_info": "🔍 Gathering sticker pack information...",
    "downloading": "⬇️ Downloading {pack_title} ({pack_name})...",
    "creating_archive": "🗜 Creating compressed archive...",
    "archive_caption": "📦 {pack_title} Sticker Pack",
    "error": "❌ An error occurred while processing the sticker pack."
}

async def start(update: Update, _: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(MESSAGES["start"])

async def download_sticker(session: aiohttp.ClientSession, url: str, path: Path) -> None:
    try:
        async with session.get(url) as response:
            if response.status == 200:
                content = await response.read()
                path.write_bytes(content)
                logger.info(f"Downloaded sticker: {path.name}")
            else:
                logger.error(f"Failed to download {url} - Status: {response.status}")
    except Exception as e:
        logger.error(f"Error downloading {url}: {str(e)}")

def save_pack_metadata(pack_dir: Path, emoji_mapping: dict, pack_title: str) -> None:
    emoji_file = pack_dir / "emoji.json"
    if not emoji_file.exists():
        emoji_file.write_text(json.dumps(emoji_mapping, indent=4, ensure_ascii=False), encoding="utf-8")
    title_file = pack_dir / "title.txt"
    if not title_file.exists():
        title_file.write_text(pack_title, encoding="utf-8")

async def download_stickers(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        # Check if the message contains a sticker
        sticker = update.message.sticker
        if not sticker.set_name:
            await update.message.reply_text(MESSAGES["no_pack"])
            return
        # Get sticker pack information
        await update.message.reply_text(MESSAGES["gathering_info"])
        sticker_set = await context.bot.get_sticker_set(sticker.set_name)
        pack_name = sticker_set.name
        pack_title = sticker_set.title
        # Create download directory
        pack_dir = DOWNLOADS_DIR / pack_name
        pack_dir.mkdir(parents=True, exist_ok=True)
        # Prepare sticker downloads
        await update.message.reply_text(MESSAGES["downloading"].format(pack_title=pack_title, pack_name=pack_name))
        emoji_mapping = {}
        download_tasks = []
        # Download stickers and save metadata
        for idx, sticker in enumerate(sticker_set.stickers):
            file_suffix = f"{idx:0{STICKER_FILE_SUFFIX_LENGTH}d}"
            emoji_mapping[file_suffix] = sticker.emoji
            sticker_path = pack_dir / f"{file_suffix}.webp"
            # Skip if the sticker already exists
            if not sticker_path.exists():
                try:
                    sticker_file = await context.bot.get_file(sticker.file_id)
                    download_tasks.append((sticker_file.file_path, sticker_path))
                except Exception as e:
                    logger.error(f"Error processing sticker {idx}: {str(e)}")
        # Download stickers concurrently
        if download_tasks:
            async with aiohttp.ClientSession() as session:
                tasks = [
                    download_sticker(session, url, path)
                    for url, path in download_tasks
                ]
                await asyncio.gather(*tasks)
        # Save metadata
        save_pack_metadata(pack_dir, emoji_mapping, pack_title)
        # Create the zip archive
        await update.message.reply_text(MESSAGES["creating_archive"])
        archive_path = pack_dir.with_suffix(".zip")
        shutil.make_archive(str(pack_dir), "zip", str(pack_dir))
        # Send the archive
        await update.message.reply_document(
            document=open(archive_path, "rb"),
            caption=MESSAGES["archive_caption"].format(pack_title=pack_title)
        )
        logger.info(f"Sent archive for pack: {pack_name}")
    except Exception as e:
        logger.error(f"Error processing sticker pack: {str(e)}")
        await update.message.reply_text(MESSAGES["error"])

if __name__ == "__main__":
    # Create the bot
    load_dotenv()
    bot_token = os.getenv("BOT_TOKEN")
    application = ApplicationBuilder().token(bot_token).build()
    # Register handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.Sticker.ALL & ~filters.COMMAND, download_stickers))
    # Run the bot
    application.run_polling()