"""Entry point for the cv-argus edge process.

Once built, this wires together pipeline/ (camera capture + landmark extraction), model/
(drowsiness inference), orchestrator/ (send-or-not decision), buffer/ (local SQLite queue),
and sender/ (transport to the ESP32) into the loop that runs on the Raspberry Pi 5.

None of those modules exist yet (see README.md's "Planned module layout") — this is a
placeholder that confirms the environment is wired correctly (imports, model/buffer
directories) until the real capture loop replaces the body of main().
"""

import os

import cv2
import mediapipe
import tensorflow as tf


def main() -> None:
    print("cv-argus environment OK")
    print(f"  cv2: {cv2.__version__}")
    print(f"  mediapipe: {mediapipe.__version__}")
    print(f"  tensorflow: {tf.__version__}")
    print(f"  MODEL_DIR: {os.environ.get('MODEL_DIR', '(not set)')}")
    print(f"  BUFFER_DIR: {os.environ.get('BUFFER_DIR', '(not set)')}")
    print(
        "No pipeline wired up yet — see README.md's 'Planned module layout' for what "
        "model/, pipeline/, orchestrator/, buffer/, sender/, and alerts/ will each do."
    )


if __name__ == "__main__":
    main()
