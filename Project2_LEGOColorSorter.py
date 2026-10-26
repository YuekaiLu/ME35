from machine import Pin, SoftI2C, PWM
import time
import math

# Servo Stuff

slide_servo = PWM(Pin(4), freq=50, duty_u16=0)
bump_servo  = PWM(Pin(5), freq=50, duty_u16=0)

slide_servo_cal = {"us_0": 450, "us_90": 1450, "us_180": 2400}
bump_servo_cal = {"us_0": 500, "us_90": 1500, "us_180": 2450}

CLASS_ANGLES1 = {
    "color 1": 75,
    "color 2": 75,
    "color 3": 105,
    "no clue": 105,
}

CLASS_ANGLES2 = {
    "color 1": 65,
    "color 2": 115,
    "color 3": 65,
    "no clue": 115,
}

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
    
def reset_angles(servo, cal):
    servo.duty_ns(cal["us_90"]*1000)

reset_angles(slide_servo, slide_servo_cal)
reset_angles(bump_servo, bump_servo_cal)
time.sleep(0.5)  # give them time to get there

# Color and Button Stuff
button_Play = Pin(34, Pin.IN, Pin.PULL_UP)
button_Train = Pin(35, Pin.IN, Pin.PULL_UP)

i2c = SoftI2C(scl = Pin(22), sda = Pin(21))
print(i2c.scan())

pressed_flag = False
STATE_PLAY = False
STATE_TRAIN = True

def playButton(p):
    global STATE_PLAY
    global STATE_TRAIN
    STATE_PLAY = True
    STATE_TRAIN = False

def trainButton(p):
    global pressed_flag
    global STATE_TRAIN
    STATE_TRAIN = True
    pressed_flag = True

button_Train.irq(trigger=Pin.IRQ_RISING, handler=trainButton)
button_Play.irq(trigger=Pin.IRQ_RISING, handler=playButton)

import veml6040
sensor = veml6040.VEML6040(i2c)

sensor.trigger_measurement()
   
def k_nearest_neighbor(x,y,z, k =1):
    distances = []
    for index, d in enumerate(data):
        dist = math.sqrt((x-d[0])**2+(y-d[1])**2+(z-d[2])**2)
        distances.append([dist,d[3]])
    
    distances.sort()
    distances = distances[:k] #get k distances
    classes = []
    for dist in distances:
        classes.append(dist[1])
    print("k classes", classes)
    most_number_of_closest_classes = max(set(classes), key = classes.count)
    print("max classes ", most_number_of_closest_classes)
    
    return most_number_of_closest_classes
       
data = []
color = ""
index = 0

while True:
    color1, color2, color3, NA = sensor.read_rgbw()

    if STATE_TRAIN and pressed_flag:
        print(color1, color2, color3, NA)
        index = index + 1
        if index <= 5:
            color = "color 1"
        elif index > 5 and index <= 10:
            color = "color 2"
        elif index > 10 and index <= 15:
            color = "color 3"
        else:
            color = "no clue"

        data.append((color1, color2, color3, color))
        pressed_flag = False

    if STATE_PLAY:
        
        if not data:
           print("No training data yet")
           STATE_PLAY = False
           continue
        
        what_class = k_nearest_neighbor(color1, color2, color3, 3)
        print(what_class)

        angle1 = CLASS_ANGLES1.get(what_class)
        angle2 = CLASS_ANGLES2.get(what_class)
        
        if angle1 is not None and angle2 is not None:
            set_servo_angle(slide_servo, angle1, slide_servo_cal)
            
            set_servo_angle(bump_servo, angle2, bump_servo_cal)
            time.sleep(0.4)
            reset_angles(slide_servo, slide_servo_cal)
            reset_angles(bump_servo, bump_servo_cal)
        
        STATE_PLAY = False

    time.sleep(0.1)

slide_servo.deinit()
bump_servo.deinit()