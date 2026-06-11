import os
import re
import asyncio
import aiohttp
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from Elevenyts import config
from Elevenyts.helpers import Track


def trim_to_width(text: str, font: ImageFont.FreeTypeFont, max_w: int) -> str:
    ellipsis = "…"
    if font.getlength(text) <= max_w:
        return text
    for i in range(len(text) - 1, 0, -1):
        if font.getlength(text[:i] + ellipsis) <= max_w:
            return text[:i] + ellipsis
    return ellipsis


class Thumbnail:
    def __init__(self):
        try:
            # စာလုံးဖောင့် အကြီးအသေးနှင့် Standard ပုံစံများ
            self.title_font = ImageFont.truetype(
                "Elevenyts/helpers/Raleway-Bold.ttf", 42)

            self.regular_font = ImageFont.truetype(
                "Elevenyts/helpers/Inter-Light.ttf", 22)

            self.watermark_font = ImageFont.truetype(
                "Elevenyts/helpers/Raleway-Bold.ttf", 32)  # ပိုမိုကျစ်လစ်သော အရွယ်အစား

            self.small_font = ImageFont.truetype(
                "Elevenyts/helpers/Inter-Light.ttf", 18)

        except OSError:
            self.title_font = self.regular_font = self.watermark_font = self.small_font = ImageFont.load_default()

    async def save_thumb(self, output_path: str, url: str) -> str:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                with open(output_path, "wb") as f:
                    f.write(await resp.read())
        return output_path

    async def generate(self, song: Track, size=(1280, 720)) -> str:
        try:
            temp = f"cache/temp_{song.id}.jpg"
            output = f"cache/{song.id}_modern.png"

            if os.path.exists(output):
                return output

            await self.save_thumb(temp, song.thumbnail)

            return await asyncio.get_event_loop().run_in_executor(
                None, self._generate_sync, temp, output, song, size
            )

        except Exception:
            return config.DEFAULT_THUMB

    def _generate_sync(self, temp: str, output: str, song: Track, size=(1280, 720)) -> str:
        try:
            # 1. Background ပုံကို Blur ခပ်ပါးပါး လုပ်ခြင်း
            with Image.open(temp) as temp_img:
                base = temp_img.resize(size).convert("RGBA")

            bg = base.filter(ImageFilter.GaussianBlur(12))  # Blur ကို ပိုတိုးပြီး မျက်လုံးအေးစေပါတယ်
            draw = ImageDraw.Draw(bg)

            # 2. ပုံတစ်ခုလုံးအပေါ်ကနေ ချောမွေ့စွာ မှောင်ဆင်းသွားမည့် Smooth Dark Overlay ထည့်ခြင်း
            overlay = Image.new("RGBA", size, (0, 0, 0, 0))
            overlay_draw = ImageDraw.Draw(overlay)
            for y in range(size[1]):
                # အောက်ခြေရောက်လေ ပိုမှောင်လေဖြစ်အောင် Gradient တွက်ချက်ခြင်း
                alpha = int(210 * (y / size[1]) ** 1.5)
                overlay_draw.line([(0, y), (size[0], y)], fill=(0, 0, 0, alpha))
            bg = Image.alpha_composite(bg, overlay)
            draw = ImageDraw.Draw(bg)

            # 3. တေးသံရှင် သို့မဟုတ် သီချင်း Original Thumbnail အသေးကို ဘယ်ဘက်တွင် ကပ်ထည့်ခြင်း
            thumb = base.resize((200, 200))
            mask = Image.new("L", thumb.size, 0)
            ImageDraw.Draw(mask).rounded_rectangle((0, 0, 200, 200), 20, fill=255)
            bg.paste(thumb, (70, 440), mask)

            # 4. စာသားများ ရေးသားခြင်း (Title & Views)
            title = re.sub(r"\W+", " ", song.title).title()
            title_text = trim_to_width(title, self.title_font, 850)
            
            draw.text(
                (300, 455),
                title_text,
                fill=(255, 255, 255, 255),
                font=self.title_font
            )

            views_text = f"YouTube • {song.view_count or 'Unknown Views'}"
            draw.text(
                (300, 515),
                views_text,
                fill=(200, 200, 200, 220),
                font=self.regular_font
            )

            # 5. တေးဂီတတိုးတက်မှုပြ Progress Bar (Minimalist Line)
            bar_start_x = 300
            bar_end_x = 850
            bar_y = 590
            
            # Progress Bar အနောက်ခံလိုင်း (Gray)
            draw.line([(bar_start_x, bar_y), (bar_end_x, bar_y)], fill=(100, 100, 100, 150), width=4)
            # အလယ်လောက်ထိ ဖွင့်ပြီးကြောင်းပြသည့် လိုင်း (White/Neon Blue စတိုင်)
            progress_x = bar_start_x + int((bar_end_x - bar_start_x) * 0.35)
            draw.line([(bar_start_x, bar_y), (progress_x, bar_y)], fill=(255, 255, 255, 255), width=4)
            # Progress Dot အဝိုင်းလေး
            draw.ellipse([(progress_x - 6, bar_y - 6), (progress_x + 6, bar_y + 6)], fill=(255, 255, 255, 255))

            # သီချင်း အချိန်ပြစာသားများ
            draw.text((bar_start_x, bar_y + 15), "00:00", fill=(180, 180, 180, 200), font=self.small_font)
            
            duration_str = getattr(song, 'duration', '00:00')
            duration_w = self.small_font.getlength(duration_str)
            draw.text((bar_end_x - duration_w, bar_y + 15), duration_str, fill=(180, 180, 180, 200), font=self.small_font)

            # 6. ရိုးရှင်းသန့်ပြန့်ပြီး စမတ်ကျသော ရေစာ (Watermark) - ညာဘက်အောက်ထောင့်
            watermark_text = "MYANMAR BOT"
            wm_w = self.watermark_font.getlength(watermark_text)
            wm_x = size[0] - wm_w - 50
            wm_y = size[1] - 70
            
            # စာသားကို ဖြူဖြူလွလွလေးနဲ့ Opacity 70% ခန့်ပေးထားလို့ မျက်လုံးမရှုပ်စေပါဘူး
            draw.text((wm_x, wm_y), watermark_text, font=self.watermark_font, fill=(255, 255, 255, 178))

            # 7. ဖိုင်သိမ်းဆည်းပြီး ယာယီဖိုင်ဖျက်ခြင်း
            bg.save(output)

            try:
                os.remove(temp)
            except:
                pass

            return output

        except Exception:
            return config.DEFAULT_THUMB
