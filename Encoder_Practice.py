from machine import Pin
from machine import PWM


class Count(object):
    def __init__(self,A,B):
        self.A = Pin(A, Pin.IN)
        self.B = Pin(B, Pin.IN)
        self.counter = 0

        self.A.irq(self.cb,self.A.IRQ_FALLING|self.A.IRQ_RISING) #interrupt on line A
        self.B.irq(self.cb,self.B.IRQ_FALLING|self.B.IRQ_RISING) #interrupt on line B


    def cb(self,msg):
        other,inc = (self.B,1) if msg == self.A else (self.A,-1) #define other line and increment
        self.counter += -inc if msg.value()!=other.value() else  inc 
        
    def value(self):
        print(self.counter)
        return self.counter
    
class Motor(Count):
    def __init__(self,m1,m2, A, B):
        self.enc = Count(A,B)
        self.M1 = PWM(m1, freq=50, duty_u16=0)
        self.M2 = PWM(m2, freq=50, duty_u16=0)
        self.stop()
        
    def pos(self):
        print(self.enc.value())        
            
    def stop(self):
        self.M1.duty_u16(0) 
        self.M2.duty_u16(0) 

    def start(self, direction = 0, speed = 50):
        if direction:
            self.M1.duty_u16(int(speed*65535/100)) 
            self.M2.duty_u16(0)
        else:
            self.M1.duty_u16(0)
            self.M2.duty_u16(int(speed*65535/100)) 
     
#IF you want to set up just the encoder use this
count = Count(22,21)
print(count.value())

#If you want to set up a motor with encoder
Motor1 = Motor(12,13, 21,22) #first two are motor pins, last two are encoder pins
Motor1.pos() # to read the encoder value for Motor 1