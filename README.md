# CG2028 sensor and fall-detection experiments

The current application is **Prototype 2** in
[`CG2028_Assignment`](CG2028_Assignment/Core/Src/main.c). It filters the
accelerometer and gyroscope readings, checks a sudden change against the
preceding activity, then observes whether movement continues or becomes quiet.
These are experimental rules for hand-moved board trials, not validated human
fall detection.

**Rebuild and flash `CG2028_Assignment` in CubeIDE to run Prototype 2.** Updating
the Python recorder alone does not install the new detector on the STM32.
Resume execution, reset the board or hold the PC13 user button for two seconds
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

- [Prototype 2: baseline comparison and decision timing](docs/prototype-2.md)
- [Prototype 2 replay results and limitations](data/prototype2_analysis_2026-09-25/README.md)
- [Recording commands, names and duration](tools/README.md)
- [Saved files, fields and verdict meanings](data/README.md)
- [Prototype 1: historical detector behavior](docs/prototype-1.md)

After a fall, 30 consecutive complete quiet seconds escalate to `LONG_LIE`.
This is an experimental demo setting, not a validated measure of a person's
condition. The fall LED toggles every 50 ms; Long Lie gives two short flashes
per second. Use `--test --duration 60` or longer to record both events because
free mode stops at the first fall. A sensor fault needs hardware attention;
holding the button does not repair it.
# Direct Telegram alerts (28 September 2026)

The onboard ISM43362 uses SPI3. A FreeRTOS network worker sends queued detector
events while the sensor task keeps its 100 ms schedule. It sends one “Possible
fall detected” message per fall episode and one “Continued stillness after
possible fall” escalation after 30 quiet seconds. Messages contain device ID
and board uptime. Manual reset starts a new episode without claiming safety.
The detector thresholds, six-axis assembly EWMA, LEDs, UART `DETECTOR` parser
and 22-column measurement format are preserved.

## Local setup and build

Copy `CG2028_Assignment/Core/Inc/telegram_secrets.example.h` to the gitignored
`telegram_secrets.h` beside it. Fill in Wi-Fi, bot token, chat ID and device ID.
Start the bot and send it a message. With `CG2028_BOT_TOKEN` in your local
environment, run `python tools/telegram_setup.py find-chat` to find the ID.
`validate` checks the token; `send-test` sends a computer-originated test, which
does not prove board delivery. The helper never prints the token.
Each private recipient needs to message the bot once, but does not need to
repeat `/start` after board restarts. The current firmware sends to one chat
ID. To alert several people with this build, add the bot to a Telegram group,
find that group's chat ID, and configure it as `CG2028_CHAT_ID`.

Run `python tools/build_telegram.py` with the STM32CubeIDE GNU toolchain
installed, then flash the ELF in the ignored
`CG2028_Assignment/BuildTelegram` directory. This repository tracks some
`Debug` output: its makefile refuses credential-bearing builds, and generated
`Debug` objects are ignored. Do not stage its binaries. A token compiled into firmware can be extracted by someone
with physical access. Rotate a leaked token with BotFather.

The SPI3 driver follows ST's B-L4S5I-IOT01A example. Mbed TLS 3.6.7 runs on
the STM32 over the module's plain TCP socket and enforces TLS 1.2, GoDaddy Root
Certificate Authority G2 chain validation and the exact `api.telegram.org`
hostname. The root is in `Core/Inc/telegram_ca.pem` and must be updated when
Telegram's chain changes. Certificate dates use NTP time; NTP is
unauthenticated and susceptible to spoofing. No HTTP or unverified TLS fallback
exists. Every network operation has a finite timeout; events use bounded
retries and backoff. Delivery requires HTTP 200 and valid Telegram JSON with
`ok:true`.

## Verification on the connected board

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
