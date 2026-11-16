"""Check that a caption barrier does not cut source ink connected to its seed."""
from __future__ import annotations


class PageInk:
    """Compute full-page components once and share them across figure seeds."""

    def __init__(self, image):
        import cv2
        import numpy as np

        gray = np.asarray(image.convert("L"))
        ink = np.ascontiguousarray(gray < 239, dtype=np.uint8)
        _, self.labels = cv2.connectedComponents(ink, connectivity=8, ltype=cv2.CV_32S)

    def barrier_loss(self, seed, panel, crop):
        """Count exact cut ink in full-page 8-connected seed components."""
        import numpy as np

        labels = self.labels
        sx0, sy0, sx1, sy1 = seed
        px0, py0, px1, py1 = panel
        cx0, cy0, cx1, cy1 = crop
        selected = np.unique(labels[sy0:sy1, sx0:sx1])
        selected = selected[selected != 0]
        if not len(selected):
            return {"seed_component_count": 0, "strict_lost_pixels": 0}
        target = np.isin(labels[py0:py1, px0:px1], selected)
        kept = np.zeros_like(target, dtype=bool)
        x0, y0 = max(px0, cx0), max(py0, cy0)
        x1, y1 = min(px1, cx1), min(py1, cy1)
        if x0 < x1 and y0 < y1:
            kept[y0-py0:y1-py0, x0-px0:x1-px0] = True
        return {"seed_component_count": int(len(selected)),
                "strict_lost_pixels": int(np.count_nonzero(target & ~kept))}
