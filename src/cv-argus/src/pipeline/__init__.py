"""Camera capture + MediaPipe FaceLandmarker pipeline for the cv-argus edge module.

Not built yet beyond `downloader.py` (fetching the FaceLandmarker `.task` bundle) — see the
root README's "Planned module layout" for what the camera-loop/FaceLandmarker-wrapper piece
still needs to do: replicate `notebook/03_deployment_export.ipynb`'s "End-to-End Live Stream
Simulation" pattern (`VIDEO` running mode, `detect_for_video(mp_image, timestamp_ms)`), and translate each
`FaceLandmarkerResult` into the plain `landmarks_xy`/`rotation_matrix`/`blendshape_scores`
arguments `cv_argus.model.DrowsinessDetector.predict_frame()` expects.
"""

from .downloader import download_face_landmarker_bundle

__all__ = ["download_face_landmarker_bundle"]
