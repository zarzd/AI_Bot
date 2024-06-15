import asyncio
import atexit
import os
from pathlib import Path
from aiogram.types import FSInputFile
from openai import OpenAI
import logging
from aiogram import Bot, Dispatcher, types
from config import settings
from aiogram import F

logging.basicConfig(level=logging.INFO)
bot = Bot(token=settings.telegram_token)
openai = OpenAI(api_key=settings.openai_api_token)
dp = Dispatcher()

files_to_cleanup = set()  # to clear audio files


def cleanup_files():
    logging.info("Cleaning up files...")
    for file_path in files_to_cleanup:
        try:
            os.remove(file_path)
            logging.info(f"Deleted file: {file_path}")
        except Exception as e:
            logging.error(f"Error deleting file {file_path}: {e}")


atexit.register(cleanup_files)  # clearing audio files after closing the bot


async def voice_to_text(file_path):
    retries = 3
    for attempt in range(retries):
        try:
            with open(file_path, "rb") as f:
                transcript = openai.audio.transcriptions.create(
                    model="whisper-1",
                    file=f
                )
            return transcript.text
        except Exception as e:
            logging.error(f"Error during voice to text conversion: {e}")
            if attempt < retries - 1:
                await asyncio.sleep(2 ** attempt)
            else:
                raise


async def get_answer_from_openai(text):
    retries = 3
    for attempt in range(retries):
        try:
            my_assistant = openai.beta.assistants.create(
                name="Assistant",
                model="gpt-4o"
            )

            my_thread = openai.beta.threads.create()

            message = openai.beta.threads.messages.create(
                thread_id=my_thread.id,
                role="user",
                content=text
            )

            run = openai.beta.threads.runs.create(
                thread_id=my_thread.id,
                assistant_id=my_assistant.id
            )

            while run.status != "completed":
                await asyncio.sleep(0.5)
                run = openai.beta.threads.runs.retrieve(
                    thread_id=my_thread.id,
                    run_id=run.id
                )

            messages = openai.beta.threads.messages.list(
                thread_id=my_thread.id
            )
            return messages.data[0].content[0].text.value
        except Exception as e:
            logging.error(f"Error during getting answer from OpenAI: {e}")
            if attempt < retries - 1:
                await asyncio.sleep(2 ** attempt)
            else:
                raise


async def text_to_speech(text, output_file_path):
    retries = 3
    for attempt in range(retries):
        try:
            with openai.audio.speech.with_streaming_response.create(
                    model="tts-1",
                    voice="alloy",
                    input=text
            ) as response:
                response.stream_to_file(output_file_path)
            return
        except Exception as e:
            logging.error(f"Error during text to speech conversion: {e}")
            if attempt < retries - 1:
                await asyncio.sleep(2 ** attempt)
            else:
                raise


async def process_and_reply(message, text):
    try:
        answer = await get_answer_from_openai(text)
        # await message.reply(answer) # bot message output

        output_voice_path = Path(__file__).parent / f"speech_{message.message_id}.ogg"
        files_to_cleanup.add(str(output_voice_path))
        await text_to_speech(answer, output_voice_path)

        voice = FSInputFile(output_voice_path)
        await message.reply_voice(voice)
    except Exception as e:
        logging.error(e)
        await message.reply("Sorry, an error occurred while processing your message.")


@dp.message(F.voice)
async def handle_voice_message(message: types.Message):
    # downloading a file from the Telegram server
    file_id = message.voice.file_id
    file = await bot.get_file(file_id)
    file_path = file.file_path
    voice_file_path = Path(__file__).parent / f"voice_{message.message_id}.ogg"
    files_to_cleanup.add(str(voice_file_path))
    await bot.download_file(file_path, voice_file_path)

    try:
        text = await voice_to_text(voice_file_path)
        await process_and_reply(message, text)
    except Exception as e:
        logging.error(e)
        await message.reply("Sorry, an error occurred while processing your voice message.")


@dp.message()
async def handle_message(message: types.Message):
    await process_and_reply(message, message.text)


async def main():
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
