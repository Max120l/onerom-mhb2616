// PMD 85 Bluetooth joystick adapter -- a 4004/482 club stick on an ESP32.
//
// The club's JOYDEMO (dam+2.ptp) is the interface: the GPIO 8255 at
// 4Ch-4Fh in mode 0 with port A as input (OUT 4Fh,92h), PC4 raised as the
// stick's supply (OUT 4Eh,10h; 11h raises PC0 too, for a second stick on
// port B), then IN 4Ch, "bity jsou negovane": bit 0 down, bit 1 up,
// bit 2 right, bit 3 left, bit 4 fire, a pressed switch reading 0.  With
// nothing on the connector the port reads low on the machine -- every
// bit "pressed" -- so this adapter drives its five lines high when idle
// and low when pressed, instead of merely releasing them.
//
// Bluetooth gamepads come in through Bluepad32: d-pad or left stick for
// the four directions, any face button or shoulder button for fire.
// Gamepad 1 drives stick 1 on GPIO/0 (K3, port A), gamepad 2 drives
// stick 2 on GPIO/1 (K4, port B).  Pins and wiring are in README.md.
//
// Build: the "ESP32 + Bluepad32" board package (see README.md).

#include <Bluepad32.h>

// ---- wiring --------------------------------------------------------------
// ESP32 output pins, in the 8255's bit order: PA0/PB0 down, PA1/PB1 up,
// PA2/PB2 right, PA3/PB3 left, PA4/PB4 fire.  All below GPIO 32 so one
// register write sets the whole stick at once.  Each goes through a
// 470 ohm series resistor to the connector pin.
static const int STICK1_PINS[5] = {16, 17, 18, 19, 21};   // K3 pins 14,13,16,15,18
static const int STICK2_PINS[5] = {22, 23, 25, 26, 27};   // K4 pins 14,13,16,15,18

// Optional: the stick's "supply" line from the machine (K3 pin 12 = PC4
// for stick 1, K4 pin 12 = PC0 for stick 2), through a 10 k series
// resistor into an input-only pin.  With FOLLOW_ENABLE set, the lines
// are driven only while the program has raised that bit -- the way a
// real stick only pulls up through it -- and float otherwise, so a
// printer or another gadget on the port is never fought.  Off by
// default: not every +4 game is known to raise it, and a line that is
// never driven is a stick that never works.
#define FOLLOW_ENABLE 0
static const int STICK1_ENABLE_PIN = 34;
static const int STICK2_ENABLE_PIN = 35;

static const int LED_PIN = 2;          // on-board LED: a gamepad is connected
static const int BOOT_BUTTON = 0;      // held at reset: forget every pairing

// ---- gamepad to stick ----------------------------------------------------
// Left-stick travel beyond which it counts as a direction (axes run
// -512..511).
static const int STICK_THRESHOLD = 200;

enum { BIT_DOWN = 0, BIT_UP = 1, BIT_RIGHT = 2, BIT_LEFT = 3, BIT_FIRE = 4 };

static ControllerPtr controllers[BP32_MAX_GAMEPADS];
static uint8_t stickState[2] = {0, 0};   // pressed bits per stick, 1 = pressed

static uint32_t pinMask(const int pins[5], uint8_t pressedBits) {
    uint32_t m = 0;
    for (int b = 0; b < 5; b++)
        if (pressedBits & (1 << b))
            m |= 1u << pins[b];
    return m;
}

static uint32_t allMask(const int pins[5]) {
    return pinMask(pins, 0x1F);
}

// Drive one stick's five lines in a single write each way: pressed low,
// the rest high.  A read by the 8255 between two digitalWrite calls would
// otherwise see left and right together for a microsecond.
static void driveStick(const int pins[5], uint8_t pressedBits) {
    uint32_t low = pinMask(pins, pressedBits);
    uint32_t high = allMask(pins) & ~low;
    GPIO.out_w1ts = high;
    GPIO.out_w1tc = low;
}

static void releaseStick(const int pins[5]) {
    for (int b = 0; b < 5; b++)
        pinMode(pins[b], INPUT);
}

static void claimStick(const int pins[5]) {
    for (int b = 0; b < 5; b++)
        pinMode(pins[b], OUTPUT);
}

static uint8_t stickBitsOf(ControllerPtr ctl) {
    uint8_t bits = 0;
    uint8_t dpad = ctl->dpad();
    if (dpad & DPAD_DOWN)  bits |= 1 << BIT_DOWN;
    if (dpad & DPAD_UP)    bits |= 1 << BIT_UP;
    if (dpad & DPAD_RIGHT) bits |= 1 << BIT_RIGHT;
    if (dpad & DPAD_LEFT)  bits |= 1 << BIT_LEFT;
    if (ctl->axisY() > STICK_THRESHOLD)  bits |= 1 << BIT_DOWN;
    if (ctl->axisY() < -STICK_THRESHOLD) bits |= 1 << BIT_UP;
    if (ctl->axisX() > STICK_THRESHOLD)  bits |= 1 << BIT_RIGHT;
    if (ctl->axisX() < -STICK_THRESHOLD) bits |= 1 << BIT_LEFT;
    if (ctl->a() || ctl->b() || ctl->x() || ctl->y() || ctl->l1() || ctl->r1())
        bits |= 1 << BIT_FIRE;
    // Opposite directions at once are a stick no one can push: drop both.
    if ((bits & (1 << BIT_UP)) && (bits & (1 << BIT_DOWN)))
        bits &= ~((1 << BIT_UP) | (1 << BIT_DOWN));
    if ((bits & (1 << BIT_LEFT)) && (bits & (1 << BIT_RIGHT)))
        bits &= ~((1 << BIT_LEFT) | (1 << BIT_RIGHT));
    return bits;
}

