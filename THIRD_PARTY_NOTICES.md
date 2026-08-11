# Third-party notices

Fast-FR uses the following model files from the OpenCV Model Zoo:

- YuNet face detector (`face_detection_yunet_2023mar.onnx`)
- SFace face recognizer (`face_recognition_sface_2021dec.onnx`)

The OpenCV Model Zoo publishes both under the Apache License 2.0. The model download script verifies fixed SHA-256 hashes before the files enter a build. See the [OpenCV Zoo repository](https://github.com/opencv/opencv_zoo) for model documentation and license notices.

Python and JavaScript package licenses remain the licenses published by their respective package authors. A production distribution should archive the dependency license inventory generated for the exact lockfiles used to build it.
