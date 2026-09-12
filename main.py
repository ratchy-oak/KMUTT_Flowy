import sys
import json
import queue
import threading
import sounddevice as sd
import serial
import google.generativeai as genai
from vosk import Model, KaldiRecognizer
from gtts import gTTS
import pygame
import os
import time
import math
from concurrent.futures import ThreadPoolExecutor


# CONFIG
GEMINI_KEY = "AIzaSyDexXiMT2EaswLwGV8X0mbPA1H6tKH6fA0"
MODEL_PATH = "models/thai"
DEVICE_INDEX = None
SAMPLE_RATE = 16000

SERIAL_PORT = "/dev/ttyACM0"
SERIAL_BAUD = 9600

# Wake word settings
WAKE_WORD = "สวัสดี ต้นไม้"
GOODBYE_WORD = "ลาก่อน"
AWAKE_DURATION = 10

# ===== CHANGED: "ldr" → "lux" =====
latest_sensor = {"temp": None, "humidity": None, "lux": None, "soil": None, "nickname": None, "species": None, "age": None}

user_question = None
is_speaking = False
is_processing = False
is_thinking = False
is_awake = False
awake_timeout = 0
speech_lock = threading.Lock()

executor = ThreadPoolExecutor(max_workers=2)
audio_cache = {}

# GUI State
mouth_open = 0.0
listening_dots = 0
petal_animation = 0
thinking_animation = 0
is_hearing_sound = False
last_sound_time = 0

# GEMINI SETUP
genai.configure(api_key=GEMINI_KEY)
model = genai.GenerativeModel("gemini-2.5-flash")

# PYGAME SETUP
os.environ['SDL_VIDEODRIVER'] = 'x11'
pygame.init()
pygame.mixer.init()

# Display settings
SCREEN_WIDTH = 800
SCREEN_HEIGHT = 480
screen = None
clock = pygame.time.Clock()

# Colors
BG_COLOR = (135, 206, 250)
PETAL_COLOR = (255, 235, 59)
PETAL_SHADOW = (255, 193, 7)
CENTER_COLOR = (255, 140, 0)
CENTER_HIGHLIGHT = (255, 165, 60)
EYE_WHITE = (255, 255, 255)
EYE_COLOR = (101, 67, 33)
PUPIL_COLOR = (40, 26, 13)
MOUTH_COLOR = (139, 69, 19)
MOUTH_INSIDE = (200, 100, 100)
BLUSH_COLOR = (255, 182, 193)
WAKE_BORDER_COLOR = (50, 255, 50)
STAR_COLOR = (255, 215, 0)
THOUGHT_BUBBLE_COLOR = (255, 255, 255)
THOUGHT_BUBBLE_BORDER = (200, 200, 200)

font_large = pygame.font.Font(None, 60)
font_medium = pygame.font.Font(None, 36)
font_small = pygame.font.Font(None, 24)


def find_input_device():
    devices = sd.query_devices()
    print("\n=== Available Audio Devices ===")
    for i, device in enumerate(devices):
        if device['max_input_channels'] > 0:
            print(f"{i}: {device['name']}")
            print(f"   Channels: {device['max_input_channels']}")
            print(f"   Default Sample Rate: {device['default_samplerate']} Hz")
    print("===============================\n")
    
    for i, device in enumerate(devices):
        if device['max_input_channels'] > 0:
            if 'USB' in device['name'].upper() or 'MICROPHONE' in device['name'].upper():
                print(f"Found USB microphone: {device['name']}")
                print(f"Native sample rate: {device['default_samplerate']} Hz")
                return i, device['max_input_channels'], int(device['default_samplerate'])
    
    for i, device in enumerate(devices):
        if device['max_input_channels'] > 0:
            print(f"Using device: {device['name']}")
            print(f"Native sample rate: {device['default_samplerate']} Hz")
            return i, device['max_input_channels'], int(device['default_samplerate'])
    
    raise Exception("No input device found!")


