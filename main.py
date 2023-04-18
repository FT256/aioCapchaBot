import logging
import os
from io import BytesIO
from pathlib import Path

from aiogram import executor, Dispatcher, Bot, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.deep_linking import decode_payload
from aiogram.utils.deep_linking import get_start_link
from multicolorcaptcha import CaptchaGenerator

from db import SimpleDB

welcome = "Добро пожаловать, #USER'!'\nПожалуйста, пройдите проверку, чтобы убедиться, что вы не бот."
captcha_text = "Добро пожаловать, #USER!\nЧтобы получить доступ к чату #CHAT\nпожалуйста, введите код, " \
               "чтобы убедиться, что вы не бот. "
try_again = "\n⚠️ : Пожалуйста, попробуйте еще раз!"
your_code = "\nВаш код: "
wrong_user = "❌ : Это не ваша задача!"
too_short = "❌ : Ваш код слишком короткий."

TOKEN = ""
generator = "default"
digits = "1234567890"
timeout = 30
max_attempts = 2
max_incorrect_to_auto_reload = 2
difficult_level = 1

# Configure logging
logging.basicConfig(level=logging.INFO)

if not TOKEN:
    logging.error("No token provided")
    exit(1)

# Initialize bot and dispatcher
bot = Bot(token=TOKEN, parse_mode="HTML")
dp = Dispatcher(bot)

captcha_generator = CaptchaGenerator(captcha_size_num=3)
captcha_directory = Path("./captcha")
captcha_directory.mkdir(parents=True, exist_ok=True)
print(captcha_directory)


@dp.message_handler(content_types=['new_chat_members'])
async def send_welcome(message: types.Message):
    link = await get_start_link(f'{message.chat.id}_{message.from_user.id}', encode=True)
    db = SimpleDB(f'{captcha_directory}/{message.chat.id}={message.from_user.id}.captcha')
    db.set("chatname", f"{message.chat.title} (@{message.chat.username})")
    db.set("username", f"{message.from_user.username}")
    db.set("userid", f"{message.from_user.id}")
    if db.get("welcome_message_id") is not False:
        await bot.delete_message(message.chat.id, db.get("welcome_message_id"))
    inline_kb1 = InlineKeyboardMarkup().add(
        InlineKeyboardButton('Пройти проверку',
                             url=link))
    await cmd_readonly(message.chat.id, message.from_user.id)
    welcome_message = await bot.send_message(text=welcome.replace("#USER", get_mention(message.from_user)), chat_id=message.chat.id,
                                             reply_markup=inline_kb1)
    db.set("welcome_message_id", welcome_message.message_id)

def get_mention(user):
    if user.username:
        mention = fmd.hlink(url='tg://user?id=' + str(user.id), title='@' + user.username)
    else:
        mention = fmd.hlink(url='tg://user?id=' + str(user.id), title=user.first_name)
    return mention

@dp.message_handler(commands=["start"])
async def create_captcha(message: types.Message):
    args = message.get_args()
    payload = decode_payload(args)
    if payload == "":
        return
    chat = payload.split("_")[0]
    user_id = payload.split("_")[1]
    if int(user_id) != message.from_user.id:
        return
    db = SimpleDB(f'{captcha_directory}/{chat}={message.from_user.id}.captcha')
    if db.get("message_id") is not False:
        await bot.delete_message(message.chat.id, db.get("message_id"))
    code, image, code_length = random_captcha()
    bio = BytesIO()
    bio.name = 'captcha.png'
    image.save(bio, format="png")
    bio.seek(0)
    db.set("code", f"{code}")
    db.set("code_length", f"{code_length}")
    db.set("user_input", "")
    db.set("previous_tries", 0)
    db.set("user_reloads_left", 2)
    captcha_message = \
        await bot.send_photo(chat_id=message.chat.id,
                             photo=bio,
                             caption=captcha_text.replace("#USER", message.from_user.first_name).replace("#CHAT",
                                                                                               db.get("chatname")),
                             reply_markup=code_input_markup(
                                 user_id=db.get("userid"),
                                 max_attempts=max_attempts,
                                 previous_tries=db.get("previous_tries"),
                                 max_incorrect_to_auto_reload=max_incorrect_to_auto_reload,
                                 user_reloads_left=db.get("user_reloads_left"),
                                 chat_id=chat))
    db.set("message_id", captcha_message.message_id)
    bio.close()


