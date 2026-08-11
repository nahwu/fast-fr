# Bundled recognition models

The Windows build script downloads and verifies the Apache-2.0 licensed OpenCV Zoo models when they are not already present:

- `face_detection_yunet_2023mar.onnx`
- `face_recognition_sface_2021dec.onnx`

Model binaries are excluded from source control to keep the repository small.
