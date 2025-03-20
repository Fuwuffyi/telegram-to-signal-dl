import os
import json
import shutil
import asyncio
import logging
import aiohttp
from pathlib import Path
from dotenv import load_dotenv

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import filters, MessageHandler, ApplicationBuilder, ContextTypes, CommandHandler, CallbackQueryHandler

from signalstickers_client import StickersClient
from signalstickers_client.models import LocalStickerPack, Sticker

# Configure logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Constants
STICKER_FILE_SUFFIX_LENGTH: int = 3
DOWNLOADS_DIR: Path = Path("downloads")
THUMBNAIL_NAME: str = "thumbnail.webp"
SIGNAL_CACHE: str = "signal_cache.json"
MESSAGES: dict[str, str] = {
    "start": "Connection established. Send me a sticker to download its pack! Use /help for instructions.",
    "no_pack": "This sticker is not part of a pack.",
    "gathering_info": "🔍 Gathering sticker pack information...",
    "downloading": "⬇️ Downloading {pack_title} ({pack_name})...",
    "creating_archive": "🗜 Creating compressed archive...",
    "archive_caption": "📦 {pack_title} Sticker Pack",
    "signal_processing": "⬆️ Uploading the pack to signal...",
    "signal_upload": "🚀 Sticker pack uploaded to Signal: {signal_url}\n⬆️ Consider adding at https://signalstickers.org/contribute if not present in their collection",
    "error": "❌ An error occurred while processing the sticker pack.",
    "signal_credentials_missing": "⚠️ Signal upload disabled - missing credentials in environment",
    "author_prompt": "📝 Please enter the author name for this sticker pack:",
    "author_empty": "⚠️ Author name cannot be empty. Please try again."
}

# Global state
user_modes: dict[str, bool] = {}
user_states: dict = {}
signal_enabled: bool = False

async def start(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(MESSAGES["start"])

async def help_cmd(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    help_text: str = (
        "Bot functionality:\n\n"
        "- /start: Initialize bot\n"
        "- /help: Show this message\n"
        "- /mode: Toggle download/upload modes\n"
        "- Send sticker: Download pack"
    )
    await update.message.reply_text(help_text)

async def mode_command(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    user_id: int = update.effective_user.id
    current_mode: bool = user_modes.get(user_id, False)
    mode_text: str = "Upload to Signal" if current_mode else "Download only"
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("Toggle Mode", callback_data="toggle_upload")]])
    await update.message.reply_text(f"Current mode: {mode_text}", reply_markup=keyboard)

async def toggle_upload_callback(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    user_id: int = query.from_user.id
    current_mode: bool = user_modes.get(user_id, False)
    # If signal is not enabled, inform the user and set to download onloy mode
    if not signal_enabled:
        await query.edit_message_text(MESSAGES["signal_credentials_missing"])
        user_modes[user_id] = False
        return
    # Toggle mode
    user_modes[user_id] = not current_mode
    new_mode_text: str = "Upload to Signal" if user_modes[user_id] else "Download only"
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("Toggle Mode", callback_data="toggle_upload")]])
    await query.edit_message_text(f"Mode changed to: {new_mode_text}", reply_markup=keyboard)

def read_signal_cache() -> dict:
    if not Path(SIGNAL_CACHE).exists():
        return {}
    try:
        with open(SIGNAL_CACHE, 'r') as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        logger.error(f"Cache read error: {e}")
        return {}

def write_signal_cache(data: dict) -> None:
    try:
        with open(SIGNAL_CACHE, 'w') as f:
            json.dump(data, f, indent=2)
    except IOError as e:
        logger.error(f"Cache write error: {e}")

