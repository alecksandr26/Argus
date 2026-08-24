"""Camera capture + MediaPipe + model-inference pipeline for the cv-argus edge module.

Everything the rest of the codebase needs from this subpackage is re-exported here:

- `Stage`/`SourceStage`/`OutputStage`/`Pipeline`/`FrameContext` (`stage.py`) — the threaded,
  queue-connected pipeline abstraction every concrete stage below is built from. See
  `stage.py`'s module docstring for the design.
- `VideoCaptureSource`/`PiCameraSource` (`sources.py`) — where frames come from: a camera, a
  video file (both via `cv2.VideoCapture`, same `CAMERA_SOURCE` convention as before), or the
  Pi's CSI camera.
- `FaceDetectorCropStage` (`face_detector_stage.py`) / `FaceLandmarkerStage`
  (`face_landmarker_stage.py`) — the two MediaPipe stages, one per model family: BlazeFace face
  cropping for the (deployed) CNN path, FaceLandmarker for the (kept, optional) LSTM path.
- `CnnInferenceStage`/`LstmInferenceStage` (`inference_stages.py`) — wrap the `model/`
  detectors, turning a `FrameContext`'s MediaPipe output into a `DetectionResult`.
- `LoggingOutputStage` (`output_stages.py`) — the placeholder sink until `orchestrator/` exists.
- `MjpegStreamOutputStage` (`mjpeg_output_stage.py`) — a demo-only sink that draws a live
  overlay (drowsiness class, face box, fps) and serves it as a browser-viewable MJPEG stream.
  Opt-in via `OUTPUTS` (see `src/main.py`) — see that module's docstring for the security note
  on why this isn't on by default.
- `download_face_landmarker_bundle`/`download_face_detector_bundle` (`downloader.py`) — fetch
  the two MediaPipe model bundles above, called by the Dockerfile at build time.

`src/main.py` builds a `Pipeline` out of these (see its module docstring) — that's the one
place all of this actually gets wired together and run.

**Lazy submodule imports, deliberately** — everything above is importable as
`cv_argus.pipeline.<Name>`, but `sources.py`/`face_detector_stage.py`/`face_landmarker_stage.py`/
`inference_stages.py`/`mjpeg_output_stage.py` (which need `cv2`/`mediapipe`/`tensorflow`, the
last transitively via `cv_argus.model`) are only imported on first attribute access via
`__getattr__` (PEP 562), not eagerly below. `stage.py` (needs only `numpy`), `downloader.py`,
and `output_stages.py` (need neither) stay eager. This keeps
`Stage`/`SourceStage`/`OutputStage`/`Pipeline`/`FrameContext` importable and testable (see
`scripts/smoke_test_pipeline.py`) without installing the full stack this Docker-first project
otherwise assumes is always present (see the root README's "Why a Docker-first workflow") —
only actually touching a stage class that needs one of those libraries pulls it in.
"""

import importlib

from .downloader import download_face_detector_bundle, download_face_landmarker_bundle
from .output_stages import LoggingOutputStage
from .stage import FrameContext, OutputStage, Pipeline, SourceStage, Stage

__all__ = [
    "Stage",
    "SourceStage",
    "OutputStage",
    "Pipeline",
    "FrameContext",
    "VideoCaptureSource",
    "PiCameraSource",
    "FaceDetectorCropStage",
    "FaceLandmarkerStage",
    "CnnInferenceStage",
    "LstmInferenceStage",
    "LoggingOutputStage",
    "MjpegStreamOutputStage",
    "download_face_landmarker_bundle",
    "download_face_detector_bundle",
]

# name -> (submodule, attribute), for the lazily-resolved exports above (see module docstring).
_LAZY = {
    "VideoCaptureSource": (".sources", "VideoCaptureSource"),
    "PiCameraSource": (".sources", "PiCameraSource"),
    "FaceDetectorCropStage": (".face_detector_stage", "FaceDetectorCropStage"),
    "FaceLandmarkerStage": (".face_landmarker_stage", "FaceLandmarkerStage"),
    "CnnInferenceStage": (".inference_stages", "CnnInferenceStage"),
    "LstmInferenceStage": (".inference_stages", "LstmInferenceStage"),
    "MjpegStreamOutputStage": (".mjpeg_output_stage", "MjpegStreamOutputStage"),
}


def __getattr__(name: str):
    try:
        module_name, attr_name = _LAZY[name]
    except KeyError:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from None
    module = importlib.import_module(module_name, __name__)
    value = getattr(module, attr_name)
    globals()[name] = value  # cache -- subsequent access skips __getattr__ entirely
    return value
