from machine import Pin, I2C
from machine import SoftI2C

i2c = I2C(0, sda=Pin(21), scl=Pin(22), freq=400000)

# you can also use softI2C on regular GPIO pins
#i2c = SoftI2C(scl = Pin(33), sda = Pin(25))

print(i2c.scan())
import time

#importing color sensor library
import veml6040
# create color sensor object
sensor = veml6040.VEML6040(i2c)

#check the color sensor library on what this means
sensor.trigger_measurement()
while True:
    red, green, blue, white = sensor.read_rgbw()
    print(red, green, blue, white)
    time.sleep(0.1)
