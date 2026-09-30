"""Build credential-bearing STM32 firmware only in the ignored build folder.

Requires STM32CubeIDE's GNU ARM compiler and make on PATH, or an installation
under /Applications on macOS or C:/ST on Windows.
Run: python3 tools/build_telegram.py
"""
from __future__ import annotations

import os
from pathlib import Path
import re
import shutil
import subprocess


ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT / "CG2028_Assignment"
SOURCE_BUILD = PROJECT / "Debug"
PRIVATE_BUILD = PROJECT / "BuildTelegram"
TEMPLATES = Path(__file__).with_name("telegram_build")


def tool(name: str, pattern: str) -> Path:
    found = shutil.which(name)
    if found:
        return Path(found)
    candidates = list(Path("C:/ST").glob(pattern))
    mac_pattern = (
        "STM32CubeIDE*.app/Contents/Eclipse/plugins/"
        + pattern.split("plugins/", 1)[1].removesuffix(".exe")
    )
    for directory in (Path("/Applications"), Path.home() / "Applications"):
        candidates.extend(directory.glob(mac_pattern))
    candidates = sorted(path for path in candidates if path.is_file())
    if not candidates:
        raise SystemExit(f"Missing {name}; install STM32CubeIDE or put it on PATH")
    return candidates[-1]


def omit_generated(_directory: str, names: list[str]) -> set[str]:
    suffixes = (".elf", ".o", ".d", ".su", ".cyclo", ".map", ".list", ".bin", ".hex")
    # objects.list is a make dependency, not the generated disassembly .list.
    return {name for name in names if name != "objects.list" and name.endswith(suffixes)}


def main() -> None:
    if not (PROJECT / "Core/Inc/telegram_secrets.h").is_file():
        raise SystemExit("Create the ignored Core/Inc/telegram_secrets.h first")
    compiler = tool(
        "arm-none-eabi-gcc",
        "STM32CubeIDE_*/STM32CubeIDE/plugins/"
        "com.st.stm32cube.ide.mcu.externaltools.gnu-tools-for-stm32.*/tools/bin/arm-none-eabi-gcc.exe",
    )
    make = tool(
        "make",
        "STM32CubeIDE_*/STM32CubeIDE/plugins/"
        "com.st.stm32cube.ide.mcu.externaltools.make.*/tools/bin/make.exe",
    )
    shutil.copytree(SOURCE_BUILD, PRIVATE_BUILD, dirs_exist_ok=True, ignore=omit_generated)
    for package in ("FreeRTOS", "mbedtls"):
        destination = PRIVATE_BUILD / "ThirdParty" / package / "subdir.mk"
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(TEMPLATES / f"{package}.subdir.mk", destination)
    (PRIVATE_BUILD / "ThirdParty/FreeRTOS/portable/GCC/ARM_CM4F").mkdir(parents=True, exist_ok=True)
    (PRIVATE_BUILD / "ThirdParty/FreeRTOS/portable/MemMang").mkdir(parents=True, exist_ok=True)
    (PRIVATE_BUILD / "ThirdParty/mbedtls/library").mkdir(parents=True, exist_ok=True)

    makefile = PRIVATE_BUILD / "makefile"
    contents = makefile.read_text(encoding="utf-8")
    linker = (PROJECT / "STM32L4S5VITX_FLASH.ld").as_posix()
    contents = re.sub(r"[A-Za-z]:[^\s\"]*STM32L4S5VITX_FLASH\.ld", linker, contents)
    contents = contents.replace('@objects.list', '$(OBJS)')
    makefile.write_text(contents, encoding="utf-8")

    environment = os.environ.copy()
    environment["PATH"] = os.pathsep.join((str(compiler.parent), str(make.parent), environment.get("PATH", "")))
    subprocess.run([str(make), "-j8", "ALLOW_PRIVATE_BUILD=1", "all"],
                   cwd=PRIVATE_BUILD, env=environment, check=True)
    print("Firmware built in the ignored BuildTelegram directory")


if __name__ == "__main__":
    main()
