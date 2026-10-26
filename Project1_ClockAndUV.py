from machine import PWM, Pin
import time
import network
import urequests
import Connect_tufts_eecs
import Project1_API

# Servo setup
second_servo = PWM(Pin(4))   # second hand (clock mode) / Madrid UV (UV mode)
minute_servo = PWM(Pin(5))   # minute hand (clock mode) / Boston UV (UV mode)
second_servo.freq(50)
minute_servo.freq(50)

second_servo_cal = {"us_0": 500, "us_90": 1500, "us_180": 2450}
minute_servo_cal = {"us_0": 500, "us_90": 1450, "us_180": 2450}

def set_servo_angle(servo, angle, cal):
    """Piecewise-linear pulse mapping using this servo's measured calibration points."""
    angle = max(0, min(180, angle))
    if angle <= 90:
        frac = angle / 90
        pulse_us = cal["us_0"] + frac * (cal["us_90"] - cal["us_0"])
    else:
        frac = (angle - 90) / 90
        pulse_us = cal["us_90"] + frac * (cal["us_180"] - cal["us_90"])
    servo.duty_ns(int(pulse_us * 1000))

def clock_angle_to_servo_angle(clock_angle):
    # servo=0 -> 12:00, linear, clockwise sweep, 2:1 gear ratio
    return max(0, min(180, clock_angle / 2))

def uv_to_servo_angle(uv_index, uv_max=11):
    uv_index = max(0, min(uv_max, uv_index))
    return (uv_index / uv_max) * 180

# Mode State
MODE_CLOCK = "clock"
MODE_UV = "uv"
mode = MODE_CLOCK

# Button Set Up
button_uv = Pin(34, Pin.IN, Pin.PULL_UP)
button_clock = Pin(35, Pin.IN, Pin.PULL_UP)

uv_flag = False
clock_flag = False
last_uv_press = 0
last_clock_press = 0
DEBOUNCE_MS = 200

def uv_button_handler(pin):
    global uv_flag, last_uv_press
    now = time.ticks_ms()
    if time.ticks_diff(now, last_uv_press) > DEBOUNCE_MS:
        uv_flag = True
        last_uv_press = now

def clock_button_handler(pin):
    global clock_flag, last_clock_press
    now = time.ticks_ms()
    if time.ticks_diff(now, last_clock_press) > DEBOUNCE_MS:
        clock_flag = True
        last_clock_press = now

button_uv.irq(trigger=Pin.IRQ_FALLING, handler=uv_button_handler)
button_clock.irq(trigger=Pin.IRQ_FALLING, handler=clock_button_handler)

# Time API
def get_time(timezone="America/New_York"):
    url = f"https://world-time-api3.p.rapidapi.com/timezone/{timezone}"
    headers = {
        "X-RapidAPI-Key": Project1_API.API_Token,
        "X-RapidAPI-Host": "world-time-api3.p.rapidapi.com"
    }
    response = urequests.get(url, headers=headers)
    data = response.json()
    response.close()
    return data

# Globals updated by sync_time_from_api(); used by the clock-mode display loop
start_ticks = 0
start_total_seconds = 0

def sync_time_from_api():
    """Fetch current time from API and reset the internal reference point.
    Called at startup, and again every time we switch back into clock mode."""
    global start_ticks, start_total_seconds
    try:
        result = get_time("America/New_York")
        print("Synced time:", result["datetime"])
        dt = result["datetime"]
        h = int(dt[11:13])
        m = int(dt[14:16])
        s = int(dt[17:19])
        start_ticks = time.ticks_ms()
        start_total_seconds = h * 3600 + m * 60 + s
    except Exception as e:
        # Keep running on the old reference point rather than crashing
        # if wifi/API hiccups during a resync
        print("Time sync failed, keeping previous reference:", e)

sync_time_from_api()   # initial fetch at startup

# UV API
BOSTON_LAT, BOSTON_LNG = 42.361145, -71.057083
MADRID_LAT, MADRID_LNG = 40.4168, 3.7038

def get_uv(lat, lng):
    url = f"https://uvindexapi.com/api/v1/forecast?latitude={lat}&longitude={lng}&timezone=Auto"
    response = urequests.get(url)
    data = response.json()
    response.close()
    return data

def extract_current_uv(data):
    # PLACEHOLDER -- confirm actual JSON shape from uvindexapi.com and fix this
    return data["now"]["uv_index"]

current_boston_uv = None
current_madrid_uv = None

def refresh_uv():
    global current_boston_uv, current_madrid_uv, last_uv_ticks
    try:
        current_boston_uv = extract_current_uv(get_uv(BOSTON_LAT, BOSTON_LNG))
        print("Boston UV:", current_boston_uv)
    except Exception as e:
        print("Boston UV fetch failed:", e)
    try:
        current_madrid_uv = extract_current_uv(get_uv(MADRID_LAT, MADRID_LNG))
        print("Madrid UV:", current_madrid_uv)
    except Exception as e:
        print("Madrid UV fetch failed:", e)
    last_uv_ticks = time.ticks_ms()

    if mode == MODE_UV:
        if current_boston_uv is not None:
            set_servo_angle(minute_servo, uv_to_servo_angle(current_boston_uv), minute_servo_cal)
        if current_madrid_uv is not None:
            set_servo_angle(second_servo, uv_to_servo_angle(current_madrid_uv), second_servo_cal)

UV_REFRESH_MS = 15 * 60 * 1000
last_uv_ticks = time.ticks_ms() - UV_REFRESH_MS  # force fetch on first entry to UV mode

# Main Loop
last_clock_update_ticks = 0
CLOCK_UPDATE_MS = 200

while True:
    now = time.ticks_ms()

    # Handles mode switch button presses
    if uv_flag:
        uv_flag = False
        if mode != MODE_UV:
            mode = MODE_UV
            print("Switched to UV mode")
            refresh_uv()

    if clock_flag:
        clock_flag = False
        if mode != MODE_CLOCK:
            mode = MODE_CLOCK
            print("Switched to clock mode, resyncing time...")
            sync_time_from_api()   # resyncs here

    # Clock mode: update hands from elapsed time since last sync
    if mode == MODE_CLOCK and time.ticks_diff(now, last_clock_update_ticks) >= CLOCK_UPDATE_MS:
        elapsed_seconds = time.ticks_diff(now, start_ticks) / 1000
        current_total_seconds = start_total_seconds + elapsed_seconds
        current_second = current_total_seconds % 60
        current_minute = (current_total_seconds // 60) % 60

        second_angle = current_second * 6
        minute_angle = (current_minute + current_second / 60) * 6

        set_servo_angle(second_servo, clock_angle_to_servo_angle(second_angle), second_servo_cal)
        set_servo_angle(minute_servo, clock_angle_to_servo_angle(minute_angle), minute_servo_cal)

        last_clock_update_ticks = now

    # UV mode: refresh while sitting in UV mode
    if mode == MODE_UV and time.ticks_diff(now, last_uv_ticks) >= UV_REFRESH_MS:
        refresh_uv()

    time.sleep_ms(10)