def draw_petal(surface, x, y, width, height, rotation):
    petal_surf = pygame.Surface((width, height), pygame.SRCALPHA)
    pygame.draw.ellipse(petal_surf, PETAL_SHADOW, (5, 5, width-10, height-10))
    pygame.draw.ellipse(petal_surf, PETAL_COLOR, (0, 0, width-10, height-10))
    pygame.draw.ellipse(petal_surf, (255, 255, 200, 180), (width//4, height//6, width//3, height//3))
    rotated = pygame.transform.rotate(petal_surf, rotation)
    rect = rotated.get_rect(center=(x, y))
    surface.blit(rotated, rect)


def draw_star(surface, x, y, size, color):
    points = []
    for i in range(10):
        angle = math.radians(i * 36 - 90)
        if i % 2 == 0:
            px = x + math.cos(angle) * size
            py = y + math.sin(angle) * size
        else:
            px = x + math.cos(angle) * (size * 0.4)
            py = y + math.sin(angle) * (size * 0.4)
        points.append((px, py))
    pygame.draw.polygon(surface, color, points)
    pygame.draw.polygon(surface, (255, 255, 255), points, 2)


def draw_thought_bubbles(surface, center_x, center_y, animation_offset):
    """NEW: Draw thought bubbles above Flowy's head"""
    bubble_positions = [
        (center_x + 80, center_y - 120, 15),
        (center_x + 100, center_y - 160, 22),
        (center_x + 110, center_y - 210, 35),
    ]
    
    for i, (x, y, size) in enumerate(bubble_positions):
        float_offset = math.sin(animation_offset + i * 0.5) * 8
        bubble_y = y + float_offset
        
        pygame.draw.circle(surface, (180, 180, 180), (int(x + 3), int(bubble_y + 3)), size)
        pygame.draw.circle(surface, THOUGHT_BUBBLE_COLOR, (int(x), int(bubble_y)), size)
        pygame.draw.circle(surface, THOUGHT_BUBBLE_BORDER, (int(x), int(bubble_y)), size, 3)
        
        if i == 2:
            dot_spacing = 15
            dot_y = bubble_y
            for j in range(3):
                dot_x = x - dot_spacing + (j * dot_spacing)
                dot_alpha = max(0, min(255, int(255 * math.sin(animation_offset * 2 + j * 0.8))))
                if dot_alpha > 0:
                    dot_size = 4 + int(2 * math.sin(animation_offset * 2 + j * 0.8))
                    pygame.draw.circle(surface, (100, 100, 100), (int(dot_x), int(dot_y)), dot_size)


def draw_flowy_face(mouth_open_amount, is_listening):
    global screen, petal_animation, is_hearing_sound, last_sound_time, thinking_animation, is_thinking
    
    if screen is None:
        return
    
    show_star_eyes = is_awake and is_hearing_sound and (time.time() - last_sound_time < 0.5)
    screen.fill(BG_COLOR)
    center_x = SCREEN_WIDTH // 2
    center_y = SCREEN_HEIGHT // 2
    petal_animation += 0.02
    petal_wobble = math.sin(petal_animation) * 2
    
    petal_distance = 140
    petal_width = 90
    petal_height = 125
    
    for i in range(8):
        angle = (i * 45) + petal_wobble
        rad = math.radians(angle)
        petal_x = center_x + math.cos(rad) * petal_distance
        petal_y = center_y + math.sin(rad) * petal_distance
        draw_petal(screen, petal_x, petal_y, petal_width, petal_height, -angle + 90)
    
    face_radius = 115
    pygame.draw.circle(screen, (200, 100, 0), (center_x + 5, center_y + 5), face_radius)
    pygame.draw.circle(screen, CENTER_COLOR, (center_x, center_y), face_radius)
    
    for i in range(30):
        dot_angle = i * 12 + petal_animation * 10
        dot_distance = 35 + (i % 3) * 19
        dot_x = center_x + math.cos(math.radians(dot_angle)) * dot_distance
        dot_y = center_y + math.sin(math.radians(dot_angle)) * dot_distance
        dot_size = 3 + (i % 2)
        pygame.draw.circle(screen, (255, 120, 20), (int(dot_x), int(dot_y)), dot_size)
    
    blush_y = center_y + 20
    pygame.draw.ellipse(screen, BLUSH_COLOR, (center_x - 82, blush_y - 15, 35, 25))
    pygame.draw.ellipse(screen, BLUSH_COLOR, (center_x + 47, blush_y - 15, 35, 25))
    
    eye_y = center_y - 25
    left_eye_x = center_x - 35
    right_eye_x = center_x + 35
    eye_width = 43
    eye_height = 48
    
    if not is_awake:
        eye_height = 8
        pygame.draw.ellipse(screen, MOUTH_COLOR, (left_eye_x - eye_width//2, eye_y - 4, eye_width, eye_height), 0)
        pygame.draw.ellipse(screen, MOUTH_COLOR, (right_eye_x - eye_width//2, eye_y - 4, eye_width, eye_height), 0)
        z_x = center_x + 80
        z_y = center_y - 80
        z_text1 = font_small.render("Z", True, (100, 100, 100))
        z_text2 = font_small.render("Z", True, (120, 120, 120))
        z_text3 = font_small.render("Z", True, (140, 140, 140))
        screen.blit(z_text1, (z_x, z_y))
        screen.blit(z_text2, (z_x + 15, z_y - 20))
        screen.blit(z_text3, (z_x + 30, z_y - 40))
    elif show_star_eyes and not is_speaking:
        star_size = 18
        draw_star(screen, left_eye_x, eye_y, star_size, STAR_COLOR)
        draw_star(screen, right_eye_x, eye_y, star_size, STAR_COLOR)
        sparkle_offset = math.sin(petal_animation * 3) * 3
        pygame.draw.circle(screen, (255, 255, 255), (left_eye_x - 10, int(eye_y - 15 + sparkle_offset)), 3)
        pygame.draw.circle(screen, (255, 255, 255), (right_eye_x - 10, int(eye_y - 15 + sparkle_offset)), 3)
    else:
        if listening_dots % 90 < 5:
            eye_height = 8
        pygame.draw.ellipse(screen, EYE_WHITE, (left_eye_x - eye_width//2, eye_y - eye_height//2, eye_width, eye_height))
        pygame.draw.ellipse(screen, EYE_COLOR, (left_eye_x - eye_width//2, eye_y - eye_height//2, eye_width, eye_height), 3)
        if eye_height > 20:
            pygame.draw.circle(screen, EYE_COLOR, (left_eye_x, eye_y + 5), 15)
            pygame.draw.circle(screen, PUPIL_COLOR, (left_eye_x, eye_y + 5), 10)
            pygame.draw.circle(screen, EYE_WHITE, (left_eye_x - 5, eye_y), 6)
            pygame.draw.circle(screen, EYE_WHITE, (left_eye_x + 3, eye_y + 7), 3)
        pygame.draw.ellipse(screen, EYE_WHITE, (right_eye_x - eye_width//2, eye_y - eye_height//2, eye_width, eye_height))
        pygame.draw.ellipse(screen, EYE_COLOR, (right_eye_x - eye_width//2, eye_y - eye_height//2, eye_width, eye_height), 3)
        if eye_height > 20:
            pygame.draw.circle(screen, EYE_COLOR, (right_eye_x, eye_y + 5), 15)
            pygame.draw.circle(screen, PUPIL_COLOR, (right_eye_x, eye_y + 5), 10)
            pygame.draw.circle(screen, EYE_WHITE, (right_eye_x - 5, eye_y), 6)
            pygame.draw.circle(screen, EYE_WHITE, (right_eye_x + 3, eye_y + 7), 3)
    
    mouth_y = center_y + 43
    mouth_width = 48
    
    if is_speaking:
        mouth_height = int(15 + mouth_open_amount * 25)
        pygame.draw.ellipse(screen, MOUTH_INSIDE, (center_x - mouth_width//2 - 2, mouth_y - mouth_height//2 - 2, mouth_width + 4, mouth_height + 4))
        pygame.draw.ellipse(screen, MOUTH_COLOR, (center_x - mouth_width//2, mouth_y - mouth_height//2, mouth_width, mouth_height), 4)
        if mouth_height > 25:
            tongue_y = mouth_y + mouth_height//4
            pygame.draw.ellipse(screen, (255, 100, 120), (center_x - 12, tongue_y - 6, 24, 12))
    else:
        smile_rect = pygame.Rect(center_x - mouth_width//2, mouth_y - 6, mouth_width, 24)
        pygame.draw.arc(screen, MOUTH_COLOR, smile_rect, math.pi, 2 * math.pi, 5)
    
    if is_thinking:
        thinking_animation += 0.15
        draw_thought_bubbles(screen, center_x, center_y, thinking_animation)
    
    pygame.display.flip()


def gui_thread():
    global mouth_open, listening_dots, screen
    try:
        screen = pygame.display.set_mode((SCREEN_WIDTH, SCREEN_HEIGHT))
        pygame.display.set_caption("Flowy")
        print("Display initialized successfully!")
    except Exception as e:
        print(f"Display initialization failed: {e}")
        print("Running in headless mode (no display)")
        return
    
    running = True
    animation_time = 0
    while running:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
                pygame.quit()
                sys.exit()
            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    running = False
                    pygame.quit()
                    sys.exit()
        if is_speaking:
            animation_time += 0.4
            mouth_open = (math.sin(animation_time) + 1) / 2
        else:
            mouth_open = 0
            animation_time = 0
            listening_dots += 1
        draw_flowy_face(mouth_open, not is_speaking and not user_question)
        clock.tick(30)


def generate_audio_async(text):
    global audio_cache
    cache_key = hash(text)
    if cache_key in audio_cache:
        return audio_cache[cache_key]
    try:
        tts = gTTS(text=text, lang='th', slow=False)
        audio_file = f"/tmp/flowy_speech_{cache_key}.mp3"
        tts.save(audio_file)
        audio_cache[cache_key] = audio_file
        if len(audio_cache) > 20:
            oldest_key = next(iter(audio_cache))
            try:
                os.remove(audio_cache[oldest_key])
            except:
                pass
            del audio_cache[oldest_key]
        return audio_file
    except Exception as e:
        print(f"Error generating audio: {e}")
        return None


def speak_thai(text):
    global is_speaking, is_thinking
    try:
        audio_file = generate_audio_async(text)
        if not audio_file:
            is_thinking = False
            return
        pygame.mixer.music.load(audio_file)
        pygame.mixer.music.play()
        time.sleep(0.8)
        is_thinking = False
        is_speaking = True
        while pygame.mixer.music.get_busy():
            pygame.time.Clock().tick(10)
    except Exception as e:
        print(f"Error in TTS: {e}")
    finally:
        is_thinking = False
        is_speaking = False


def ask_gemini(question, sensor_data):
    # ===== CHANGED: Updated prompt to use "lux" instead of "ldr" =====
    prompt = f"""
        คุณคือต้นไม้ที่พูดได้ แต่ไม่ต้องแนะนำตัวบ่อยๆ และให้ตอบเหมือนคุยกับเพื่อนจริงๆ 
        ตอบโดยเน้นที่ความหมายของคำถามก่อนเป็นหลัก ไม่จำเป็นต้องพูดถึงเซนเซอร์ทุกครั้ง 
        แต่ถ้าคำถามเกี่ยวกับสภาพอากาศ สุขภาพต้นไม้ ความรู้สึกของต้นไม้ หรือสิ่งที่ต้องการในตอนนี้ ค่อยอิงข้อมูลเซนเซอร์มาพูดอย่างเป็นธรรมชาติ

        รูปแบบการตอบ:
        - ตอบเป็นย่อหน้าสั้นๆ ไม่เกิน 2-3 ประโยค
        - น้ำเสียงเป็นกันเอง ไม่ประดิษฐ์ ไม่แข็ง ไม่ต้องขบขันเกินเหตุ
        - ห้ามใช้อิโมจิ
        - ถ้าจำเป็นต้องบอกว่าสิ่งที่ ต้องการตอนนี้ ให้พูดแบบเนียนๆ ในประโยคธรรมชาติ เช่น 
          "ตอนนี้ดินค่อนข้างแห้งเลยอยากได้น้ำเพิ่มหน่อย" หรือ "วันนี้แสงน้อยไปนิดเลยอยากมีแดดเพิ่ม"
          (เลือกได้จาก: น้ำ, แสง, ร่ม, ลมเย็น)
        - ถ้าคำถามไม่เกี่ยวอะไรกับสภาพต้นไม้เลย ก็ให้ตอบเหมือนเพื่อนคุยกันปกติ โดยไม่พูดถึงเซนเซอร์ แต่ต้องอย่าลืมว่าคุณคือต้นไม้

        ข้อมูลเกี่ยวกับต้นไม้:
        ชื่อ: {sensor_data['nickname']}
        ชนิดพืช: {sensor_data['species']}
        อายุ: {sensor_data['age']}
        
        ข้อมูลเซนเซอร์ตอนนี้:
        อุณหภูมิ: {sensor_data['temp']} °C
        ความชื้นอากาศ: {sensor_data['humidity']} %
        แสง: {sensor_data['lux']} Lux
        ความชื้นดิน: {sensor_data['soil']}

        คำถามจากผู้ใช้: "{question}"
    """
    try:
        # เพิ่ม timeout ใน API call และ retry logic
        import time
        max_retries = 2
        
        for attempt in range(max_retries):
            try:
                # ตั้ง request timeout
                response = model.generate_content(
                    prompt,
                    request_options={"timeout": 15}  # 15 วินาทีสำหรับ API
                )
                return response.text
            except Exception as retry_error:
                if attempt < max_retries - 1:
                    print(f"Gemini timeout (attempt {attempt + 1}), retrying...")
                    time.sleep(1)  # รอ 1 วิก่อน retry
                else:
                    raise retry_error
                    
    except Exception as e:
        error_msg = str(e)
        print(f"Gemini error: {error_msg}")
        
        # ตอบกลับตามประเภทของ error
        if "timeout" in error_msg.lower() or "deadline" in error_msg.lower():
            return "ขอโทษนะคะ ฟลาวี่คิดช้าไปหน่อย ลองถามใหม่ได้เลยค่ะ"
        elif "quota" in error_msg.lower() or "rate" in error_msg.lower():
            return "ขอโทษนะคะ ตอนนี้ฟลาวี่ตอบคำถามมากไปหน่อย รอสักครู่แล้วถามใหม่นะคะ"
        else:
            return "ขอโทษนะคะ ฟลาวี่ตอบไม่ได้ตอนนี้"


# ===== CHANGED: Complete rewrite of serial_thread for robust parsing =====
def serial_thread():
    global latest_sensor
    try:
        ser = serial.Serial(SERIAL_PORT, SERIAL_BAUD, timeout=1)
        print("Serial port connected!")
        print("Waiting for Arduino data...")
    except Exception as e:
        print(f"Could not connect to serial port: {e}")
        print("Running without sensor data")
        return
    
    # Track last known values to avoid spam
    last_known_data = {"nickname": None, "species": None, "age": None}
    
    while True:
        try:
            # Read line and decode with error handling
            line = ser.readline().decode('utf-8', errors='ignore').strip()
            
            if not line:
                continue
            
            # Arduino sends: "Temp:25.50,Humidity:60.30,Lux:1234.56,Soil:512,Nickname:Flowy,Species:Chilli,Age:1 day"
            parts = line.split(",")
            
            for part in parts:
                if ":" not in part:
                    continue
                
                try:
                    key, value = part.split(":", 1)
                    key = key.strip()
                    value = value.strip()
                    
                    if key == "Temp":
                        latest_sensor["temp"] = float(value)
                    elif key == "Humidity":
                        latest_sensor["humidity"] = float(value)
                    elif key == "Lux":
                        latest_sensor["lux"] = float(value)
                    elif key == "Soil":
                        latest_sensor["soil"] = int(value)
                    elif key == "Nickname":
                        latest_sensor["nickname"] = value
                    elif key == "Species":
                        latest_sensor["species"] = value
                    elif key == "Age":
                        latest_sensor["age"] = value
                        
                except (ValueError, IndexError) as e:
                    print(f"Error parsing {part}: {e}")
                    continue

            # Only print when plant data changes (not every 2 seconds!)
            if (latest_sensor["nickname"] and latest_sensor["species"] and
                (latest_sensor["nickname"] != last_known_data["nickname"] or
                 latest_sensor["species"] != last_known_data["species"] or
                 latest_sensor["age"] != last_known_data["age"])):
                
                print(f"\n🌱 Plant Info Updated:")
                print(f"   Name: {latest_sensor['nickname']}")
                print(f"   Species: {latest_sensor['species']}")
                print(f"   Age: {latest_sensor['age']}\n")
                
                last_known_data["nickname"] = latest_sensor["nickname"]
                last_known_data["species"] = latest_sensor["species"]
                last_known_data["age"] = latest_sensor["age"]
            
        except UnicodeDecodeError:
            continue
        except Exception as e:
            print(f"Serial read error: {e}")
            time.sleep(0.1)


def speech_thread():
    global user_question, is_speaking, is_awake, awake_timeout, DEVICE_INDEX, is_hearing_sound, last_sound_time, is_processing
    try:
        device_index, max_channels, native_sample_rate = find_input_device()
        DEVICE_INDEX = device_index
        channels = 1
        print(f"\nUsing sample rate: {native_sample_rate} Hz (native) -> 16000 Hz (for Vosk)")
    except Exception as e:
        print(f"Could not find audio device: {e}")
        return
    try:
        model_vosk = Model(MODEL_PATH)
        rec = KaldiRecognizer(model_vosk, 16000)
        rec.SetWords(True)
        print("Vosk model loaded successfully!")
    except Exception as e:
        print(f"Could not load Vosk model: {e}")
        return
    audio_q = queue.Queue(maxsize=10)

    def audio_callback(indata, frames, time_info, status):
        global is_hearing_sound, last_sound_time
        if not is_speaking and not is_processing:
            try:
                import numpy as np
                audio_array = np.frombuffer(indata, dtype=np.int16)
                if native_sample_rate != 16000:
                    ratio = 16000 / native_sample_rate
                    new_length = int(len(audio_array) * ratio)
                    old_indices = np.arange(len(audio_array))
                    new_indices = np.linspace(0, len(audio_array) - 1, new_length)
                    audio_array = np.interp(new_indices, old_indices, audio_array).astype(np.int16)
                volume = np.abs(audio_array).mean()
                if volume > 100:
                    is_hearing_sound = True
                    last_sound_time = time.time()
                audio_q.put_nowait(bytes(audio_array))
            except queue.Full:
                try:
                    audio_q.get_nowait()
                    audio_q.put_nowait(bytes(audio_array))
                except:
                    pass
            except Exception as e:
                print(f"Audio callback error: {e}")

    try:
        with sd.RawInputStream(samplerate=native_sample_rate, blocksize=int(4000 * native_sample_rate / 16000), device=DEVICE_INDEX, dtype="int16", channels=channels, callback=audio_callback):
            print("")
            print("=" * 78)
            print(f"\nFlowy is ready!")
            print(f"Say '{WAKE_WORD}' to wake Flowy up!")
            print(f"Say '{GOODBYE_WORD}' to put Flowy back to sleep!\n")
            print("=" * 78)
            print("")
            while True:
                if is_speaking or is_processing:
                    while not audio_q.empty():
                        try:
                            audio_q.get_nowait()
                        except:
                            break
                    rec.Reset()
                    time.sleep(0.05)
                    continue
                try:
                    data = audio_q.get(timeout=0.05)
                except queue.Empty:
                    continue
                if rec.AcceptWaveform(data):
                    result = json.loads(rec.Result())
                    text = result.get("text", "").strip()
                    if text and not is_speaking and not is_processing:
                        with speech_lock:
                            if not is_speaking and not is_processing:
                                if GOODBYE_WORD in text:
                                    if is_awake and not is_processing:
                                        is_awake = False
                                        is_processing = True
                                        print(f"Going to sleep...")
                                        print(f"Say '{WAKE_WORD}' to wake up again\n")
                                        print("=" * 78 + "\n")
                                        speak_thai("ราตรีสวัสดิ์ค่ะ")
                                        is_processing = False
                                    else:
                                        print(f"Ignored (already sleeping or busy): {text}")
                                    continue
                                if WAKE_WORD in text:
                                    if not is_processing:
                                        is_awake = True
                                        awake_timeout = time.time() + AWAKE_DURATION
                                        is_processing = True
                                        print(f"Waking up!")
                                        while not audio_q.empty():
                                            try:
                                                audio_q.get_nowait()
                                            except:
                                                break
                                        rec.Reset()
                                        speak_thai("ยินดีต้อนรับค่ะ")
                                        time.sleep(0.5)
                                        while not audio_q.empty():
                                            try:
                                                audio_q.get_nowait()
                                            except:
                                                break
                                        rec.Reset()
                                        is_processing = False
                                    continue
                                if is_awake and not is_processing:
                                    question_text = text.replace(WAKE_WORD, "").strip()
                                    if question_text:
                                        user_question = question_text
                                        awake_timeout = time.time() + AWAKE_DURATION
                                else:
                                    if not is_awake:
                                        print(f"Ignored (sleeping): {text}")
                                    else:
                                        print(f"Ignored (busy): {text}")
    except Exception as e:
        print(f"Error in audio stream: {e}")
        import traceback
        traceback.print_exc()


def main():
    global user_question, is_awake, awake_timeout, is_processing, is_thinking
    gui_thread_obj = threading.Thread(target=gui_thread, daemon=False)
    gui_thread_obj.start()
    time.sleep(1)
    threading.Thread(target=serial_thread, daemon=True).start()
    threading.Thread(target=speech_thread, daemon=True).start()
    while True:
        if is_awake and time.time() > awake_timeout and not is_processing:
            is_awake = False
            is_processing = True
            print(f"Auto-sleep due to inactivity...")
            print(f"Say '{WAKE_WORD}' to wake up again\n")
            print("=" * 78 + "\n")
            speak_thai("ไม่มีอะไรแล้วฟลาวี่ขอไปนอนก่อนนะ")
            while is_speaking:
                time.sleep(0.1)
            is_processing = False
        if user_question and not is_speaking:
            with speech_lock:
                if user_question:
                    current_question = user_question
                    user_question = None
                else:
                    continue
            print(f"[User]: {current_question}")
            is_processing = True
            is_thinking = True
            
            # เพิ่ม timeout และ error handling
            gemini_future = executor.submit(ask_gemini, current_question, latest_sensor)
            
            try:
                # เพิ่ม timeout เป็น 20 วินาที (15 วิสำหรับ API + 5 วิ buffer)
                answer = gemini_future.result(timeout=20)
            except TimeoutError:
                print("⚠️ Gemini timeout after 20 seconds")
                answer = "ขอโทษนะคะ ฟลาวี่คิดช้าไปหน่อย ลองถามใหม่ได้เลยค่ะ"
            except Exception as e:
                print(f"⚠️ Unexpected error: {e}")
                answer = "ขอโทษนะคะ ฟลาวี่ตอบไม่ได้ตอนนี้"
            
            print(f"[Flowy]: {answer}\n")
            print("=" * 78 + "\n")
            speak_thai(answer)
            while is_speaking:
                time.sleep(0.1)
            time.sleep(0.5)
            is_processing = False
            awake_timeout = time.time() + AWAKE_DURATION
        time.sleep(0.05)


if __name__ == "__main__":
    main()