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

from signalstickers_client import StickersClient
from signalstickers_client.models import LocalStickerPack, Sticker

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Constants
DOWNLOADS_DIR: Path = Path("downloads")
STICKER_FILE_SUFFIX_LENGTH: int = 3
THUMBNAIL_NAME: str = "thumbnail.webp"
MESSAGES: dict[str, str] = {
    "start": "Connection established. Send me a sticker to download its pack!",
    "no_pack": "This sticker is not part of a pack.",
    "gathering_info": "🔍 Gathering sticker pack information...",
    "downloading": "⬇️ Downloading {pack_title} ({pack_name})...",
    "creating_archive": "🗜 Creating compressed archive...",
    "archive_caption": "📦 {pack_title} Sticker Pack",
    "signal_upload": "🚀 Sticker pack uploaded to Signal: {signal_url}\n⬆️ Consider adding the sticker pack at https://signalstickers.org/contribute if not already present",
    "error": "❌ An error occurred while processing the sticker pack."
}

async def start(update: Update, _: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(MESSAGES["start"])

async def upload_to_signal(signal_uuid: str, signal_passowrd: str, pack_dir: str) -> str:
    # Load pack metadata
    metadata_file: Path = pack_dir / "metadata.json"
    metadata: dict = json.loads(metadata_file.read_text(encoding="utf-8"))
    # Create a sticker pack
    pack = LocalStickerPack()
    pack.title = metadata["title"]
    pack.author = "Dummy" # TODO: Fixme somehow
    # Add the stickers to it
    for (file_suffix, emoji) in metadata["emojis"].items():
        sticker: Sticker = Sticker()
        sticker.id = pack.nb_stickers
        sticker.emoji = emoji
        sticker_path = pack_dir / f"{file_suffix}.webp"
        with open(sticker_path, "rb") as f_in:
            sticker.image_data = f_in.read()
        pack._addsticker(sticker)
    # Create the sticker cover
    cover: Sticker = Sticker()
    cover.id = pack.nb_stickers
    # Write the image data to the cover
    thumbnail_path: Path = pack_dir / THUMBNAIL_NAME
    if (thumbnail_path).exists():
        with open(thumbnail_path, "rb") as f_in:
            cover.image_data = f_in.read()
    else:
        # Default to first sticker if no thumbnail
        cover.image_data = pack.stickers[0].image_data[:]
    pack.cover = cover
    # Upload the pack to Signal using the client provided
    async with StickersClient(signal_uuid, signal_passowrd) as client:
        pack_id, pack_key = await client.upload_pack(pack)
    pack_url: str = f"https://signal.art/addstickers/#pack_id={pack_id}&pack_key={pack_key}"
    logger.info(f"Uploaded pack to signal: {pack_url}")
    return pack_url

async def download_sticker(session: aiohttp.ClientSession, url: str, path: Path) -> None:
    try:
        async with session.get(url) as response:
            if response.status == 200:
                content: bytearray = await response.read()
                path.write_bytes(content)
                logger.info(f"Downloaded sticker: {path.name}")
            else:
                logger.error(f"Failed to download {url} - Status: {response.status}")
    except Exception as e:
        logger.error(f"Error downloading {url}: {str(e)}")

def save_pack_metadata(pack_dir: Path, metadata: dict) -> None:
    metadata_path: Path = pack_dir / "metadata.json"
    metadata_path.write_text(json.dumps(metadata, indent=4, ensure_ascii=False), encoding="utf-8")
    logger.info(f"Saved metadata for pack: {pack_dir.name}")

async def download_and_zip_stickers(update: Update, context: ContextTypes.DEFAULT_TYPE, sticker_set: object, pack_name: str, pack_title: str) -> Path:
    pack_dir: Path = DOWNLOADS_DIR / pack_name
    pack_dir.mkdir(parents=True, exist_ok=True)
    # Prepare download tasks and emoji mapping
    emoji_mapping: dict = {}
    needed_stickers: list = []
    for idx, pack_sticker in enumerate(sticker_set.stickers):
        file_suffix: str = f"{idx:0{STICKER_FILE_SUFFIX_LENGTH}d}"
        emoji_mapping[file_suffix] = pack_sticker.emoji
        sticker_path: Path = pack_dir / f"{file_suffix}.webp"
        if not sticker_path.exists():
            needed_stickers.append((pack_sticker.file_id, sticker_path))
    # Add the thumbnail
    thumbnail_path: Path = pack_dir / THUMBNAIL_NAME
    if not (thumbnail_path).exists():
        if sticker_set.thumbnail.file_id:
            needed_stickers.append((sticker_set.thumbnail.file_id, thumbnail_path))
    # Get file URLs concurrently
    if needed_stickers:
        file_ids: list[int] = [fid for fid, _ in needed_stickers]
        try:
            sticker_files = await asyncio.gather(*[context.bot.get_file(fid) for fid in file_ids])
        except Exception as e:
            logger.error(f"Error getting file URLs: {str(e)}")
            raise
        download_tasks: list = [(sf.file_path, path) for sf, (_, path) in zip(sticker_files, needed_stickers)]
        # Download stickers concurrently
        async with aiohttp.ClientSession() as session:
            await asyncio.gather(*[download_sticker(session, url, path) for url, path in download_tasks])
    # Save metadata
    metadata = {
        "title": pack_title,
        "name": pack_name,
        "emojis": emoji_mapping
    }
    save_pack_metadata(pack_dir, metadata)
    # Create zip archive in thread pool
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, shutil.make_archive, str(pack_dir), "zip",str(pack_dir))
    return pack_dir.with_suffix(".zip")

async def process_sticker_pack(update: Update, context: ContextTypes.DEFAULT_TYPE) -> tuple:
    # Check if the message contains a sticker
    sticker = update.message.sticker
    if not sticker.set_name:
        await update.message.reply_text(MESSAGES["no_pack"])
        return None, None, None
    # Get sticker pack information
    await update.message.reply_text(MESSAGES["gathering_info"])
    sticker_set = await context.bot.get_sticker_set(sticker.set_name)
    return sticker_set, sticker_set.name, sticker_set.title

async def download_stickers(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        # Get sticker pack information
        sticker_set, pack_name, pack_title = await process_sticker_pack(update, context)
        if not sticker_set:
            return
        # Download stickers
        await update.message.reply_text(MESSAGES["downloading"].format(pack_title=pack_title,pack_name=pack_name))
        archive_path = await download_and_zip_stickers(update, context, sticker_set, pack_name, pack_title)
        # Send archive to user
        await update.message.reply_document(document=open(archive_path, "rb"),caption=MESSAGES["archive_caption"].format(pack_title=pack_title))
        logger.info(f"Sent archive for pack: {pack_name}")
        # Upload pack to signal if user set up
        signal_uuid = os.getenv("SIGNAL_UUID")
        signal_password = os.getenv("SIGNAL_PASSWORD")
        if signal_uuid and signal_password:
            signal_url: str = await upload_to_signal(signal_uuid, signal_password, DOWNLOADS_DIR / pack_name)
            await update.message.reply_text(MESSAGES["signal_upload"].format(signal_url=signal_url))
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