import os
import requests
import telebot
from telebot.types import InputMediaPhoto
import time
import logging
import uuid
import asyncio
import subprocess
from shazamio import Shazam
from flask import Flask, jsonify
from threading import Thread

# --- ١. ڕێکخستنی سێرڤەر (Keep Alive) ---
app = Flask('')

@app.route('/')
def home():
    return jsonify({"status": "ok", "bot": "TikTok MP3 Downloader"}), 200

def run():
    app.run(host='0.0.0.0', port=5000, threaded=True)

def keep_alive():
    t = Thread(target=run, daemon=True)
    t.start()

# --- ٢. ڕێکخستنی بۆت ---
logging.basicConfig(level=logging.ERROR)
TOKEN = os.environ.get('TELEGRAM_TOKEN')
bot = telebot.TeleBot(TOKEN, num_threads=10)

shazam_loop = asyncio.new_event_loop()
def start_shazam_loop(loop):
    asyncio.set_event_loop(loop)
    loop.run_forever()

Thread(target=start_shazam_loop, args=(shazam_loop,), daemon=True).start()

# --- ٣. بەشی گۆڕینی دەنگ بۆ MP3 بە FFmpeg ---
def convert_to_mp3(input_file, output_file):
    try:
        subprocess.run(
            ['ffmpeg', '-y', '-i', input_file, '-vn', '-ar', '44100', '-ac', '2', '-b:a', '192k', output_file],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True
        )
        return True
    except Exception:
        return False

# --- ٤. دۆزینەوەی ناوی گۆرانی بە Shazam ---
async def _recognize_song_async(filepath):
    try:
        shazam = Shazam()
        out = await shazam.recognize(filepath)
        if 'track' in out:
            title = out['track'].get('title', '')
            artist = out['track'].get('subtitle', '')
            return f"{title} {artist}"
        return None
    except Exception:
        return None

def recognize_song(filepath):
    future = asyncio.run_coroutine_threadsafe(_recognize_song_async(filepath), shazam_loop)
    try:
        return future.result(timeout=10)
    except Exception:
        return None

# --- ٥. داگرتنی گۆرانی کامڵ (Full MP3) ---
def download_full_song_mp3(song_name, output_mp3_path):
    try:
        clean_name = song_name.replace(" ", "+")
        saavn_api = f"https://saavn.me/search/songs?query={clean_name}"
        res = requests.get(saavn_api, timeout=6).json()
        
        if res.get('status') == 'SUCCESS' and res.get('data', {}).get('results'):
            results = res['data']['results']
            if results:
                dl_urls = results[0].get('downloadUrl', [])
                if dl_urls:
                    audio_link = dl_urls[-1].get('link')
                    temp_dl = f"dl_temp_{uuid.uuid4()}.tmp"
                    
                    r = requests.get(audio_link, stream=True, timeout=15)
                    if r.status_code == 200:
                        with open(temp_dl, 'wb') as f:
                            for chunk in r.iter_content(chunk_size=16384):
                                f.write(chunk)
                        
                        if convert_to_mp3(temp_dl, output_mp3_path):
                            if os.path.exists(temp_dl): os.remove(temp_dl)
                            return True
                    if os.path.exists(temp_dl): os.remove(temp_dl)
    except Exception:
        pass
    return False

# --- ٦. داگرتنی تیکتۆک ---
def download_tiktok(url):
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        api_url = f"https://www.tikwm.com/api/?url={url}"
        response = requests.get(api_url, headers=headers, timeout=8).json()

        if response.get("code") == 0:
            is_photo = "images" in response["data"]
            audio_link = response["data"].get("music")
            info = {
                "author": response["data"]["author"]["nickname"],
                "likes": response["data"]["digg_count"],
                "views": response["data"]["play_count"],
                "is_photo": is_photo
            }
            if is_photo:
                media_links = response["data"]["images"]
            else:
                media_links = response["data"].get("hdplay") or response["data"].get("play")
            return media_links, audio_link, info
        return None, None, None
    except Exception:
        return None, None, None

# --- ٧. فەرمانەکانی بۆت ---
@bot.message_handler(commands=['start'])
def start(message):
    bot.reply_to(message, "👋🏼 من دەتوانم بە خێرایی لە تیکتۆک ڤیدیۆ و گۆرانی کامڵی MP3 دابەزێنم.\n🔗 تەنها لینک بنێرە:")

