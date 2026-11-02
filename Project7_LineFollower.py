from machine import Pin, PWM, I2C, SoftI2C
import time
import json

# ---------------- Settings ----------------
TRAIN_FILE = "training.json"   # where the KNN training data is saved on the ESP32
FORCE_RETRAIN = False   # True = ignore the saved file and train again (set back to False after)
USE_STATION = True

STATION_MAC = b'\xc8\x85\x41\xc5\x62\xc8'

# KNN
K = 5                   # neighbors that vote
CHROMA_WEIGHT = 3.0    
BRIGHT_WEIGHT = 1.0   
CONFIRM_READS = 5       

# Driving
BASE_SPEED = 40        # 0-255
CURVE_SLOWDOWN = 0.1   # 0 = constant speed; 0.35 = lose up to 35% speed in hard turns

# PID (error is -1..+1, output in motor speed units)
KP = 60
KI = 0.0
KD = 1
I_LIMIT = 1.0
MAX_STEER = 220
STEER_SIGN = -1

LOOP_MS = 10            # control loop period

# After a color action the bot drives straight until both sensors are off the patch
CLEAR_MIN_MS = 300      # always drive at least this long
CLEAR_MAX_MS = 1500     # give up waiting after this long and resume steering
LOCKOUT_MS = 800        # then ignore colors this long so the patch isn't triggered twice

# ---------------- Sensors ----------------
VEML_ADDR = 0x10
RED, GREEN, BLUE, NONE = 1, 2, 3, 0
LBL_WHITE, LBL_BLACK, LBL_EDGE = 0, 4, 5

i2c_right = I2C(0, scl=Pin(22), sda=Pin(21), freq=100000)   # RIGHT sensor: Grove port 2

def find_left_bus():
    for sda, scl in ((33, 32), (32, 33)):                    # LEFT sensor: Grove port 5
        bus = SoftI2C(scl=Pin(scl), sda=Pin(sda), freq=100000)
        if VEML_ADDR in bus.scan():
            return bus
    return None

if VEML_ADDR not in i2c_right.scan():
    raise RuntimeError("Right color sensor not found on Grove port 2")
i2c_left = find_left_bus()
if i2c_left is None:
    raise RuntimeError("Left color sensor not found on Grove port 5")

def veml_init(bus):
    bus.writeto_mem(VEML_ADDR, 0x00, bytes([0x00, 0x00]))   # 40 ms, sensor on

def read_word(bus, reg):
    d = bus.readfrom_mem(VEML_ADDR, reg, 2)
    return d[0] | (d[1] << 8)

def read_rgbw(bus):
    return (read_word(bus, 0x08), read_word(bus, 0x09),
            read_word(bus, 0x0A), read_word(bus, 0x0B))

# ---------------- Motors (Robo ESP32 driver) ----------------
#   A=PWM, B=low -> forward     A=low, B=PWM -> backward     both low -> brake
M1A = PWM(Pin(12), freq=10000, duty_u16=0)
M1B = PWM(Pin(13), freq=10000, duty_u16=0)
M2A = PWM(Pin(14), freq=10000, duty_u16=0)
M2B = PWM(Pin(27), freq=10000, duty_u16=0)

def drive(pa, pb, speed):
    speed = max(-255, min(255, int(speed)))
    if speed >= 0:
        pa.duty_u16(speed * 257)
        pb.duty_u16(0)
    else:
        pa.duty_u16(0)
        pb.duty_u16(-speed * 257)

def set_motors(left, right):
    drive(M1A, M1B, left)
    drive(M2A, M2B, right)

def stop_motors():
    set_motors(0, 0)

# ---------------- LED, buzzer, servos ----------------
led = Pin(17, Pin.OUT)
buzzer = PWM(Pin(23))
buzzer.duty_u16(0)
servo1 = PWM(Pin(18), freq=50)
servo2 = PWM(Pin(19), freq=50)

def tone(freq):
    buzzer.freq(freq)
    buzzer.duty_u16(32768)

def no_tone():
    buzzer.duty_u16(0)

def servo_angle(servo, angle):
    us = 500 + (angle / 180) * 2000          # 0.5 - 2.5 ms pulse
    servo.duty_u16(int(us / 20000 * 65535))

