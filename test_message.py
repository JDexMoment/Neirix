import asyncio
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command

async def main():
    bot = Bot(token="8776202705:AAGcwPYPwjVt646IlTj0jHo5qHGzNptrx9I")
    dp = Dispatcher()

    @dp.message(Command("start"))
    async def start(message: types.Message):
        await message.answer("Тестовый ответ")

    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())