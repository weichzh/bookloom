"""PP-DocLayoutV3 adapter based on the frozen single-forward experiment."""
"""Frozen pixel-space comparison routes for the mask-frontier audit."""
import math

import cv2
import numpy as np


def outward_bbox(box, width, height, pad_px=0.0):
    """Return a clamped, half-open integer enclosure of a floating box."""
    x0, y0, x1, y1 = map(float, box)
    return [max(0, min(width, math.floor(x0 - pad_px))),
            max(0, min(height, math.floor(y0 - pad_px))),
            max(0, min(width, math.ceil(x1 + pad_px))),
            max(0, min(height, math.ceil(y1 + pad_px)))]


def tight_bbox(binary):
    """The union envelope of every positive pixel; None means empty."""
    yy, xx = np.nonzero(binary)
    if not len(xx):
        return None
    return [int(xx.min()), int(yy.min()), int(xx.max()) + 1, int(yy.max()) + 1]


def page_components(ink):
    """8-connected components, including one-pixel components, on the whole page."""
    _, labels, stats, _ = cv2.connectedComponentsWithStats(
        np.ascontiguousarray(ink, dtype=np.uint8), connectivity=8, ltype=cv2.CV_32S)
    return labels, stats


def _components_bbox(stats, ids):
    if not len(ids):
        return None
    s = stats[ids]
    return [int(s[:, cv2.CC_STAT_LEFT].min()),
            int(s[:, cv2.CC_STAT_TOP].min()),
            int((s[:, cv2.CC_STAT_LEFT] + s[:, cv2.CC_STAT_WIDTH]).max()),
            int((s[:, cv2.CC_STAT_TOP] + s[:, cv2.CC_STAT_HEIGHT]).max())]


def r3(ink, box, dpi):
    """All ink in a 3-pt-expanded B box; tight half-open bbox, or None."""
    h, w = ink.shape
    x0, y0, x1, y1 = outward_bbox(box, w, h, 3 * dpi / 72)
    result = tight_bbox(ink[y0:y1, x0:x1])
    if result is None:
        return None
    return [result[0] + x0, result[1] + y0, result[2] + x0, result[3] + y0]


def bcc(labels, stats, box):
    """Select only components with ink in the outward B rectangle."""
    h, w = labels.shape
    x0, y0, x1, y1 = outward_bbox(box, w, h)
    ids = np.unique(labels[y0:y1, x0:x1])
    ids = ids[ids != 0]
    return _components_bbox(stats, ids), ids



def track_original_indices(raw_boxes, raw_masks, pp, img_size, captured, post):
    """Trace exact row filtering, then prove correspondence at library's polygon seam."""
    if not isinstance(pp.conf_thres, float):
        return None, 'unexpected threshold type'
    if raw_boxes.ndim != 2 or raw_boxes.shape[1] not in (6, 7, 8):
        return None, 'unexpected raw box dimensions'
    if raw_masks is None or len(raw_boxes) != len(raw_masks):
        return None, 'raw box and mask counts differ'
    idx = np.flatnonzero((raw_boxes[:, 1] > pp.conf_thres) & (raw_boxes[:, 0] > -1))
    boxes = raw_boxes[idx]
    if pp.layout_nms:
        selected = post.nms(boxes[:, :6], iou_same=0.6, iou_diff=0.98)
        idx, boxes = idx[selected], boxes[selected]
    if len(boxes) > 1 and boxes.shape[1] in (6, 7, 8):
        area_thres = 0.82 if img_size[0] > img_size[1] else 0.93
        image_index = pp.labels.index('image') if 'image' in pp.labels else None
        keep = []
        for box in boxes:
            cls, score, xmin, ymin, xmax, ymax = box[:6]
            if cls == image_index:
                xmin, ymin = max(0, xmin), max(0, ymin)
                xmax, ymax = min(img_size[0], xmax), min(img_size[1], ymax)
                if (xmax - xmin) * (ymax - ymin) <= area_thres * img_size[0] * img_size[1]:
                    keep.append(True)
                else:
                    keep.append(False)
            else:
                keep.append(True)
        # Installed code retains everything if all boxes would have been removed.
        if any(keep):
            keep = np.asarray(keep, dtype=bool)
            idx, boxes = idx[keep], boxes[keep]
    mode = pp.layout_merge_bboxes_mode
    if mode:
        if not isinstance(mode, dict):
            return None, 'unexpected library containment mode'
        formula_index = pp.labels.index('formula') if 'formula' in pp.labels else None
        keep = np.ones(len(boxes), dtype=bool)
        for category_index, layout_mode in mode.items():
            if layout_mode == 'large':
                _, contained = post.check_containment(boxes[:, :6], formula_index, category_index, mode=layout_mode)
                keep &= contained == 0
            elif layout_mode == 'small':
                contains, contained = post.check_containment(boxes[:, :6], formula_index, category_index, mode=layout_mode)
                keep &= (contains == 0) | (contained == 1)
            elif layout_mode != 'union':
                return None, 'unexpected library per-category mode'
        idx, boxes = idx[keep], boxes[keep]
    if boxes.shape[1] == 8:
        selected = np.lexsort((-boxes[:, 7], boxes[:, 6]))
        idx, boxes = idx[selected], boxes[selected, :6]
    elif boxes.shape[1] == 7:
        selected = np.argsort(boxes[:, 6])
        idx, boxes = idx[selected], boxes[selected, :6]
    if 'boxes' not in captured:
        return (idx, None) if len(idx) == 0 else (None, 'polygon seam was not called')
    if not np.array_equal(boxes, captured['boxes']):
        return None, 'raw-row trace does not equal installed polygon-seam boxes'
    if not np.array_equal(raw_masks[idx], captured['masks']):
        return None, 'raw-row trace does not equal installed polygon-seam masks'
    return idx, None