async def on_timeout(chat_id=None, user_id=None, message_id=None):
    await bot.delete_message(chat_id, message_id)
    await bot.send_message(chat_id, f"{user_id} не успел решить капчу")


@dp.callback_query_handler(lambda callback_query: True)
async def callback_captcha(callback: types.CallbackQuery):
    chat_id = callback.data.split("=")[3]
    btn = callback.data.split("=")[2]
    user = int(callback.data.split("=")[1])
    db = SimpleDB(f'{captcha_directory}/{chat_id}={callback.data.split("=")[1]}.captcha')
    text = captcha_text.replace("#USER", db.get("username")).replace("#CHAT", db.get("chatname"))

    if callback.from_user.id != user:
        await bot.answer_callback_query(callback.id, text=f'Это не ваша капча!', show_alert=True)
        return

    if btn == "OK":
        if len(db.get("user_input")) < int(db.get("code_length")):
            await bot.answer_callback_query(callback.id, text=f'Код слишком короткий!', show_alert=True)
            return
        db.set("previous_tries", db.get("previous_tries") + 1)
        if db.get("user_input") == db.get("code"):  # success
            await callback.message.bot.delete_message(chat_id, db.get("welcome_message_id"))
            await callback.message.bot.delete_message(callback.message.chat.id, callback.message.message_id)
            db.delete()
            await cmd_unreadonly(chat_id, callback.from_user.id)
            return
        if db.get("previous_tries") > max_attempts:  # failed
            await callback.message.bot.delete_message(chat_id, db.get("welcome_message_id"))
            await callback.message.bot.delete_message(callback.message.chat.id, callback.message.message_id)
            db.delete()
            await cmd_kick(chat_id, callback.from_user.id)
            return

        code, image, code_length = random_captcha()
        bio = BytesIO()
        bio.name = 'capcha.png'
        image.save(bio, format="png")
        bio.seek(0)
        db.set("code", f"{code}")
        db.set("user_input", "")
        text += try_again
        await bot.edit_message_media(
            media=types.InputMediaPhoto(bio, text, "HTML"),
            chat_id=callback.message.chat.id,
            message_id=callback.message.message_id,
            reply_markup=code_input_markup(user_id=db.get("userid"),
                                           max_attempts=max_attempts,
                                           previous_tries=db.get("previous_tries"),
                                           max_incorrect_to_auto_reload=max_incorrect_to_auto_reload,
                                           user_reloads_left=db.get("user_reloads_left"),
                                           chat_id=chat_id))
        bio.close()
        await bot.answer_callback_query(callback.id)
        return

    elif btn == "RELOAD":
        code, image, code_length = random_captcha()
        bio = BytesIO()
        bio.name = 'capcha.png'
        image.save(bio, format="png")
        bio.seek(0)
        db.set("code", f"{code}")
        db.set("user_input", "")
        db.set("user_reloads_left", db.get("user_reloads_left") - 1)
        await bot.edit_message_media(
            media=types.InputMediaPhoto(bio, text, "HTML"),
            chat_id=callback.message.chat.id,
            message_id=callback.message.message_id,
            reply_markup=code_input_markup(user_id=db.get("userid"),
                                           max_attempts=max_attempts,
                                           previous_tries=db.get("previous_tries"),
                                           max_incorrect_to_auto_reload=max_incorrect_to_auto_reload,
                                           user_reloads_left=db.get("user_reloads_left"),
                                           chat_id=chat_id))
        bio.close()
        return

    elif btn == "BACK":
        if db.get("user_input") == "":
            await bot.answer_callback_query(callback.id)
            return
        db.set("user_input", "")

    else:
        if len(db.get("user_input")) < 4:
            db.set("user_input", f'{db.get("user_input")}{btn}')
            text += your_code + db.get("user_input")
        else:
            await bot.answer_callback_query(callback.id, text=f'Превышена максимальная длина кода!', show_alert=True)
            return

    await bot.edit_message_caption(
        caption=text,
        chat_id=callback.message.chat.id,
        message_id=callback.message.message_id,
        reply_markup=code_input_markup(user_id=db.get("userid"),
                                       max_attempts=max_attempts,
                                       previous_tries=db.get("previous_tries"),
                                       max_incorrect_to_auto_reload=max_incorrect_to_auto_reload,
                                       user_reloads_left=db.get("user_reloads_left"),
                                       chat_id=chat_id))
    await bot.answer_callback_query(callback.id)


