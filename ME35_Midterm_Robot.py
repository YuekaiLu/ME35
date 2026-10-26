from machine import ADC, Pin, I2C, PWM
import struct, time, math, json, os

# =====================================================================
# PINS AND SETTINGS (change these to match your wiring)
# =====================================================================
JOY_X, JOY_Y = 39, 32      # X = the axis that spikes on click, Y = up/down
SDA, SCL = 21, 22
SERVO_PIN = 4             # <-- set to your servo pin
MOTOR_PIN = 12             # <-- set to your motor driver pin

DEMO = True                # True: minutes become seconds for testing
UNIT = 1000 if DEMO else 60000
SESSIONS = [1, 15, 25, 45]
PETS_NEEDED = 8

DEADZONE, CLICK_LEVEL = 300, 3450   # use the CLICK_LEVEL you measured

# KNN settings
LABELS = ["STILL", "BUMP", "FACEDOWN"]
ESCALATE_BUMPS = 3         # this many bumps within ESCALATE_MS = betrayal
ESCALATE_MS = 4000
WIN = 15                   # samples per window (15 x 20 ms = 300 ms)
WINDOWS_PER_RECORDING = 8  # windows captured each time you click in training
MIN_PER_LABEL = 8          # need at least this many samples per label
K = 3
DATA_FILE = "knn2.json"

# =====================================================================
# SERVO TAIL AND PURR MOTOR
# =====================================================================
servo = PWM(Pin(SERVO_PIN), freq=50)
def set_angle(deg):
    deg = max(0, min(180, deg))
    ms = 0.5 + (deg / 180) * 2.0
    servo.duty_u16(int(ms / 20 * 65535))

def tail(center, amp, period):
    return center + amp * math.sin(2 * math.pi * time.ticks_ms() / period)

motor = PWM(Pin(MOTOR_PIN), freq=1000)
def purr(on):
    motor.duty_u16(65535 if on else 0)

purr(False)                # make sure the motor starts off

# =====================================================================
# JOYSTICK AND ACCELEROMETER
# =====================================================================
jx = ADC(Pin(JOY_X)); jy = ADC(Pin(JOY_Y))
jx.atten(ADC.ATTN_11DB); jy.atten(ADC.ATTN_11DB)

i2c = I2C(0, scl=Pin(SCL), sda=Pin(SDA), freq=400000)
ADDR = 0x19
i2c.writeto_mem(ADDR, 0x20, b'\x57')   # 100 Hz, X/Y/Z on
i2c.writeto_mem(ADDR, 0x23, b'\x88')   # +/-2g, high-res

def read_accel():
    raw = i2c.readfrom_mem(ADDR, 0x28 | 0x80, 6)
    x, y, z = struct.unpack('<hhh', raw)
    return (x >> 4) / 1000, (y >> 4) / 1000, (z >> 4) / 1000

def since(t):
    return time.ticks_diff(time.ticks_ms(), t)

# =====================================================================
# KNN
# =====================================================================
def features(buf):
    """Turn a window of (x,y,z) samples into 3 numbers."""
    n = len(buf)
    mean_z = sum(s[2] for s in buf) / n
    devs = [abs(math.sqrt(s[0]**2 + s[1]**2 + s[2]**2) - 1) for s in buf]
    return [mean_z, max(devs), sum(devs) / n]

data = []       # list of [features, label]
scale = [1, 1, 1]

def update_scale():
    """Range of each feature across the training data, used to normalize."""
    global scale
    scale = []
    for d in range(3):
        vals = [s[0][d] for s in data]
        r = max(vals) - min(vals)
        scale.append(r if r > 0.001 else 1)

def classify(f):
    dists = []
    for feats, label in data:
        d = 0
        for i in range(3):
            d += ((f[i] - feats[i]) / scale[i]) ** 2
        dists.append((d, label))
    dists.sort(key=lambda t: t[0])
    votes = {}
    for d, label in dists[:K]:
        votes[label] = votes.get(label, 0) + 1
    best = dists[0][1]                     # nearest neighbor breaks ties
    for label, v in votes.items():
        if v > votes[best]:
            best = label
    return best

def save_data():
    with open(DATA_FILE, "w") as f:
        json.dump(data, f)

def load_data():
    global data
    try:
        with open(DATA_FILE) as f:
            data = json.load(f)
        counts = {l: 0 for l in LABELS}
        for s in data:
            counts[s[1]] += 1
        if all(counts[l] >= MIN_PER_LABEL for l in LABELS):
            update_scale()
            return True
    except Exception:
        pass
    data = []
    return False

# =====================================================================
# TRAINING MODE
# =====================================================================
def record(label):
    print("Get ready to do:", label)
    time.sleep_ms(1500)                    # countdown before recording
    for w in range(WINDOWS_PER_RECORDING):
        buf = []
        for i in range(WIN):
            buf.append(read_accel())
            time.sleep_ms(20)
        data.append([features(buf), label])
    time.sleep_ms(300)
    print("Recorded", label)

def counts_now():
    c = {l: 0 for l in LABELS}
    for s in data:
        c[s[1]] += 1
    return c