@bot.message_handler(func=lambda msg: True)
def handle_links(message):
    Thread(target=process_request, args=(message,), daemon=True).start()

def process_request(message):
    url = message.text.strip()

    if "tiktok.com" in url:
        wait_msg = bot.reply_to(message, "⚡ خەریکی داگرتنم...")
        media_data, audio_url, info = download_tiktok(url)

        if media_data:
            caption = f"👤 : {info['author']}\n❤️ : {info['likes']} | 👀 : {info['views']}\n\n⚙️ @bu404"
            temp_raw_audio = None
            temp_mp3 = None
            try:
                # ١. ناردنی ڤیدیۆ/وێنە
                if info["is_photo"]:
                    images = media_data
                    for i in range(0, len(images), 10):
                        chunk = images[i:i+10]
                        media_group = [InputMediaPhoto(chunk[0], caption=caption if i==0 else "")] + [InputMediaPhoto(img) for img in chunk[1:]]
                        bot.send_media_group(message.chat.id, media_group, timeout=60)
                else:
                    bot.send_video(message.chat.id, media_data, caption=caption, timeout=60)

                # ٢. دۆزینەوەی ناوی گۆرانی
                song_name = None
                if audio_url:
                    temp_raw_audio = f"raw_{uuid.uuid4()}.tmp"
                    r = requests.get(audio_url, headers={"User-Agent": "Mozilla/5.0"}, stream=True, timeout=8)
                    if r.status_code == 200:
                        with open(temp_raw_audio, 'wb') as f:
                            for chunk in r.iter_content(chunk_size=16384):
                                f.write(chunk)

                        if os.path.exists(temp_raw_audio) and os.path.getsize(temp_raw_audio) > 0:
                            song_name = recognize_song(temp_raw_audio)

                # ٣. داگرتنی گۆرانی کامڵ (Full MP3)
                full_song_downloaded = False
                if song_name:
                    temp_mp3 = f"full_song_{uuid.uuid4()}.mp3"
                    full_song_downloaded = download_full_song_mp3(song_name, temp_mp3)

                # ٤. ناردنی فایلی دەنگ
                if full_song_downloaded and os.path.exists(temp_mp3):
                    audio_caption = f"🎶 ناوی گۆرانی: **{song_name}**\n🎧 **گۆرانی کامڵ (Full MP3)**"
                    with open(temp_mp3, 'rb') as final_audio:
                        bot.send_audio(message.chat.id, final_audio, caption=audio_caption, parse_mode="Markdown", timeout=60)
                elif audio_url:
                    temp_fallback_mp3 = f"fallback_{uuid.uuid4()}.mp3"
                    if temp_raw_audio and convert_to_mp3(temp_raw_audio, temp_fallback_mp3):
                        caption_text = f"🎶 ناوی گۆرانی: {song_name}\n🔊 دەنگی سەر پۆستەکە (MP3)" if song_name else "🔊 دەنگی سەر پۆستەکە (MP3)"
                        with open(temp_fallback_mp3, 'rb') as fallback_file:
                            bot.send_audio(message.chat.id, fallback_file, caption=caption_text, timeout=60)
                        if os.path.exists(temp_fallback_mp3): os.remove(temp_fallback_mp3)
                    else:
                        bot.send_audio(message.chat.id, audio_url, caption="🔊 دەنگی سەر پۆستەکە", timeout=60)

                bot.delete_message(message.chat.id, wait_msg.message_id)
            except Exception:
                pass
            finally:
                if temp_raw_audio and os.path.exists(temp_raw_audio):
                    try: os.remove(temp_raw_audio)
                    except: pass
                if temp_mp3 and os.path.exists(temp_mp3):
                    try: os.remove(temp_mp3)
                    except: pass
        else:
            bot.edit_message_text("❌ نەمتوانی دابیەزێنم.", message.chat.id, wait_msg.message_id)

if __name__ == '__main__':
    keep_alive()
    bot.delete_webhook()
    while True:
        try:
            bot.infinity_polling(timeout=10, long_polling_timeout=5)
        except:
            time.sleep(2)
