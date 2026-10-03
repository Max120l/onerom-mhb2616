// Bluetooth joystick adapter on an ESP32: one firmware, the machine
// picked at run time from a menu on a small OLED and remembered across
// power cycles.  Bluepad32 brings the gamepads in; a table of profiles
// says how each machine wants its switch lines driven.
//
// Every machine here reads a joystick as a handful of switch lines, and
// the differences are (a) whether an idle line must be driven high or
// merely let go, (b) which connector pins carry which switch, and (c)
// what a second button is.  The profile table captures exactly that:
//
//   PMD 85 (4004/482 club stick): the GPIO 8255's port reads low with
//     nothing connected -- every bit "pressed" -- so the lines are driven
//     high when idle, push-pull.  Bits: down, up, right, left, fire, a
//     pressed switch reading 0 (JOYDEMO on dam+2.ptp).
//   Atari-style DE-9 (C64, VIC-20, Amiga, Atari, Kempston, Master System,
//     MSX): a pressed switch shorts its pin to ground and the machine's
//     own pull-ups hold it high otherwise.  On the C64 and the Amiga the
//     lines are the keyboard matrix, which the machine drives on every
//     scan, so these lines are open-drain: pulled low when pressed and
//     let go otherwise, never driven high.  Pin 6 is fire; a second
//     button is pin 9 (Amiga, Master System) or pin 7 (MSX).
//
// UI: a rotary encoder (or two buttons) scrolls the profile, the OLED
// shows it with the connected pads and the live stick.  The choice is
// saved after a second.  Hold the encoder button (or the third button)
// two seconds, or press BOOT within 3 s of reset, to forget pairings.
//
// Build: the "ESP32 + Bluepad32" board package and the U8g2 library
// (README.md).

#include <Bluepad32.h>
#include <Preferences.h>
#include <U8g2lib.h>
#include <Wire.h>

// ---- hardware ------------------------------------------------------------
// The OLED: 0.96" SSD1306 128x64 on I2C, SDA 21, SCL 22.  For the 1.3"
// SH1106 kind, swap the constructor as noted.
U8G2_SSD1306_128X64_NONAME_F_HW_I2C u8g2(U8G2_R0, U8X8_PIN_NONE, 22, 21);
// U8G2_SH1106_128X64_NONAME_F_HW_I2C u8g2(U8G2_R0, U8X8_PIN_NONE, 22, 21);

// UI_ENCODER 1: rotary encoder A/B on GPIO 4/5, its push button on 15.
// UI_ENCODER 0: three momentary buttons to ground -- next on 4, previous
// on 5, forget-pairings (hold 2 s) on 15.  Internal pull-ups on all.
#ifndef UI_ENCODER
#define UI_ENCODER 1
#endif
static const int UI_A_PIN = 4;
static const int UI_B_PIN = 5;
static const int UI_BUTTON_PIN = 15;
static const int LED_PIN = 2;          // on-board LED: a gamepad is connected
static const int BOOT_BUTTON = 0;      // within 3 s of reset: forget pairings

// Switch lines, named by the DE-9 pin they land on (the PMD 85 cable
// takes the first five to K3, see README.md).  Stick 1 has all seven,
// stick 2 the five every one-button machine needs.  -1: not wired.
enum Line { L_UP, L_DOWN, L_LEFT, L_RIGHT, L_F6, L_F9, L_F7, N_LINES };
static const int STICK1_PINS[N_LINES] = {16, 17, 18, 19, 23, 25, 32};
static const int STICK2_PINS[N_LINES] = {26, 27, 13, 14, 33, -1, -1};
static const char* LINE_NAMES[N_LINES] = {"up", "down", "left", "right",
                                          "fire", "pin9", "pin7"};
static const uint8_t UDLR = (1 << L_UP) | (1 << L_DOWN) | (1 << L_LEFT) | (1 << L_RIGHT);

// ---- profiles ------------------------------------------------------------
struct Profile {
    const char* name;
    bool openDrain;     // false: push-pull, idle high (PMD 85)
    uint8_t lines;      // which lines this machine reads
    int8_t fire2;       // line for a second button, or -1
    bool upOnXY;        // X and Y press "up" (machines whose games jump on up)
};