def train(cy):
    global data
    data = []
    idx = 0
    press_start = None
    last_move = 0
    purr(False)                            # motor stays off during training
    print("=== TRAINING MODE ===")
    print("Push up/down to pick a label, short click = record,")
    print("hold click 2 s = save and finish.")
    while True:
        purr(False)                        # keep motor off every loop
        x, y = jx.read(), jy.read()
        click = x > CLICK_LEVEL
        dy = y - cy
        label = LABELS[idx]

        if not click and since(last_move) > 400:
            if dy > DEADZONE:
                idx = (idx + 1) % len(LABELS); last_move = time.ticks_ms()
                print("Label:", LABELS[idx], counts_now())
            elif dy < -DEADZONE:
                idx = (idx - 1) % len(LABELS); last_move = time.ticks_ms()
                print("Label:", LABELS[idx], counts_now())

        if click and press_start is None:
            press_start = time.ticks_ms()

        if not click and press_start is not None:
            dur = since(press_start)
            press_start = None
            if dur < 1000:
                record(label)
                print(counts_now())
            elif dur >= 1500:
                c = counts_now()
                if all(c[l] >= MIN_PER_LABEL for l in LABELS):
                    update_scale()
                    save_data()
                    print("Saved. Training done.")
                    time.sleep(1)
                    return
                else:
                    print("Need at least", MIN_PER_LABEL, "per label:", c)
                    time.sleep_ms(500)
        time.sleep_ms(20)

# =====================================================================
# BOOT: calibrate joystick, load or train KNN
# =====================================================================
held_at_boot = jx.read() > CLICK_LEVEL
if held_at_boot:
    print("Click held at boot: retraining")
    while jx.read() > CLICK_LEVEL:         # wait for release
        time.sleep_ms(20)
time.sleep_ms(300)
cx, cy = jx.read(), jy.read()

if held_at_boot or not load_data():
    train(cy)
else:
    print("Loaded KNN data:", counts_now())

# =====================================================================
# MAIN: state machine
# =====================================================================
state = "SETUP"
choice = 1
last_choice = 0
prev_click = False
focus_start = paused_at = break_start = 0
react_type, react_until = None, 0
bump_times = []
prev_label = "STILL"
pets, last_sign, last_pet = 0, 0, 0
buf = []
label = "STILL"

def go(new):
    global state
    state = new
    print("STATE:", new)

go("SETUP")
while True:
    x, y = jx.read(), jy.read()
    click = x > CLICK_LEVEL
    clicked = click and not prev_click
    prev_click = click
    dy = y - cy

    # accelerometer -> rolling window -> KNN label
    buf.append(read_accel())
    if len(buf) > WIN:
        buf.pop(0)
    if len(buf) == WIN:
        new_label = classify(features(buf))
        if new_label != label:
            label = new_label
            print("GESTURE:", label)

    if state == "SETUP":
        if since(last_choice) > 400 and not click:
            if dy > DEADZONE and choice < len(SESSIONS) - 1:
                choice += 1; last_choice = time.ticks_ms()
            elif dy < -DEADZONE and choice > 0:
                choice -= 1; last_choice = time.ticks_ms()
        set_angle(20 + choice * 140 / (len(SESSIONS) - 1))
        purr(False)
        if clicked:
            focus_start = time.ticks_ms()
            go("FOCUS")

    elif state == "FOCUS":
        total = SESSIONS[choice] * UNIT
        if label == "FACEDOWN":
            paused_at = time.ticks_ms()
            go("PAUSED")
        elif label == "BUMP" and prev_label != "BUMP":
            # a new bump just started: record it and drop old ones
            now = time.ticks_ms()
            bump_times.append(now)
            bump_times = [t for t in bump_times
                          if time.ticks_diff(now, t) < ESCALATE_MS]
            if len(bump_times) >= ESCALATE_BUMPS:
                react_type, react_until = "BETRAY", now + 1500
                bump_times = []
            elif react_type != "BETRAY":
                react_type, react_until = "ANNOYED", now + 800

        if react_type and time.ticks_diff(react_until, time.ticks_ms()) > 0:
            if react_type == "BETRAY":
                set_angle(tail(90, 80, 150))
            else:
                set_angle(tail(90, 30, 400))
        else:
            react_type = None
            set_angle(tail(60, 5, 6000))

        if since(focus_start) >= total:
            bump_times = []
            break_start = time.ticks_ms()
            pets, last_sign, last_pet = 0, 0, 0
            go("BREAK")

    elif state == "PAUSED":
        set_angle(0)
        if label != "FACEDOWN":
            focus_start = time.ticks_add(focus_start, since(paused_at))
            go("FOCUS")

    elif state == "BREAK":
        sign = 1 if dy > DEADZONE else (-1 if dy < -DEADZONE else 0)
        if sign != 0 and sign != last_sign:
            if last_sign != 0:
                pets += 1
            last_pet = time.ticks_ms()
        if sign != 0:
            last_sign = sign
        petting = since(last_pet) < 800 and pets > 0
        if petting:
            set_angle(tail(90, 20, 1500)); purr(True)
        else:
            set_angle(tail(90, 60, 500)); purr(False)
        if pets >= PETS_NEEDED:
            purr(False)
            set_angle(120)
            time.sleep(1.5)
            go("SETUP")

    prev_label = label
    time.sleep_ms(20)