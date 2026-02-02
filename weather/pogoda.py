import logging
import json
import os
import asyncio
import time
import requests
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes,
)


BOT_TOKEN = "------------" 
DATA_FILE = "weather_users_v5.json"


logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)


def make_request(url, params=None, retries=3):
    """Пытается сделать запрос несколько раз, если сеть лагает"""
    for i in range(retries):
        try:
            response = requests.get(url, params=params, timeout=5)
            if response.status_code == 200:
                return response.json()
        except:
            time.sleep(1) 
    return None


def load_data():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except:
            pass
    return {}

def save_data(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

# --- Клавиатуры ---
def get_time_keyboard():
    keyboard = [
        [InlineKeyboardButton("⏱ 1 час", callback_data="time_60"), InlineKeyboardButton("⏱ 3 часа", callback_data="time_180")],
        [InlineKeyboardButton("⏱ 6 часов", callback_data="time_360"), InlineKeyboardButton("⏱ 12 часов", callback_data="time_720")],
        [InlineKeyboardButton("⏱ 24 часа", callback_data="time_1440"), InlineKeyboardButton("🔕 Не присылать", callback_data="time_off")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_check_keyboard():
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔄 Обновить погоду", callback_data="check_now")]])

# --- 1. Поиск города ---
def get_coordinates(city_name):
    url = f"https://geocoding-api.open-meteo.com/v1/search?name={city_name}&count=1&language=ru&format=json"
    data = make_request(url) 
    
    if data and "results" in data and len(data["results"]) > 0:
        return data["results"][0], None
    
    if data is None:
        return None, "⚠️ Нестабильное соединение. Попробуйте еще раз."
        
    return None, "Город не найден. Попробуйте написать латиницей (Moscow)."


def get_weather_detailed(lat, lon):
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": lat,
        "longitude": lon,
        "current": "temperature_2m,relative_humidity_2m,apparent_temperature,weather_code,surface_pressure,wind_speed_10m,precipitation",
        "timezone": "auto"
    }
    return make_request(url, params)


def format_report(data, city_name):
    if not data: return "⚠️ Не удалось обновить данные (сбой сети)."
    
    curr = data.get("current", {})
    temp = curr.get("temperature_2m")
    feels = curr.get("apparent_temperature")
    wind = curr.get("wind_speed_10m")
    humid = curr.get("relative_humidity_2m")
    press = curr.get("surface_pressure")
    
 
    code = curr.get("weather_code", 0)
    icon = "☀️"
    if code in [1, 2, 3]: icon = "☁️"
    elif code in [45, 48]: icon = "🌫"
    elif code in [51, 53, 55, 61, 63, 65, 80, 81, 82]: icon = "🌧"
    elif code in [71, 73, 75, 77, 85, 86]: icon = "🌨"
    elif code >= 95: icon = "⛈"

    return (
        f"🏙 <b>{city_name}</b>\n\n"
        f"{icon} <b>{temp}°C</b> (ощущается {feels}°C)\n"
        f"💨 Ветер: {wind} км/ч\n"
        f"💧 Влажность: {humid}%\n"
        f"⏲ Давление: {press} гПа"
    )



async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    await update.message.reply_text(
        "🌤 <b>Погодный Бот</b>\n\n"
        "Я помогу следить за погодой и пришлю отчет, когда скажешь.\n\n"
        "👇 <b>Просто напиши название города:</b>",
        parse_mode="HTML"
    )

async def handle_city_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    city_name = update.message.text.strip()
    
    msg = await update.message.reply_text(f"🔎 Ищу <b>{city_name}</b>...", parse_mode="HTML")
    
    coords, error = get_coordinates(city_name)
    
    if error:
        await msg.edit_text(f"❌ {error}")
        return


    user_id = str(update.effective_chat.id)
    users = load_data()
    
    if user_id not in users: users[user_id] = {}
    users[user_id]["name"] = coords["name"]
    users[user_id]["lat"] = coords["latitude"]
    users[user_id]["lon"] = coords["longitude"]
    users[user_id]["interval"] = 0 
    
    save_data(users)
    
    await msg.edit_text(
        f"✅ Город <b>{coords['name']}</b> найден!\n\n"
        "Как часто присылать отчет?",
        parse_mode="HTML",
        reply_markup=get_time_keyboard()
    )

async def handle_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer() 
    
    data = query.data
    user_id = str(update.effective_chat.id)
    users = load_data()
    

    if data.startswith("time_"):
        if user_id not in users or "lat" not in users[user_id]:
            await query.edit_message_text("❌ Сначала напишите город.")
            return

        if data == "time_off":
            users[user_id]["interval"] = 0
            save_data(users)
            await query.edit_message_text("🔕 Авто-отчеты выключены.\nВы можете проверять погоду вручную.", reply_markup=get_check_keyboard())
            return

        minutes = int(data.split("_")[1])
        users[user_id]["interval"] = minutes
        users[user_id]["last_run"] = time.time()
        save_data(users)

        weather = get_weather_detailed(users[user_id]["lat"], users[user_id]["lon"])
        report = format_report(weather, users[user_id]["name"])
        
        await query.edit_message_text(
            f"✅ Таймер: <b>каждые {minutes} мин.</b>\n\n{report}",
            parse_mode="HTML",
            reply_markup=get_check_keyboard()
        )

    elif data == "check_now":
        if user_id not in users: return
            
        weather = get_weather_detailed(users[user_id]["lat"], users[user_id]["lon"])
        report = format_report(weather, users[user_id]["name"])
        
        
        if "Сбой сети" in report:
            await query.answer("⚠️ Сбой сети, попробуйте позже", show_alert=True)
            return

        try:
            await query.message.edit_text(
                text=report,
                parse_mode="HTML",
                reply_markup=get_check_keyboard()
            )
        except:
            pass 


async def background_loop(app):
    print("✅ Мониторинг запущен")
    while True:
        try:
            users = load_data()
            current_time = time.time()
            is_changed = False
            
            for user_id, data in users.items():
                interval = data.get("interval", 0)
                if interval <= 0 or "lat" not in data: continue
                
                last_run = data.get("last_run", 0)
                
                if current_time - last_run >= (interval * 60):
                    weather = get_weather_detailed(data["lat"], data["lon"])
                    
              
                    if weather:
                        report = format_report(weather, data["name"])
                        try:
                            await app.bot.send_message(
                                chat_id=user_id, 
                                text=f"⏰ <b>Отчет:</b>\n\n{report}", 
                                parse_mode="HTML",
                                reply_markup=get_check_keyboard()
                            )
                            users[user_id]["last_run"] = current_time
                            is_changed = True
                        except: pass
                    
                    await asyncio.sleep(1)

            if is_changed:
                save_data(users)
                
        except Exception as e:
            print(f"Ошибка цикла: {e}")
        
        await asyncio.sleep(10)

async def on_startup(app):
    asyncio.create_task(background_loop(app))

def main():
    if "ВСТАВЬТЕ" in BOT_TOKEN: print("❌ Вставьте токен!"); return

    app = Application.builder().token(BOT_TOKEN).post_init(on_startup).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(handle_buttons))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_city_text))

    print("✅ Бот запущен!")
    app.run_polling()

if __name__ == "__main__":
    main()

