# CG2028 Assignment — ElderCare Wearable Safety Companion

This firmware extends the supplied `CG2028_Assignment.zip` CubeIDE project. It retains assembly EWMA processing, C reference checks, sensor UART output, and LED behavior, with fall detection and direct Telegram alerts in FreeRTOS tasks.

## Build and flash

Import `CG2028_Assignment` as an existing STM32CubeIDE project. Select **Debug**, then use **Project → Clean** and **Build Project**. Flash `CG2028_Assignment/Debug/CG2028_Assignment.elf`. The project contains its FreeRTOS and mbedTLS source and uses project-relative include paths. It needs no separate build script or generated makefile edits.

## Telegram

Wi-Fi, bot credentials, and the recipient chat ID are defined in `CG2028_Assignment/Core/Inc/telegram_secrets.h` and are included in the ELF. The firmware does not print them over UART. Flash a new ELF after changing any of these settings. `tools/telegram_setup.py find-chat` can identify a chat ID after the recipient starts the bot; pass the bot token in the local `CG2028_BOT_TOKEN` environment variable.

## Sensor data

Each valid UART sample includes filtered acceleration and gyroscope XYZ, vector magnitude, mean squared axis change (MSD), and five-sample magnitude and MSD slopes. The detector uses acceleration MSD, gyroscope MSD, and gyroscope magnitude. Invalid reads are reported separately. Board testing remains necessary for sensor timing, LED behavior, Wi-Fi, TLS, and Telegram delivery.

See [tools/README.md](tools/README.md) for recording tools. `Lab_export.zip` is an optional Wi-Fi reference archive.