async def upload_to_signal(pack_dir: Path) -> str | None:
    # Load metadata
    metadata_file = pack_dir / "metadata.json"
    metadata = json.loads(metadata_file.read_text(encoding="utf-8"))
    # Create pack
    pack = LocalStickerPack()
    pack.title = metadata.get("title")
    pack.author = metadata.get("author", "Telegram Converter Bot")
    # Add stickers
    for file_suffix, emoji in metadata.get("emojis").items():
        sticker = Sticker()
        sticker.id = pack.nb_stickers
        sticker.emoji = emoji
        sticker_path = pack_dir / f"{file_suffix}.webp"
        with open(sticker_path, "rb") as f:
            sticker.image_data = f.read()
        pack._addsticker(sticker)
    # Set cover image
    cover_path = pack_dir / THUMBNAIL_NAME
    cover = Sticker()
    cover.id = pack.nb_stickers
    try:
        with open(cover_path, "rb") as f:
            cover.image_data = f.read()
    except FileNotFoundError:
        cover.image_data = pack.stickers[0].image_data[:]
    pack.cover = cover
    # Upload to Signal
    try:
        async with StickersClient(os.getenv("SIGNAL_UUID"), os.getenv("SIGNAL_PASSWORD")) as client:
            pack_id, pack_key = await client.upload_pack(pack)
    except Exception as e:
        logger.error(f"Signal upload failed: {e}")
        return None
    return f"https://signal.art/addstickers/#pack_id={pack_id}&pack_key={pack_key}"

async def download_sticker(session: aiohttp.ClientSession, url: str, path: Path) -> bool:
    try:
        async with session.get(url) as response:
            if response.status == 200:
                content = await response.read()
                path.write_bytes(content)
                logger.debug(f"Downloaded: {path.name}")
                return True
            logger.error(f"Download failed: {url} ({response.status})")
    except Exception as e:
        logger.error(f"Download error: {url} - {str(e)}")
    return False

async def download_pack_assets(context: ContextTypes.DEFAULT_TYPE, sticker_set, pack_dir: Path) -> None:
    needed_stickers: list = []
    emoji_mapping: dict = {}
    # Collect regular stickers
    for idx, sticker in enumerate(sticker_set.stickers):
        file_suffix = f"{idx:0{STICKER_FILE_SUFFIX_LENGTH}d}"
        emoji_mapping[file_suffix] = sticker.emoji
        sticker_path = pack_dir / f"{file_suffix}.webp"
        if not sticker_path.exists():
            needed_stickers.append((sticker.file_id, sticker_path))
    # Collect thumbnail if available
    thumbnail_path = pack_dir / THUMBNAIL_NAME
    if sticker_set.thumbnail and not thumbnail_path.exists():
        needed_stickers.append((sticker_set.thumbnail.file_id, thumbnail_path))
    if not needed_stickers:
        return
    # Get file URLs in parallel
    file_ids = [fid for fid, _ in needed_stickers]
    sticker_files = await asyncio.gather(*[context.bot.get_file(fid) for fid in file_ids])
    # Download all assets in parallel
    async with aiohttp.ClientSession() as session:
        tasks = [download_sticker(session, sf.file_path, path) for sf, (_, path) in zip(sticker_files, needed_stickers)]
        await asyncio.gather(*tasks)
    # Save metadata
    metadata = {
        "title": sticker_set.title,
        "name": sticker_set.name,
        "emojis": emoji_mapping
    }
    (pack_dir / "metadata.json").write_text(json.dumps(metadata, indent=4, ensure_ascii=False), encoding="utf-8")

async def process_sticker_pack(update: Update, context: ContextTypes.DEFAULT_TYPE) -> tuple:
    sticker = update.message.sticker
    if not sticker.set_name:
        await update.message.reply_text(MESSAGES["no_pack"])
        raise ValueError("Sticker not part of a pack")
    # Get sticker pack information
    await update.message.reply_text(MESSAGES["gathering_info"])
    sticker_set = await context.bot.get_sticker_set(sticker.set_name)
    return sticker_set, sticker_set.name, sticker_set.title