def random_captcha():
    image, code, code_length = None, None, None
    if generator == "default":
        captcha = captcha_generator.gen_captcha_image(
            difficult_level=difficult_level,
            multicolor=True,
            chars_mode="nums",
        )
        image = captcha["image"]
        code = captcha["characters"]

    elif generator == "math":
        captcha = captcha_generator.gen_math_captcha_image(
            difficult_level=difficult_level,
            multicolor=True
        )
        image = captcha["image"]
        code = captcha["equation_result"]

    code_length = len(code)
    return code, image, code_length


def code_input_markup(user_id,
                      max_attempts,
                      previous_tries,
                      max_incorrect_to_auto_reload,
                      user_reloads_left,
                      chat_id) -> types.InlineKeyboardMarkup:
    values = {}
    row_width = 5
    display_attempts_left = max_attempts - previous_tries
    if max_incorrect_to_auto_reload <= 0:
        display_attempts_left = 0
    display_attempts_left = (
        "" if display_attempts_left <= 0 else str(display_attempts_left)
    )
    for digit in digits:
        values[digit] = {"callback_data": f"?cap={user_id}={digit}={chat_id}"}
    if user_reloads_left > 0:
        values[f"🔄 {user_reloads_left}"] = {"callback_data": f"?cap={user_id}=RELOAD={chat_id}"}
    values = {
        **values,
        "⬅️": {"callback_data": f"?cap={user_id}=BACK={chat_id}"},
        f"✅ {display_attempts_left}": {"callback_data": f"?cap={user_id}=OK={chat_id}"},
    }
    return quick_markup(values, row_width)


def quick_markup(values, row_width=4) -> types.InlineKeyboardMarkup:
    markup = types.InlineKeyboardMarkup(row_width=row_width)
    buttons = []
    for text, kwargs in values.items():
        buttons.append(types.InlineKeyboardButton(text=text, **kwargs))
    markup.add(*buttons)
    return markup


async def cmd_kick(chat, user):
    await bot.kick_chat_member(chat_id=chat,
                               user_id=user
                               )
    await bot.unban_chat_member(chat_id=chat,
                                user_id=user
                                )


async def cmd_readonly(chat, user):
    await bot.restrict_chat_member(chat,
                                   user,
                                   types.ChatPermissions(),
                                   )


async def cmd_unreadonly(chat, user):
    await bot.restrict_chat_member(chat,
                                   user,
                                   types.ChatPermissions(
                                       can_send_messages=True,
                                       can_send_media_messages=True,
                                       can_send_polls=True,
                                       can_send_other_messages=True,
                                       can_add_web_page_previews=True,
                                       can_change_info=True,
                                       can_invite_users=True,
                                       can_pin_messages=True)
                                   )


if __name__ == '__main__':
    executor.start_polling(dp, skip_updates=True)