static const Profile PROFILES[] = {
    {"PMD 85  4004/482",   false, UDLR | (1 << L_F6), -1,    false},
    {"C64 / VIC-20 / C128", true, UDLR | (1 << L_F6), -1,    true},
    {"Amiga  (2 buttons)",  true, UDLR | (1 << L_F6) | (1 << L_F9), L_F9, true},
    {"Atari 2600/8-bit/ST", true, UDLR | (1 << L_F6), -1,    false},
    {"ZX Spectrum Kempston", true, UDLR | (1 << L_F6), -1,   false},
    {"Sega Master System",  true, UDLR | (1 << L_F6) | (1 << L_F9), L_F9, false},
    {"MSX",                 true, UDLR | (1 << L_F6) | (1 << L_F7), L_F7, false},
};
static const int N_PROFILES = sizeof(PROFILES) / sizeof(PROFILES[0]);

static Preferences prefs;
static int profileIndex = 0;
static int pendingProfile = -1;        // chosen on the UI, not yet saved
static uint32_t pendingSince = 0;

// ---- lines ---------------------------------------------------------------
static void lineMasks(const int pins[N_LINES], uint8_t lines,
                      uint32_t& lo, uint32_t& hi) {
    lo = hi = 0;
    for (int l = 0; l < N_LINES; l++) {
        if (!(lines & (1 << l)) || pins[l] < 0)
            continue;
        if (pins[l] < 32)
            lo |= 1u << pins[l];
        else
            hi |= 1u << (pins[l] - 32);
    }
}

// Set a stick's lines: pressed ones low, the rest high (push-pull) or let
// go (open-drain: a 1 in the output register releases the pin).  One
// register write per direction, so the machine never samples a
// half-updated stick.
static void driveStick(const int pins[N_LINES], uint8_t pressed) {
    const Profile& p = PROFILES[profileIndex];
    uint32_t lowLo, lowHi, allLo, allHi;
    lineMasks(pins, pressed & p.lines, lowLo, lowHi);
    lineMasks(pins, p.lines, allLo, allHi);
    GPIO.out_w1ts = allLo & ~lowLo;
    GPIO.out_w1tc = lowLo;
    GPIO.out1_w1ts.val = allHi & ~lowHi;
    GPIO.out1_w1tc.val = lowHi;
}

// Pin modes for the current profile: its lines as outputs of the right
// kind, every other line floating so it cannot touch a pin the machine
// uses for something else (MSX's pin 7 is a button, Atari's is +5 V).
static void applyProfile() {
    const Profile& p = PROFILES[profileIndex];
    for (const int* pins : {STICK1_PINS, STICK2_PINS}) {
        for (int l = 0; l < N_LINES; l++) {
            if (pins[l] < 0)
                continue;
            if (p.lines & (1 << l)) {
                // released before the mode switch: the machine must
                // never see a line "pressed" while we come up
                digitalWrite(pins[l], HIGH);
                pinMode(pins[l], p.openDrain ? OUTPUT_OPEN_DRAIN : OUTPUT);
                digitalWrite(pins[l], HIGH);
            } else {
                pinMode(pins[l], INPUT);
            }
        }
    }
}

// ---- gamepad to stick ----------------------------------------------------
static const int STICK_THRESHOLD = 200;   // of the axes' -512..511

static ControllerPtr controllers[BP32_MAX_GAMEPADS];
static uint8_t stickState[2] = {0, 0};   // pressed lines per stick

static uint8_t stickBitsOf(ControllerPtr ctl) {
    const Profile& p = PROFILES[profileIndex];
    uint8_t bits = 0;
    uint8_t dpad = ctl->dpad();
    if (dpad & DPAD_UP)    bits |= 1 << L_UP;
    if (dpad & DPAD_DOWN)  bits |= 1 << L_DOWN;
    if (dpad & DPAD_LEFT)  bits |= 1 << L_LEFT;
    if (dpad & DPAD_RIGHT) bits |= 1 << L_RIGHT;
    if (ctl->axisY() < -STICK_THRESHOLD) bits |= 1 << L_UP;
    if (ctl->axisY() > STICK_THRESHOLD)  bits |= 1 << L_DOWN;
    if (ctl->axisX() < -STICK_THRESHOLD) bits |= 1 << L_LEFT;
    if (ctl->axisX() > STICK_THRESHOLD)  bits |= 1 << L_RIGHT;
    if (ctl->a() || ctl->b() || ctl->l1() || ctl->r1())
        bits |= 1 << L_F6;
    if (ctl->x() || ctl->y()) {
        if (p.fire2 >= 0)
            bits |= 1 << p.fire2;
        else if (p.upOnXY)
            bits |= 1 << L_UP;
        else
            bits |= 1 << L_F6;
    }
    // Opposite directions at once are a stick no one can push: drop both.
    if ((bits & (1 << L_UP)) && (bits & (1 << L_DOWN)))
        bits &= ~((1 << L_UP) | (1 << L_DOWN));
    if ((bits & (1 << L_LEFT)) && (bits & (1 << L_RIGHT)))
        bits &= ~((1 << L_LEFT) | (1 << L_RIGHT));
    return bits & p.lines;
}