# ---------------- KNN color classifier ----------------
class KNN:
    def __init__(self, data):
        self.wmax = max(row[4] for row in data) or 1
        self.rows = []
        for lab, r, g, b, w in data:
            f = self.features(r, g, b, w)
            self.rows.append((f[0], f[1], f[2], f[3], lab))
        white = [row[4] for row in data if row[0] == LBL_WHITE]
        black = [row[4] for row in data if row[0] == LBL_BLACK]
        self.white_w = sum(white) / len(white) if white else self.wmax
        self.black_w = sum(black) / len(black) if black else 0

    def features(self, r, g, b, w):
        total = r + g + b
        if total < 1:
            return (0.0, 0.0, 0.0, 0.0)
        return (CHROMA_WEIGHT * r / total,
                CHROMA_WEIGHT * g / total,
                CHROMA_WEIGHT * b / total,
                BRIGHT_WEIGHT * w / self.wmax)

    def predict(self, r, g, b, w):
        f0, f1, f2, f3 = self.features(r, g, b, w)
        dists = []
        for a0, a1, a2, a3, lab in self.rows:
            dists.append(((a0 - f0) ** 2 + (a1 - f1) ** 2 +
                          (a2 - f2) ** 2 + (a3 - f3) ** 2, lab))
        dists.sort()
        nearest = dists[:K]
        votes = {}
        for d, lab in nearest:
            votes[lab] = votes.get(lab, 0) + 1
        best = max(votes.values())
        for d, lab in nearest:                  # on a tie, the closest winner takes it
            if votes[lab] == best:
                return lab

def color_of(label):
    return label if label in (RED, GREEN, BLUE) else NONE

# ---------------- Training data (saved to / loaded from JSON) ----------------
def collect_training_data():
    plan = [
        ("WHITE paper", LBL_WHITE, 15),
        ("BLACK tape", LBL_BLACK, 15),
        ("tape EDGE - slowly slide it back and forth across the edge", LBL_EDGE, 30),
        ("RED patch - slowly slide it across the patch", RED, 20),
        ("GREEN patch - slowly slide it across the patch", GREEN, 20),
        ("BLUE patch - slowly slide it across the patch", BLUE, 20),
    ]
    results = {"LEFT": [], "RIGHT": []}
    for name, bus in (("LEFT", i2c_left), ("RIGHT", i2c_right)):
        for desc, label, n in plan:
            print("\n>> %s sensor over %s" % (name, desc))
            for c in (4, 3, 2, 1):
                print("   starting in", c)
                time.sleep(1)
            led.value(1); tone(1500); time.sleep_ms(120); no_tone()
            for _ in range(n):
                r, g, b, w = read_rgbw(bus)
                results[name].append((label, r, g, b, w))
                time.sleep_ms(60)
            led.value(0)
            print("   done")
    data = {"left": results["LEFT"], "right": results["RIGHT"]}
    with open(TRAIN_FILE, "w") as f:
        json.dump(data, f)
    print("\nSaved %d + %d samples to %s" % (len(data["left"]), len(data["right"]), TRAIN_FILE))
    return data

def load_training_data():
    if not FORCE_RETRAIN:
        try:
            with open(TRAIN_FILE) as f:
                data = json.load(f)
            if data.get("left") and data.get("right"):
                print("Loaded training data from", TRAIN_FILE)
                return data
        except (OSError, ValueError):
            pass                                  # missing / unreadable / old format -> train
    print("Starting training." if FORCE_RETRAIN else "No usable training data - starting training.")
    time.sleep(2)
    data = collect_training_data()
    print("Training done. Place the bot on the track.")
    time.sleep(5)
    return data

# ---------------- ESP-NOW (sends "Perry has reached <color>") ----------------
now = None
if USE_STATION:
    import network
    import espnow
    sta = network.WLAN(network.WLAN.IF_STA)   # a WLAN interface must be active to send
    sta.active(True)
    sta.disconnect()
    now = espnow.ESPNow()
    now.active(True)
    now.add_peer(STATION_MAC)                 # must add_peer() before send()

COLOR_NAMES = {RED: "red", GREEN: "green", BLUE: "blue"}

def send_to_station(color):
    if not USE_STATION:
        return
    msg = "Perry has reached %s" % COLOR_NAMES[color]
    try:
        ok = now.send(STATION_MAC, msg)
        print("Sent:", msg, "(delivered)" if ok else "(no ack from receiver)")
    except OSError as e:
        print("send error:", e)

# ---------------- Actions ----------------
def action_red():
    for _ in range(4):                       # servos oscillate 0 <-> 90
        servo_angle(servo1, 90); servo_angle(servo2, 90)
        time.sleep_ms(350)
        servo_angle(servo1, 0); servo_angle(servo2, 0)
        time.sleep_ms(350)

def action_green():
    tone(1000)
    time.sleep_ms(1200)
    no_tone()