// ---- Bluepad32 callbacks -------------------------------------------------
static void onConnectedController(ControllerPtr ctl) {
    for (int i = 0; i < BP32_MAX_GAMEPADS; i++) {
        if (controllers[i] == nullptr) {
            controllers[i] = ctl;
            ControllerProperties p = ctl->getProperties();
            Serial.printf("gamepad %d connected: %s (VID %04x PID %04x)\r\n",
                          i + 1, ctl->getModelName().c_str(),
                          p.vendor_id, p.product_id);
            if (i < 2)
                ctl->setPlayerLEDs(1 << i);
            return;
        }
    }
    Serial.println("gamepad connected but every slot is taken");
}

static void onDisconnectedController(ControllerPtr ctl) {
    for (int i = 0; i < BP32_MAX_GAMEPADS; i++) {
        if (controllers[i] == ctl) {
            controllers[i] = nullptr;
            if (i < 2) {
                stickState[i] = 0;
                driveStick(i == 0 ? STICK1_PINS : STICK2_PINS, 0);
            }
            Serial.printf("gamepad %d disconnected\r\n", i + 1);
            return;
        }
    }
}

static bool anyConnected() {
    for (int i = 0; i < BP32_MAX_GAMEPADS; i++)
        if (controllers[i] != nullptr && controllers[i]->isConnected())
            return true;
    return false;
}

// ---- setup / loop --------------------------------------------------------
void setup() {
    Serial.begin(115200);
    pinMode(BOOT_BUTTON, INPUT_PULLUP);
    pinMode(LED_PIN, OUTPUT);
    digitalWrite(LED_PIN, LOW);

    // Idle high before anything else: the machine must never see the
    // lines low (every bit "pressed") while we come up.
    claimStick(STICK1_PINS);
    claimStick(STICK2_PINS);
    driveStick(STICK1_PINS, 0);
    driveStick(STICK2_PINS, 0);
#if FOLLOW_ENABLE
    pinMode(STICK1_ENABLE_PIN, INPUT);
    pinMode(STICK2_ENABLE_PIN, INPUT);
#endif

    Serial.printf("PMD 85 joystick adapter, Bluepad32 %s\r\n", BP32.firmwareVersion());
    // BOOT pressed within the first three seconds after reset forgets
    // every pairing.  Not held through the reset itself: GPIO 0 low at
    // reset is the chip's own flashing-mode strap, and the sketch never
    // starts.
    Serial.println("press BOOT in the next 3 s to forget every paired gamepad");
    bool forget = false;
    for (uint32_t t0 = millis(); millis() - t0 < 3000; delay(10)) {
        if (digitalRead(BOOT_BUTTON) == LOW) {
            forget = true;
            break;
        }
    }
    BP32.setup(&onConnectedController, &onDisconnectedController);
    if (forget) {
        Serial.println("BOOT pressed: forgetting every paired gamepad");
        BP32.forgetBluetoothKeys();
    }
    BP32.enableVirtualDevice(false);
    BP32.enableNewBluetoothConnections(true);
}

void loop() {
    bool updated = BP32.update();
    if (updated) {
        for (int i = 0; i < 2; i++) {
            ControllerPtr ctl = controllers[i];
            if (ctl == nullptr || !ctl->isConnected() || !ctl->isGamepad())
                continue;
            if (!ctl->hasData())
                continue;
            uint8_t bits = stickBitsOf(ctl);
            if (bits != stickState[i]) {
                stickState[i] = bits;
                Serial.printf("stick %d: %c%c%c%c%c\r\n", i + 1,
                              bits & (1 << BIT_UP) ? 'U' : '.',
                              bits & (1 << BIT_DOWN) ? 'D' : '.',
                              bits & (1 << BIT_LEFT) ? 'L' : '.',
                              bits & (1 << BIT_RIGHT) ? 'R' : '.',
                              bits & (1 << BIT_FIRE) ? 'F' : '.');
            }
        }
    }

#if FOLLOW_ENABLE
    static bool armed[2] = {false, false};
    bool want[2] = {digitalRead(STICK1_ENABLE_PIN) == HIGH,
                    digitalRead(STICK2_ENABLE_PIN) == HIGH};
    for (int i = 0; i < 2; i++) {
        const int* pins = i == 0 ? STICK1_PINS : STICK2_PINS;
        if (want[i] && !armed[i]) {
            driveStick(pins, stickState[i]);
            claimStick(pins);
        } else if (!want[i] && armed[i]) {
            releaseStick(pins);
        }
        armed[i] = want[i];
    }
    if (armed[0]) driveStick(STICK1_PINS, stickState[0]);
    if (armed[1]) driveStick(STICK2_PINS, stickState[1]);
#else
    driveStick(STICK1_PINS, stickState[0]);
    driveStick(STICK2_PINS, stickState[1]);
#endif

    digitalWrite(LED_PIN, anyConnected() ? HIGH : LOW);
    // Bluepad32's own task does the Bluetooth work; a short yield keeps
    // the loop from starving it and the stick latency around a millisecond.
    delay(1);
}
