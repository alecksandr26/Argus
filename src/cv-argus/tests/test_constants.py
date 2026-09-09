"""`constants.py` internal-consistency guards.

These constants are wired into model-load-time shapes (`FusedDrowsinessDetector.__init__`),
the deployed threshold, and the Docker build args. A mismatch here would only surface as a
crash (or, worse, a silent accuracy bug) once a real `.keras` file is loaded — cheap to catch
at the constant level instead.
"""

from cv_argus import constants
from cv_argus.model import fused_features


def test_geo_feature_count_matches_the_name_list():
    assert constants.FUSED_MODEL_NUM_GEO_FEATURES == len(fused_features.FUSED_GEO_FEATURE_NAMES)
    assert constants.FUSED_MODEL_NUM_GEO_FEATURES == fused_features.NUM_FUSED_GEO_FEATURES == 10


def test_fused_dim_is_embedding_plus_geo():
    # FusedDrowsinessDetector builds its window buffer as (max_timesteps, embed_dim + num_geo).
    assert constants.FUSED_MODEL_EMBED_DIM == 64
    fused_dim = constants.FUSED_MODEL_EMBED_DIM + constants.FUSED_MODEL_NUM_GEO_FEATURES
    assert fused_dim == 74  # the (100, 74) input shape documented in CLAUDE.md


def test_threshold_is_a_probability():
    assert 0.0 < constants.FUSED_MODEL_THRESHOLD < 1.0


def test_max_timesteps_positive():
    assert constants.FUSED_MODEL_MAX_TIMESTEPS == 100
    assert constants.FUSED_MODEL_MAX_TIMESTEPS > 0


def test_drowsy_index_is_one():
    # Binary softmax [Not Drowsy, Drowsy]; index 1 is the class the threshold is applied to.
    assert constants.FUSED_MODEL_DROWSY_INDEX == 1


def test_cnn_img_size_positive():
    assert constants.CNN_IMG_SIZE == 96
    assert constants.CNN_IMG_SIZE > 0


def test_drive_file_ids_are_non_empty():
    assert constants.CNN_MODEL_DRIVE_FILE_ID
    assert constants.FUSED_MODEL_DRIVE_FILE_ID
    # They must be different artifacts (fused model vs. its embedding backbone).
    assert constants.CNN_MODEL_DRIVE_FILE_ID != constants.FUSED_MODEL_DRIVE_FILE_ID


def test_bundle_urls_are_https_and_named_files():
    assert constants.FACE_LANDMARKER_BUNDLE_URL.startswith("https://")
    assert constants.FACE_DETECTOR_BUNDLE_URL.startswith("https://")
    assert constants.FACE_LANDMARKER_BUNDLE_URL.endswith(constants.FACE_LANDMARKER_BUNDLE_FILENAME)
    assert constants.FACE_DETECTOR_BUNDLE_URL.endswith(constants.FACE_DETECTOR_BUNDLE_FILENAME)


def test_model_filenames_have_expected_extensions():
    assert constants.CNN_MODEL_FILENAME.endswith(".keras")
    assert constants.FUSED_MODEL_FILENAME.endswith(".keras")
    assert constants.FACE_LANDMARKER_BUNDLE_FILENAME.endswith(".task")
    assert constants.FACE_DETECTOR_BUNDLE_FILENAME.endswith(".tflite")