// ---- Bluepad32 callbacks -------------------------------------------------
static volatile bool displayDirty = true;

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
            displayDirty = true;
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
            displayDirty = true;
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

// ---- UI: encoder / buttons -----------------------------------------------
static volatile int encoderDelta = 0;

#if UI_ENCODER
// Quadrature by lookup on both edges; a detent is four steps on the
// usual encoders.
static void IRAM_ATTR onEncoderEdge() {
    static uint8_t last = 0;
    static int8_t steps = 0;
    static const int8_t TABLE[16] = {0, -1, 1, 0, 1, 0, 0, -1,
                                     -1, 0, 0, 1, 0, 1, -1, 0};
    uint8_t now = (digitalRead(UI_A_PIN) << 1) | digitalRead(UI_B_PIN);
    steps += TABLE[(last << 2) | now];
    last = now;
    if (steps >= 4) { encoderDelta++; steps = 0; }
    if (steps <= -4) { encoderDelta--; steps = 0; }
}
#endif

// A button to ground with debounce; returns true once per press.
struct Button {
    int pin;
    bool down;
    uint32_t changed;
    explicit Button(int p) : pin(p), down(false), changed(0) {}
    bool poll() {
        bool now = digitalRead(pin) == LOW;
        if (now != down && millis() - changed > 30) {
            down = now;
            changed = millis();
            return down;
        }
        return false;
    }
    uint32_t heldFor() const { return down ? millis() - changed : 0; }
};
static Button uiButton(UI_BUTTON_PIN);
#if !UI_ENCODER
static Button nextButton(UI_A_PIN);
static Button prevButton(UI_B_PIN);
#endif

static void selectProfile(int idx) {
    idx = ((idx % N_PROFILES) + N_PROFILES) % N_PROFILES;
    if (idx == profileIndex)
        return;
    // Let go of everything under the old rules before the new ones.
    driveStick(STICK1_PINS, 0);
    driveStick(STICK2_PINS, 0);
    profileIndex = idx;
    applyProfile();
    stickState[0] = stickState[1] = 0;
    pendingProfile = idx;
    pendingSince = millis();
    displayDirty = true;
    Serial.printf("profile: %s\r\n", PROFILES[idx].name);
}

// ---- UI: display, on its own task so an I2C frame never delays a stick
static void drawFrame() {
    const Profile& p = PROFILES[profileIndex];
    u8g2.clearBuffer();
    u8g2.setFont(u8g2_font_7x14B_tr);
    u8g2.drawStr(0, 13, p.name);
    u8g2.drawHLine(0, 16, 128);
    u8g2.setFont(u8g2_font_6x12_tr);
    char line[32];
    for (int i = 0; i < 2; i++) {
        ControllerPtr ctl = controllers[i];
        if (ctl != nullptr && ctl->isConnected()) {
            String name = ctl->getModelName();
            snprintf(line, sizeof line, "%d %-14.14s", i + 1, name.c_str());
        } else {
            snprintf(line, sizeof line, "%d no pad", i + 1);
        }
        u8g2.drawStr(0, 30 + i * 13, line);
        // the live stick, as glyphs
        uint8_t s = stickState[i];
        char g[8];
        snprintf(g, sizeof g, "%c%c%c%c%c",
                 s & (1 << L_UP) ? 'U' : '.', s & (1 << L_DOWN) ? 'D' : '.',
                 s & (1 << L_LEFT) ? 'L' : '.', s & (1 << L_RIGHT) ? 'R' : '.',
                 s & (1 << L_F6) ? 'F' : (s & ((1 << L_F9) | (1 << L_F7)) ? '2' : '.'));
        u8g2.drawStr(96, 30 + i * 13, g);
    }
    snprintf(line, sizeof line, "%s%s%s",
             p.openDrain ? "open-drain" : "push-pull",
             p.fire2 >= 0 ? "  2 btn" : "",
             pendingProfile >= 0 ? "  saving" : "");
    u8g2.drawStr(0, 62, line);
    u8g2.sendBuffer();
}

