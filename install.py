#!/usr/bin/env python3
import os
import subprocess
import sys
from pathlib import Path


def run(cmd: list[str]) -> None:
    print("+", " ".join(cmd))
    subprocess.check_call(cmd)


def main() -> None:
    root = Path(__file__).resolve().parent
    venv_dir = root / ".venv"

    if not venv_dir.exists():
        print("Creando virtualenv...")
        run([sys.executable, "-m", "venv", str(venv_dir)])

    if os.name == "nt":
        python_bin = venv_dir / "Scripts" / "python.exe"
        pip_bin = venv_dir / "Scripts" / "pip.exe"
    else:
        python_bin = venv_dir / "bin" / "python"
        pip_bin = venv_dir / "bin" / "pip"

    run([str(pip_bin), "install", "--upgrade", "pip"])
    run(
        [
            str(pip_bin),
            "install",
            "-r",
            str(root / "requirements.txt"),
            "-r",
            str(root / "requirements-dev.txt"),
        ]
    )

    print("\n✅ Instalación completada.")
    print("Activa el entorno virtual con:")
    if os.name == "nt":
        print(r"  .\.venv\Scripts\activate")
    else:
        print("  source .venv/bin/activate")


if __name__ == "__main__":
    main()
