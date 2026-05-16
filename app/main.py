"""Entry point for the YOLO Stand Counting desktop app."""
import sys
import os

from PySide6.QtWidgets import QApplication


def main():
    if hasattr(sys, "frozen"):
        os.chdir(os.path.dirname(sys.executable))
    app = QApplication(sys.argv)
    app.setApplicationName("YOLO Stand Counting")
    from .main_window import MainWindow
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
