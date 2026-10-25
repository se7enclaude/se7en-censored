# Entry point frozen by PyInstaller (windowed app).
import multiprocessing

from medialab.main import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    raise SystemExit(main())
