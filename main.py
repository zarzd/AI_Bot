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

assistant = openai.beta.assistants.create(
    name="Assistant",
    model="gpt-4o"
)
thread = openai.beta.threads.create()

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
    try:
        with open(file_path, "rb") as f:
            transcript = openai.audio.transcriptions.create(
                model="whisper-1",
                file=f
            )
        return transcript.text
    except Exception as e:
        logging.error(f"Error during voice to text conversion: {e}")
        raise


async def get_answer_from_openai(text, my_thread, my_assistant):
    try:
        message = openai.beta.threads.messages.create(
            thread_id=my_thread.id,
            role="user",
            content=text
        )

        run = openai.beta.threads.runs.create(
            thread_id=my_thread.id,
            assistant_id=my_assistant.id
        )

        while True:
            run_status = openai.beta.threads.runs.retrieve(thread_id=thread.id,
                                                           run_id=run.id)
            if run_status.status == "completed":
                break
            elif run_status.status == "failed":
                logging.error("Run failed: %s", run_status.last_error)
                raise RuntimeError("OpenAI run failed")

        messages = openai.beta.threads.messages.list(
            thread_id=my_thread.id
        )
        return messages.data[0].content[0].text.value
    except Exception as e:
        logging.error(f"Error during getting answer from OpenAI: {e}")
        raise


async def text_to_speech(text, output_file_path):
    try:
        with openai.audio.speech.with_streaming_response.create(
                model="tts-1",
                voice="alloy",
                input=text
        ) as response:
            response.stream_to_file(output_file_path)
    except Exception as e:
        logging.error(f"Error during text to speech conversion: {e}")
        raise


async def process_and_reply(message, text):
    answer = await get_answer_from_openai(text, thread, assistant)
    # await message.reply(answer) # bot message output

    output_voice_path = Path(__file__).parent / f"speech_{message.message_id}.ogg"
    files_to_cleanup.add(str(output_voice_path))
    await text_to_speech(answer, output_voice_path)

    voice = FSInputFile(output_voice_path)
    await message.reply_voice(voice)


async def handle_voice_message(message: types.Message):
    # downloading a file from the Telegram server
    try:
        file_id = message.voice.file_id
        file = await bot.get_file(file_id, request_timeout=120)
        file_path = file.file_path
        voice_file_path = Path(__file__).parent / f"voice_{message.message_id}.ogg"
        files_to_cleanup.add(str(voice_file_path))
        await bot.download_file(file_path, voice_file_path, timeout=120)

        text = await voice_to_text(voice_file_path)
        await process_and_reply(message, text)
    except Exception as e:
        logging.error(e)
        await message.reply("Sorry, an error occurred while processing your voice message.")


async def handle_message(message: types.Message):
    try:
        await process_and_reply(message, message.text)
    except Exception as e:
        logging.error(e)
        await message.reply("Sorry, an error occurred while processing your message.")


queue = asyncio.Queue()


@dp.message(F.voice)
async def queue_voice_message(message: types.Message):
    await queue.put(handle_voice_message(message))


@dp.message()
async def queue_text_message(message: types.Message):
    await queue.put(handle_message(message))


async def worker():
    while True:
        task = await queue.get()
        try:
            await task
        except Exception as e:
            logging.error(f"Error processing message: {e}")
        finally:
            queue.task_done()


async def main():
    worker_task = asyncio.create_task(worker())
    await dp.start_polling(bot)
    await queue.join()
    worker_task.cancel()


if __name__ == "__main__":
    asyncio.run(main())
