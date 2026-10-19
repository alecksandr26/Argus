"""`constants.py` internal-consistency guards.

These constants are wired into the parts detector's input/output shapes, its decision
thresholds, the rule engine, and the Docker build args. A mismatch here would only surface as a
crash (or, worse, a silent accuracy bug) once a real model file is loaded — cheap to catch at the
constant level instead.
"""

from cv_argus import constants


def test_parts_classes_match_the_detector_ids():
    from cv_argus.model import parts_detector as pd_
    assert constants.PARTS_CLASSES == ("open_eye", "closed_eye", "yawn", "head_straight", "head_down")
    assert (pd_.OPEN_EYE, pd_.CLOSED_EYE, pd_.YAWN, pd_.HEAD_STRAIGHT, pd_.HEAD_DOWN) == (0, 1, 2, 3, 4)


def test_parts_crop_geometry_matches_training():
    # 192 stored crop -> 160 model input, square side = max(w, h) * (1 + 2 * 0.25).
    assert constants.PARTS_IMG_SIZE == 160 and constants.PARTS_STORED_CROP_SIZE == 192
    assert constants.PARTS_FACE_MARGIN_FRAC == 0.25


def test_decision_thresholds_are_probabilities():
    for thr in (constants.PARTS_CONF_THRESHOLD, constants.PARTS_CLOSED_EYE_THRESHOLD,
                constants.PARTS_HEAD_DOWN_THRESHOLD):
        assert 0.0 < thr < 1.0
    assert 1 <= constants.PARTS_HEAD_DOWN_VOTES <= constants.PARTS_TRACK_VOTE_FRAMES


def test_rule_ladders_are_increasing():
    assert constants.RULE_EYES_LOW_S < constants.RULE_EYES_MEDIUM_S < constants.RULE_EYES_CRITICAL_S
    assert (constants.RULE_EYES_LOW_YAWN_S < constants.RULE_EYES_MEDIUM_YAWN_S
            < constants.RULE_EYES_CRITICAL_YAWN_S <= constants.RULE_EYES_CRITICAL_S)
    assert constants.RULE_HEAD_DOWN_YAWN_S <= constants.RULE_HEAD_DOWN_S


def test_p_drowsy_crosses_the_esp32_threshold_only_at_medium():
    # The ESP32 treats p_drowsy >= 0.57 as drowsy; only medium/critical may reach it.
    p = constants.RULE_P_DROWSY
    assert p["none"] < p["low"] < 0.57 <= p["medium"] < p["critical"] <= 1.0


def test_drive_file_ids_are_non_empty_and_distinct():
    assert constants.PARTS_INT8_DRIVE_FILE_ID and constants.PARTS_FLOAT_DRIVE_FILE_ID
    assert constants.PARTS_INT8_DRIVE_FILE_ID != constants.PARTS_FLOAT_DRIVE_FILE_ID


def test_bundle_url_is_https_and_named_file():
    assert constants.FACE_DETECTOR_BUNDLE_URL.startswith("https://")
    assert constants.FACE_DETECTOR_BUNDLE_URL.endswith(constants.FACE_DETECTOR_BUNDLE_FILENAME)


def test_model_filenames_have_expected_extensions():
    assert constants.PARTS_INT8_FILENAME.endswith(".tflite")
    assert constants.PARTS_FLOAT_FILENAME.endswith(".keras")
    assert constants.FACE_DETECTOR_BUNDLE_FILENAME.endswith(".tflite")
