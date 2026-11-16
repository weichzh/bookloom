"""Frozen local PP-DocLayoutV3 inference and immediately persisted raw tensors."""
from __future__ import annotations

import importlib.metadata
import os

import cv2
import numpy as np
import PIL


class LayoutInference:
    @staticmethod
    def read_page(image_path):
        from PIL import Image

        with Image.open(image_path) as im:
            image = np.asarray(im.convert("RGB"))
            gray = np.asarray(im.convert("L"))
        h, w = image.shape[:2]
        return image, gray, h, w

    def __init__(self, model_path, use_cuda=False, cuda_dll_dir=None):
        self.dll_handle = None
        if cuda_dll_dir:
            if not use_cuda:
                raise ValueError("cuda_dll_dir requires use_cuda=true")
            os.environ["PATH"] = str(cuda_dll_dir) + os.pathsep + os.environ.get("PATH", "")
            if hasattr(os, "add_dll_directory"):
                self.dll_handle = os.add_dll_directory(str(cuda_dll_dir))
        import onnxruntime as ort
        if use_cuda and cuda_dll_dir and hasattr(ort, "preload_dlls"):
            ort.preload_dlls(directory=str(cuda_dll_dir))
        from rapid_doc.model.layout.rapid_layout_self.main import RapidLayout
        from rapid_doc.model.layout.rapid_layout_self.utils.typings import ModelType, RapidLayoutInput
        from rapid_doc.model.layout.rapid_layout_self.model_handler.pp_doclayout import post_process

        config = RapidLayoutInput(model_type=ModelType.PP_DOCLAYOUTV3,
                                  model_dir_or_path=str(model_path),
                                  engine_cfg={"use_cuda": use_cuda})
        self.layout = RapidLayout(config)
        self.handler = self.layout.model_handler.model_processors
        self.session = self.handler.session
        self.post = post_process
        providers = self.session.session.get_providers()
        wanted = "CUDAExecutionProvider" if use_cuda else "CPUExecutionProvider"
        if not providers or providers[0] != wanted:
            raise RuntimeError(f"requested {wanted}, actual providers: {providers}")
        self.names = self.session.get_output_names()
        self.metadata = {
            "providers": providers,
            "provider_options": self.session.session.get_provider_options(),
            "versions": {"rapid_doc": importlib.metadata.version("rapid-doc"),
                         "onnxruntime": ort.__version__, "numpy": np.__version__,
                         "opencv": cv2.__version__, "Pillow": PIL.__version__},
            "outputs": [{"name": v.name, "shape": v.shape, "runtime_type": v.type}
                        for v in self.session.session.get_outputs()],
        }

    def outputs(self, image, raw_path, reuse_raw=None):
        if reuse_raw is None:
            h, w = image.shape[:2]
            image_input = self.handler.preprocess(image)
            scale = np.array([[800 / h, 800 / w]], dtype=np.float32)
            outputs = self.session(image_input, scale)  # exactly one forward for this page
            partial = raw_path.with_name(raw_path.name + ".partial")
            with partial.open("wb") as handle:
                np.savez_compressed(handle, **dict(zip(self.names, outputs)))
            partial.replace(raw_path)
            return outputs, raw_path
        with np.load(reuse_raw, allow_pickle=False) as archive:
            if set(archive.files) != set(self.names):
                raise ValueError("raw tensor names no longer match model outputs")
            return [archive[name].copy() for name in self.names], reuse_raw