static void displayTask(void*) {
    uint8_t shown[2] = {0xFF, 0xFF};
    for (;;) {
        bool stickMoved = shown[0] != stickState[0] || shown[1] != stickState[1];
        if (displayDirty || stickMoved) {
            displayDirty = false;
            shown[0] = stickState[0];
            shown[1] = stickState[1];
            drawFrame();
        }
        vTaskDelay(pdMS_TO_TICKS(50));
    }
}

// ---- setup / loop --------------------------------------------------------
void setup() {
    Serial.begin(115200);
    pinMode(BOOT_BUTTON, INPUT_PULLUP);
    pinMode(LED_PIN, OUTPUT);
    digitalWrite(LED_PIN, LOW);
    pinMode(UI_A_PIN, INPUT_PULLUP);
    pinMode(UI_B_PIN, INPUT_PULLUP);
    pinMode(UI_BUTTON_PIN, INPUT_PULLUP);

    prefs.begin("btjoy", false);
    profileIndex = prefs.getInt("profile", 0);
    if (profileIndex < 0 || profileIndex >= N_PROFILES)
        profileIndex = 0;
    applyProfile();

    u8g2.begin();
    u8g2.clearBuffer();
    u8g2.setFont(u8g2_font_7x14B_tr);
    u8g2.drawStr(0, 13, "BT joystick");
    u8g2.setFont(u8g2_font_6x12_tr);
    u8g2.drawStr(0, 30, PROFILES[profileIndex].name);
    u8g2.drawStr(0, 50, "BOOT now: forget pads");
    u8g2.sendBuffer();

    Serial.printf("BT joystick adapter, Bluepad32 %s, profile %s\r\n",
                  BP32.firmwareVersion(), PROFILES[profileIndex].name);
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

#if UI_ENCODER
    attachInterrupt(digitalPinToInterrupt(UI_A_PIN), onEncoderEdge, CHANGE);
    attachInterrupt(digitalPinToInterrupt(UI_B_PIN), onEncoderEdge, CHANGE);
#endif
    displayDirty = true;
    xTaskCreatePinnedToCore(displayTask, "display", 4096, nullptr, 1, nullptr, 1);
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
                Serial.printf("stick %d:", i + 1);
                for (int l = 0; l < N_LINES; l++)
                    if (bits & (1 << l))
                        Serial.printf(" %s", LINE_NAMES[l]);
                Serial.print("\r\n");
            }
        }
    }
    driveStick(STICK1_PINS, stickState[0]);
    driveStick(STICK2_PINS, stickState[1]);

    // the UI
#if UI_ENCODER
    int d = encoderDelta;
    if (d != 0) {
        encoderDelta = 0;
        selectProfile(profileIndex + d);
    }
#else
    if (nextButton.poll()) selectProfile(profileIndex + 1);
    if (prevButton.poll()) selectProfile(profileIndex - 1);
#endif
    uiButton.poll();
    static bool forgot = false;
    if (uiButton.heldFor() > 2000 && !forgot) {
        forgot = true;
        Serial.println("button held: forgetting every paired gamepad");
        BP32.forgetBluetoothKeys();
        displayDirty = true;
    }
    if (!uiButton.down)
        forgot = false;
    if (pendingProfile >= 0 && millis() - pendingSince > 1000) {
        prefs.putInt("profile", pendingProfile);
        pendingProfile = -1;
        displayDirty = true;
    }

    digitalWrite(LED_PIN, anyConnected() ? HIGH : LOW);
    // Bluepad32's own task does the Bluetooth work; a short yield keeps
    // the loop from starving it and the stick latency around a millisecond.
    delay(1);
}
