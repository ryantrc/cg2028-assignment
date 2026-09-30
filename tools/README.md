# Recording tools

Build and flash `CG2028_Assignment/Debug/CG2028_Assignment.elf` using STM32CubeIDE's ordinary **Debug** configuration.

`record_activity.py` records UART sensor samples and diagnostics. `detector_verdicts.py` manages trial labels and summaries. The current UART sample contains XYZ, magnitude, MSD, and five-sample magnitude and MSD slopes for each sensor. Older recordings may contain historical columns that are not produced by the current firmware.

Telegram delivery uses the verified numeric `CG2028_CHAT_ID` in `CG2028_Assignment/Core/Inc/telegram_secrets.h`. `telegram_setup.py` can find one with a token supplied through `CG2028_BOT_TOKEN`. The board sends directly over Wi-Fi; Python is not a relay.
