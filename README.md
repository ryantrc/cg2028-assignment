# CG2028 sensor and fall-detection experiments

The current application is **Prototype 3** in
[`CG2028_Assignment`](CG2028_Assignment/Core/Src/main.c). It filters the
accelerometer and gyroscope readings, checks a sudden change against the
preceding activity, then observes whether movement continues or becomes quiet.
**A low baseline ratio no longer ends the observation early.** Later quiet
blocks can still produce a possible fall; continued movement after a low-ratio
event gives the neutral `MOVEMENT_CONTINUED` result. This can also restore false
fall alarms when running is followed by stopping; see the
[Prototype 3 behavior and limitations](docs/prototype-3.md).
These are experimental rules for hand-moved board trials, not validated human
fall detection.

**Build the current Telegram firmware with `tools/build_telegram.py`, then
flash its `BuildTelegram` ELF.** Follow the
[Mac setup steps below](#local-setup-and-build-on-mac). Updating the Python
recorder alone does not install firmware on the STM32.
Start the firmware, reset the board or hold the PC13 user button for two seconds
after an alarm, and wait for `READY`
(`State=NORMAL`, `Alarm=0`) before the intended event. Startup needs roughly
five seconds of valid readings: two seconds for filter settling, then three
seconds of baseline activity.

Sampling remains **100 ms**, UART remains **115200 baud**, and each axis uses
the existing **25% EWMA** filter. Recording commands and destinations are
unchanged:

```bash
python3 tools/record_activity.py --test --name trial-1 --verdict fall
python3 tools/record_activity.py --free --name everyday-activity
python3 tools/record_activity.py --calibration --name walking-slowly \
  --notes "Consistent hand movement"
```

Choose one command at a time. Tests record for 30 seconds by default, including
after an early fall decision; calibration records for 30 seconds. Free mode
runs until the board reports a fall or you stop it. `--verdict` is your expected
test outcome and never changes the detector's answer. Existing recordings are
retained.

- [Prototype 3: continued observation after a low ratio](docs/prototype-3.md)
- [Prototype 2: historical ratio-veto behavior](docs/prototype-2.md)
- [Prototype 2: historical replay results](data/prototype2_analysis_2026-09-25/README.md)
- [Recording commands, names and duration](tools/README.md)
- [Saved files, fields and verdict meanings](data/README.md)
- [Prototype 1: historical detector behavior](docs/prototype-1.md)

After a fall, 30 consecutive complete quiet seconds escalate to `LONG_LIE`.
This is an experimental demo setting, not a validated measure of a person's
condition. The fall LED toggles every 50 ms; Long Lie gives two short flashes
per second. Use `--test --duration 90` to leave time to record both events because
free mode stops at the first fall. A sensor fault needs hardware attention;
holding the button does not repair it.
## Direct Telegram alerts

The onboard ISM43362 uses SPI3. A FreeRTOS network worker sends queued detector
events while the sensor task keeps its 100 ms schedule. It sends one “Possible
fall detected” message per fall episode and one “Continued stillness after
possible fall” escalation after 30 quiet seconds. Messages contain device ID
and board uptime. Manual reset starts a new episode without claiming safety.
The detector thresholds, six-axis assembly EWMA, LEDs, UART `DETECTOR` parser
and 22-column measurement format are preserved.

## Local setup and build on Mac

The STM32 sends Telegram alerts directly over Wi-Fi. Python saves readings on
your Mac and displays diagnostics; it is not a Telegram relay. The powered,
running board can send alerts without the recorder. It needs the configured
Wi-Fi network and internet access.

### 1. Create your bot and start its chat

Open [@BotFather](https://t.me/BotFather) in Telegram, send `/newbot`, choose a
display name and an available username ending in `bot`, and keep the issued
token locally. Then open **your new bot** and tap **Start** or send `/start`.
[Telegram's creation instructions](https://core.telegram.org/bots/features#creating-a-new-bot)

For a teammate's existing bot, use their bot username and agreed shared token
instead. Each private recipient must message the bot once before it can send
alerts; `/start` is not needed again after board resets.
[Telegram's conversation rules](https://core.telegram.org/bots#how-are-bots-different-from-humans)

From Terminal in this repository, enter the token at a hidden **zsh** prompt:

```zsh
read -r -s 'CG2028_BOT_TOKEN?Paste bot token: '
printf '\n'
export CG2028_BOT_TOKEN
python3 tools/telegram_setup.py validate
python3 tools/telegram_setup.py find-chat
```

`validate` prints the bot username. `find-chat` prints recipient chat IDs from
incoming messages. Use your receiving chat ID, not the bot's own ID. If several
IDs appear, verify which is yours; the helper does not label them. If none
appears, send a fresh message to the bot and retry. For a shared bot, another
service may already have consumed it; an existing webhook also prevents
`getUpdates`. Coordinate with the teammate rather than removing their webhook.
[Telegram API: getMe](https://core.telegram.org/bots/api#getme),
[getUpdates](https://core.telegram.org/bots/api#getupdates)

An optional computer-side test sends one labelled setup message:

```zsh
read -r 'CG2028_CHAT_ID?Recipient chat ID: '
export CG2028_CHAT_ID
python3 tools/telegram_setup.py send-test
unset CG2028_BOT_TOKEN CG2028_CHAT_ID
```

This tests your computer's access to the chat, not STM32 delivery. Keep tokens
out of chat, command arguments and committed files. The firmware targets one
chat ID; for several recipients, use a group containing the bot and configure
that group's verified chat ID.

### 2. Enter the local board settings

Copy the example once; `-n` preserves an existing settings file:

```bash
cp -n CG2028_Assignment/Core/Inc/telegram_secrets.example.h CG2028_Assignment/Core/Inc/telegram_secrets.h
```

Open the new `telegram_secrets.h` in your editor. Replace the placeholders for
`CG2028_WIFI_SSID`, `CG2028_WIFI_PASSWORD`, `CG2028_BOT_TOKEN`,
`CG2028_CHAT_ID` and `CG2028_DEVICE_ID`, keeping values inside the C string
quotes. Use **2.4 GHz Wi-Fi or a hotspot with WPA2 personal security** and
internet access. This firmware connects using WPA2; the Mac's Wi-Fi settings
are not automatically copied to the board.

The local settings file and `BuildTelegram` output are gitignored. Leave
`telegram_secrets.example.h` as a template. Rebuild and reflash whenever the
local settings change.

### 3. Build and flash

With STM32CubeIDE installed in `/Applications`, run:

```bash
python3 tools/build_telegram.py
```

The helper finds the bundled GNU compiler and produces
`CG2028_Assignment/BuildTelegram/CG2028_Assignment.elf`. This builds the program;
it does not program the board. Use the helper for this firmware: the normal
CubeIDE managed `Debug` rebuild does not provide this build setup. Keep build
products containing credentials in the ignored `BuildTelegram` directory.

Connect the board through its ST-LINK USB connector, then configure CubeIDE to
load that ELF without rebuilding it:

1. Open **Run → Debug Configurations → STM32 C/C++ Application**. Duplicate
   the existing `CG2028_Assignment` launch and name the copy `Telegram`.
2. On **Main**, keep **Project** as `CG2028_Assignment` and **Build
   Configuration** as `Debug`. Turn off **Select configuration using
   'C/C++ Application'** if selected. Set **C/C++ Application:** to
   `BuildTelegram/CG2028_Assignment.elf`.
3. Under **Build (if required) before launching**, select **Disable auto
   build**. `BuildTelegram` is an output folder, not a CubeIDE build
   configuration.
4. On **Startup**, check the load table uses the private `BuildTelegram` ELF.
   Select its row and **Edit...** if needed: **Program path:** must point to
   that ELF, **Perform build** must be unchecked, and **Download** and
   **Load symbols** checked. Remove an old `Debug` ELF row from this duplicated
   launch if it would also be downloaded.
5. Click **Apply → Debug**. When execution stops at `main()`, click **Resume**
   or press **F8**. Downloading the file alone does not run past that breakpoint.

Run `python3 tools/build_telegram.py` again after code/settings changes, then
use this launch to load the new ELF. Avoid the ordinary **Build Project** action
for this Telegram build. The detector and network tasks run on the board;
Python recording is a separate step.

### 4. Record and check delivery

Once the firmware is running, close other serial viewers and start a longer
test to observe both the fall and the later Long Lie event:

```bash
python3 tools/record_activity.py --test --name telegram-demo --verdict fall \
  --duration 90 --notes "Fall followed by continued stillness"
```

Wait for `READY` / `State=NORMAL Alarm=0` before the intended movement. The
recorder shows `ALERT` and `WIFI` diagnostics alongside detector events.
`ALERT Network=AP_CONNECTED Code=0` means Wi-Fi connected;
`ALERT Network=DELIVERED Code=0` means Telegram accepted an alert.
`PENDING` or `RETRY_PENDING` does not mean delivery succeeded. Confirm the
message arrived in your Telegram chat.

The first message follows a detected fall. Keep the board quiet afterward:
**30 consecutive complete quiet seconds after the fall** trigger Long Lie and
the second message. This is separate from the recorder's duration.
`--test --duration 90` continues recording after a fall; `--free` stops Python
at the first fall, while the board keeps running and can send its escalation.
No measurements are saved on the Mac while the recorder is closed.

Hold the **PC13 user button for two seconds** after an alarm, then wait for
`READY`, to begin another episode; a board reset also restarts it. The button
does not repair a sensor fault. See the [recorder guide](tools/README.md) for
saved files, modes and serial-port selection.

### Network implementation

The SPI3 driver follows ST's B-L4S5I-IOT01A example. Mbed TLS 3.6.7 runs on
the STM32 over the module's plain TCP socket and enforces TLS 1.2, GoDaddy Root
Certificate Authority G2 chain validation and the exact `api.telegram.org`
hostname. The root is in `Core/Inc/telegram_ca.pem` and must be updated when
Telegram's chain changes. Certificate dates use NTP time; NTP is
unauthenticated and susceptible to spoofing. No HTTP or unverified TLS fallback
exists. Every network operation has a finite timeout; events use bounded
retries and backoff. Delivery requires HTTP 200 and valid Telegram JSON with
`ok:true`.

## Earlier Telegram verification on the connected board

These checks were performed on the earlier Telegram build. They establish the
reported network behavior for those trials, not hardware validation of the
new Prototype 3 decision rule.

- ST-Link: `STM32L4S5I-DK`, MCU revision V. Wi-Fi module:
  `ISM43362-M3G-L44-SPI`, firmware `C3.5.2.7.STM`, API `v3.5.2`.
- The module's `PE` query reported certificate slots 0, 1 and 2 available.
  No module certificate was programmed. The command manual for this firmware
  does not specify a TLS protocol version or hostname validation guarantee;
  those module capabilities remain unverified. The STM32 instead enforces TLS
  1.2 and validates the chain and hostname itself.
- The board joined the 2.4 GHz hotspot, resolved `api.telegram.org`, obtained
  time and verified TLS on port 443. A labeled board test POST received HTTP
  200 and Telegram `ok:true`; UART reported `BOARD_TEST_DELIVERED`.
- After a controlled module disconnect, the board rejoined and repeated DNS,
  NTP and verified TLS. A transient NTP failure retried successfully. In the
  65-second trial, 656 samples had a maximum device interval of 100 ms and
  host receipt gap of 110 ms, below the 250 ms sensor fault threshold.
- Host `tools/test_telegram_alerts.c` covers deduplication, reset and new fall,
  retry backoff, rejection, response parsing and form encoding. Host tests
  are reported separately from board results.

A hand-held board gesture triggered the fall event; the board reported
`DELIVERED`. At uptime 223606 ms it queued `LONG_LIE`, following 30 quiet
seconds, and reported a second `DELIVERED` after verified TLS. The recipient
confirmed receiving both Telegram messages. Across the 120-second event
monitoring window, 1209 samples had a maximum device interval of 100 ms.
These are board results, separate from the host unit tests.