def action_blue():
    for _ in range(6):
        led.value(1); time.sleep_ms(150)
        led.value(0); time.sleep_ms(150)

def handle_color(c):
    stop_motors()
    send_to_station(c)            # message goes out BEFORE the action
    if c == RED:
        action_red()
    elif c == GREEN:
        action_green()
    elif c == BLUE:
        action_blue()
    led.value(0)

def read_colors():
    lr, lg, lb, lw = read_rgbw(i2c_left)
    rr, rg, rb, rw = read_rgbw(i2c_right)
    left_color = color_of(knn_l.predict(lr, lg, lb, lw))
    right_color = color_of(knn_r.predict(rr, rg, rb, rw))
    return lw, rw, left_color, right_color

def drive_forward_until_clear():
    set_motors(BASE_SPEED, BASE_SPEED)
    t0 = time.ticks_ms()
    clear_reads = 0
    while True:
        elapsed = time.ticks_diff(time.ticks_ms(), t0)
        if elapsed >= CLEAR_MAX_MS:
            break
        time.sleep_ms(LOOP_MS)
        if elapsed >= CLEAR_MIN_MS:
            lw, rw, lc, rc = read_colors()
            clear_reads = clear_reads + 1 if (lc == NONE and rc == NONE) else 0
            if clear_reads >= 2:
                break

def norm(w, white, black):
    # 0 = fully over black, 1 = fully over white
    span = (white - black) or 1
    return max(0.0, min(1.0, (w - black) / span))

# ---------------- Main ----------------
veml_init(i2c_left)
veml_init(i2c_right)
servo_angle(servo1, 0)
servo_angle(servo2, 0)
stop_motors()

training = load_training_data()
knn_l = KNN(training["left"])
knn_r = KNN(training["right"])
WHITE_L, BLACK_L = knn_l.white_w, knn_l.black_w     # taken from the training data
WHITE_R, BLACK_R = knn_r.white_w, knn_r.black_w
print("Left white/black W: %d / %d   Right: %d / %d"
      % (WHITE_L, BLACK_L, WHITE_R, BLACK_R))

time.sleep(2)                      # time to place the bot on the track

last_seen = NONE
seen_count = 0
last_trigger = None                # time the last color action finished
last_e = 0.0                       # last good error (held while a sensor is on a patch)
integral = 0.0
prev_error = 0.0
last_t = time.ticks_ms()

while True:
    # ---- fixed-rate loop so the derivative term is meaningful ----
    elapsed = time.ticks_diff(time.ticks_ms(), last_t)
    if elapsed < LOOP_MS:
        time.sleep_ms(LOOP_MS - elapsed)
    t_now = time.ticks_ms()
    dt = min(time.ticks_diff(t_now, last_t) / 1000.0, 0.2)
    last_t = t_now

    lw, rw, left_color, right_color = read_colors()
    color = left_color if left_color != NONE else right_color

    in_lockout = (last_trigger is not None and
                  time.ticks_diff(time.ticks_ms(), last_trigger) < LOCKOUT_MS)

    # ---- color detection (either sensor) ----
    if not in_lockout and color != NONE and color == last_seen:
        seen_count += 1
    else:
        seen_count = 1 if (not in_lockout and color != NONE) else 0
    last_seen = color if not in_lockout else NONE

    if seen_count >= CONFIRM_READS:
        handle_color(color)                    # stop, send message, do the action
        drive_forward_until_clear()            # keep moving forward, back onto the line
        last_trigger = time.ticks_ms()
        seen_count = 0
        last_seen = NONE
        integral = 0.0                         # start PID fresh
        prev_error = last_e
        last_t = time.ticks_ms()
        continue

    # ---- error: left brightness minus right brightness ----
    # (positive = line is toward the right sensor = turn right)
    if color == NONE:
        e = norm(lw, WHITE_L, BLACK_L) - norm(rw, WHITE_R, BLACK_R)
        last_e = e
    else:
        e = last_e                             # a sensor is on a patch: hold last good value

    # ---- PID ----
    integral += e * dt
    integral = max(-I_LIMIT, min(I_LIMIT, integral))
    derivative = (e - prev_error) / dt
    prev_error = e

    steer = (KP * e) + (KI * integral) + (KD * derivative)
    steer = max(-MAX_STEER, min(MAX_STEER, steer)) * STEER_SIGN

    # slow down in proportion to how hard it is turning
    speed = BASE_SPEED * (1 - CURVE_SLOWDOWN * min(1.0, abs(steer) / MAX_STEER))
    set_motors(speed + steer, speed - steer)