class LayoutModel:
    """Own the configured local model; never resolve or download another model."""

    def __init__(self, model_path, use_cuda=False, cuda_dll_dir=None):
        try:
            from .source_figures_inference import LayoutInference
        except ImportError:
            from source_figures_inference import LayoutInference
        self.inference = LayoutInference(model_path, use_cuda, cuda_dll_dir)
        self.handler = self.inference.handler
        self.pp, self.session, self.post = self.handler.pp_postprocess, self.inference.session, self.inference.post
        if self.pp.conf_thres != 0.3 or self.handler.layout_shape_mode != "auto":
            raise RuntimeError("unsupported PP-DocLayoutV3 postprocessor defaults")
        self.names, self.metadata = self.inference.names, self.inference.metadata

    def predict(self, image_path, raw_path, *, reuse_raw=None):
        image, gray, h, w = self.inference.read_page(image_path)
        outputs, used_raw = self.inference.outputs(image, raw_path, reuse_raw)
        formatted = self.handler._format_output(outputs)
        if len(formatted) != 1:
            raise ValueError("unexpected layout model batch size")
        boxes, masks = formatted[0]["boxes"], formatted[0].get("masks")
        if masks is None or masks.ndim != 3 or len(boxes) != len(masks):
            raise ValueError("raw model boxes and masks cannot be traced")
        captured = {}
        original = self.post.extract_polygon_points_by_masks

        def intercept(bs, ms, scale_ratio, layout_shape_mode):
            captured["boxes"], captured["masks"] = bs.copy(), ms.copy()
            return original(bs, ms, scale_ratio, layout_shape_mode)

        self.post.extract_polygon_points_by_masks = intercept
        try:
            results = self.pp(boxes.copy(), [w, h], masks.copy(), self.handler.layout_shape_mode)
        finally:
            self.post.extract_polygon_points_by_masks = original
        trace, reason = track_original_indices(boxes, masks, self.pp, [w, h], captured, self.post)
        if trace is None:
            raise ValueError(f"model original-row identity unavailable: {reason}")
        labels, stats = page_components((255 - gray.astype(np.int16)) > 16)
        all_predictions, bodies = [], []
        for position, result in enumerate(results):
            index = int(result["order"]) - 1
            if not 0 <= index < len(trace):
                raise ValueError("model postprocessor emitted invalid raw-row identity")
            raw_index = int(trace[index])
            candidate = {
                "candidate_id": f"raw-{raw_index}", "raw_index": raw_index,
                "postprocess_index": position, "polygon_seam_order": int(result["order"]),
                "raw_box_row": boxes[raw_index].tolist(),
                "label": result["label"], "class_id": int(result["cls_id"]),
                "score": float(result["score"]),
                "B": [float(x) for x in result["coordinate"]],
            }
            all_predictions.append(candidate)
            if result["label"] in {"image", "chart", "table"}:
                box, ids = bcc(labels, stats, candidate["B"])
                bodies.append({**candidate, "Bcc": box, "component_count": int(len(ids))})
        return {
            "width_px": w, "height_px": h, "raw_tensor_npz": str(used_raw),
            "raw_tensors": [
                {"name": name, "shape": list(value.shape), "dtype": str(value.dtype)}
                for name, value in zip(self.names, outputs)
            ],
            "identity_trace": "verified", "inference_reused": reuse_raw is not None,
            "model": self.metadata, "all_default_predictions": all_predictions,
            "body_candidates": bodies,
        }