async def handle_sticker_pack(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    print(user_states)
    try:
        # Get pack information
        sticker_set, pack_name, pack_title = await process_sticker_pack(update, context)
        pack_dir = DOWNLOADS_DIR / pack_name
        pack_dir.mkdir(parents=True, exist_ok=True)
        # Download assets
        await update.message.reply_text(MESSAGES["downloading"].format(pack_title=pack_title, pack_name=pack_name))
        await download_pack_assets(context, sticker_set, pack_dir)
        # Create archive
        await update.message.reply_text(MESSAGES["creating_archive"])
        loop = asyncio.get_running_loop()
        archive_path = await loop.run_in_executor(None, shutil.make_archive, str(pack_dir), "zip", str(pack_dir))
        # Send to user
        with open(archive_path, 'rb') as f:
            await update.message.reply_document(document=f, caption=MESSAGES["archive_caption"].format(pack_title=pack_title))
        # Handle Signal upload
        user_id = update.effective_user.id
        if signal_enabled and user_modes.get(user_id, False):
            cache = read_signal_cache()
            signal_url = cache.get(pack_name)
            if not signal_url:
                # Prompt user for author if not present
                metadata_file = pack_dir / "metadata.json"
                metadata = json.loads(metadata_file.read_text(encoding="utf-8"))
                if "author" not in metadata:
                    await update.message.reply_text(MESSAGES["author_prompt"])
                    user_states[user_id] = {'state': 'awaiting_author', 'pack_dir': pack_dir}
                    return # Exit to wait for user input
                await update.message.reply_text(MESSAGES["signal_processing"])
                signal_url = await upload_to_signal(pack_dir)
                if signal_url:
                    cache[pack_name] = signal_url
                    write_signal_cache(cache)
            if signal_url:
                await update.message.reply_text(MESSAGES["signal_upload"].format(signal_url=signal_url))
    except Exception as e:
        logger.error(f"Processing error: {str(e)}", exc_info=True)
        await update.message.reply_text(MESSAGES["error"])


async def handle_author_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id: id = update.effective_user.id
    user_state: dict = user_states.get(user_id)
    if not user_state or user_state.get('state') != 'awaiting_author':
        return  # Ignore if not awaiting author
    author: str = update.message.text.strip()
    if not author:
        await update.message.reply_text(MESSAGES["author_empty"])
        return
    pack_dir: str = user_state['pack_dir']
    del user_states[user_id]  # Clear state immediately
    try:
        # Update metadata with author
        metadata_file = pack_dir / "metadata.json"
        metadata = json.loads(metadata_file.read_text(encoding="utf-8"))
        metadata['author'] = author
        metadata_file.write_text(json.dumps(metadata, indent=4, ensure_ascii=False), encoding="utf-8")
        # Proceed with upload
        await update.message.reply_text(MESSAGES["signal_processing"])
        signal_url = await upload_to_signal(pack_dir)
        if signal_url:
            # Update cache
            cache = read_signal_cache()
            cache[pack_dir.name] = signal_url
            write_signal_cache(cache)
            await update.message.reply_text(MESSAGES["signal_upload"].format(signal_url=signal_url))
        else:
            await update.message.reply_text(MESSAGES["error"])
    except Exception as e:
        logger.error(f"Author input handling failed: {e}")
        await update.message.reply_text(MESSAGES["error"])

if __name__ == "__main__":
    # Create the bot
    load_dotenv()
    signal_enabled: bool = all([os.getenv("SIGNAL_UUID"), os.getenv("SIGNAL_PASSWORD")])
    application = ApplicationBuilder().token(os.getenv("BOT_TOKEN")).build()
    # Register handlers
    handlers: list = [
        CommandHandler("start", start),
        CommandHandler("help", help_cmd),
        CommandHandler("mode", mode_command),
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_author_input),
        MessageHandler(filters.Sticker.ALL, handle_sticker_pack),
        CallbackQueryHandler(toggle_upload_callback, pattern="^toggle_upload$")
    ]
    for handler in handlers:
        application.add_handler(handler)
    # Run the bot
    application.run_polling()
