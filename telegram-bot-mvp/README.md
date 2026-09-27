# Telegram fall-alert bot MVP

This directory contains an experimental local Telegram bot for the existing
CG2028 STM32 firmware. It reads the board's newline-delimited UART output at
115200 baud, reuses the assignment's read-only `SampleParser`,
`RecordingDatabase`, and `CSVRecorder`, and sends an alert only for a detector
line that has all three of these properties:

```text
Event=POSSIBLE_FALL State=FALL_LATCHED Alarm=1
```

This is an experimental prototype and not a medically validated emergency
system. It must not be relied upon for emergency response or personal safety.

## Files and local state

- `bot.py` — Telegram polling entry point and command handlers.
- `bot_core.py` — parser, serial monitor, activity recording, bot state, and
  delivery logic.
- `config.py` — `.env` loading and path resolution relative to this directory.
- `recorder_adapter.py` — read-only import bridge to the existing recorder.
- `tests/test_bot.py` — standard-library automated tests with mocked Telegram
  delivery and simulated serial readers.
- `runtime/activity_readings.sqlite3` and `runtime/activity_readings.csv` — the
  existing 22-column activity format, including its `readings` view.
- `runtime/telegram_bot.sqlite3` — separate Telegram subscribers, detector
  events, durable event identities, and delivery results.

All runtime files are ignored by the directory-local `.gitignore`. The bot
does not add Telegram columns or tables to the activity database.

## Create a Telegram bot token

1. In Telegram, open the verified `@BotFather` account.
2. Send `/newbot` and follow the prompts to choose a display name and a unique
   username ending in `bot`.
3. Copy the token from BotFather into `.env` as `TELEGRAM_BOT_TOKEN`.
4. Keep `.env` private; do not paste the token into source code, logs, or a
   repository.

## Install and run with the STM32

From this directory:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Edit `.env` and set the token and serial port. Use an explicit port such as
`COM3` when possible; `AUTO` is allowed when exactly one serial device is
visible. Keep the board firmware at 115200 baud. Then run:

```powershell
python bot.py
```

Use `/start` in Telegram, then `/subscribe` in each chat that should receive
alerts. `/status` reports serial state, configured port, current recording
session, detector state, and subscriber count. `/latest` reads the newest row
from the compatible `readings` view. `/subscribe` and `/unsubscribe` are
idempotent, and `/start` never subscribes automatically.

While this process is running it owns the UART connection. Close CubeIDE serial
viewers, PuTTY, the existing recorder, and other serial monitors first; they
cannot share the same port. A disconnected board does not stop Telegram
commands. The bot closes the serial handle, CSV, activity database, and bot
database on shutdown and marks the activity session ended.

## Simulation mode

Set `SERIAL_PORT=SIMULATED` in `.env`. Standard input is then consumed as
newline-delimited UART text through the exact same sample and detector path as
the real serial connection. A detector-only smoke test is:

```powershell
'DETECTOR TimeMs=12345 State=FALL_LATCHED Alarm=1 Sensors=OK Event=POSSIBLE_FALL Sustained stillness; reset board to clear alarm.' | python bot.py
```

Complete `Sample`/`Accel`/`Gyro` frames can be piped the same way. The bot
continues its Telegram polling loop after input closes, so use Ctrl+C when the
simulation is finished.

## Tests

The suite uses mocked Telegram calls and temporary databases. It covers strict
detector parsing and qualification, durable duplicate suppression, cooldown,
subscription persistence, event and delivery logging, failed subscribers,
`readings` queries, complete sample persistence, simulation processing,
serial retry/disconnection state, and clean shutdown.

```powershell
python -B -m unittest discover -s tests -v
```

## MVP assumptions and limitations

- Telegram long polling requires outbound internet access; no webhook or
  public server is used.
- The bot does not authenticate or encrypt the local databases beyond the
  operating system's file permissions.
- Event identity uses board time plus detector state, alarm, sensor status, and
  event name. This is durable across bot restarts and protects against repeated
  UART lines, while a truly separate event with the same board timestamp would
  be treated as a duplicate.
- The cooldown is measured from laptop receipt timestamps and is durable in the
  event database. It suppresses qualified alerts during the configured window;
  all detector events are still logged.
- Delivery failures are recorded and do not block other subscribers. This MVP
  does not implement a background retry queue for failed Telegram messages.
- It assumes the existing firmware's current sample format and detector field
  names. Malformed or incomplete lines are ignored safely.
- Telegram users can subscribe any chat that can send commands to the bot;
  access control and multi-device deployment are outside this local MVP.

