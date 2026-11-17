"""Check that a caption barrier does not cut source ink connected to its seed."""
from __future__ import annotations


class PageInk:
    """Keep strong component identity while attributing connected pale edges."""

    def __init__(self, image, gray=None):
        import cv2
        import numpy as np

        if hasattr(image, "convert"):
            rgb = np.asarray(image.convert("RGB"))
            gray = np.asarray(image.convert("L")) if gray is None else np.asarray(gray)
        else:
            rgb = np.asarray(image)
            if gray is None:
                if rgb.ndim == 2:
                    gray = rgb
                else:
                    from PIL import Image
                    gray = np.asarray(Image.fromarray(rgb).convert("L"))
            else:
                gray = np.asarray(gray)
        strong = np.ascontiguousarray(gray < 239, dtype=np.uint8)
        visible = np.any(rgb != 255, axis=2) if rgb.ndim == 3 else rgb != 255
        weak = np.ascontiguousarray((gray >= 239) & visible, dtype=np.uint8)
        _, self.labels, self.stats, _ = cv2.connectedComponentsWithStats(
            strong, connectivity=8, ltype=cv2.CV_32S)
        _, self.weak_labels, self.weak_stats, _ = cv2.connectedComponentsWithStats(
            weak, connectivity=8, ltype=cv2.CV_32S)
        pairs = []
        h, w = gray.shape
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                if not dx and not dy:
                    continue
                y0, y1 = max(0, -dy), min(h, h-dy)
                x0, x1 = max(0, -dx), min(w, w-dx)
                weak_view = self.weak_labels[y0:y1, x0:x1]
                strong_view = self.labels[y0+dy:y1+dy, x0+dx:x1+dx]
                mask = (weak_view != 0) & (strong_view != 0)
                if mask.any():
                    pairs.append(np.unique(np.stack((weak_view[mask], strong_view[mask]), axis=1), axis=0))
        self.adjacency = (np.unique(np.concatenate(pairs), axis=0) if pairs
                          else np.empty((0, 2), dtype=np.int32))

    def _selected(self, seed):
        import numpy as np

        sx0, sy0, sx1, sy1 = seed
        selected = np.unique(self.labels[sy0:sy1, sx0:sx1])
        selected = selected[selected != 0]
        weak_in_seed = np.unique(self.weak_labels[sy0:sy1, sx0:sx1])
        weak_in_seed = weak_in_seed[weak_in_seed != 0]
        pairs = self.adjacency
        mine = np.isin(pairs[:, 1], selected)
        touched = np.unique(pairs[mine, 0])
        foreign = np.unique(pairs[~mine, 0])
        owned = np.setdiff1d(touched, foreign)
        ambiguous = np.union1d(np.intersect1d(touched, foreign), np.setdiff1d(weak_in_seed, owned))
        return selected, owned, ambiguous

    def bbox(self, seed):
        """Return all unambiguously owned weak edges plus the original core."""
        import cv2

        def component_box(stats, ids):
            if not len(ids):
                return None
            data = stats[ids]
            return [int(data[:, cv2.CC_STAT_LEFT].min()), int(data[:, cv2.CC_STAT_TOP].min()),
                    int((data[:, cv2.CC_STAT_LEFT] + data[:, cv2.CC_STAT_WIDTH]).max()),
                    int((data[:, cv2.CC_STAT_TOP] + data[:, cv2.CC_STAT_HEIGHT]).max())]

        selected, weak, ambiguous = self._selected(seed)
        boxes = [b for b in (component_box(self.stats, selected),
                             component_box(self.weak_stats, weak)) if b is not None]
        # Unknown pale components are retained only inside the model seed;
        # extending them across the page would silently claim foreign content.
        if len(ambiguous):
            import numpy as np
            sx0, sy0, sx1, sy1 = seed
            yy, xx = np.nonzero(np.isin(self.weak_labels[sy0:sy1, sx0:sx1], ambiguous))
            if len(xx):
                boxes.append([int(xx.min())+sx0, int(yy.min())+sy0,
                              int(xx.max())+sx0+1, int(yy.max())+sy0+1])
        box = ([min(b[0] for b in boxes), min(b[1] for b in boxes),
                max(b[2] for b in boxes), max(b[3] for b in boxes)] if boxes else None)
        return box, int(len(selected)), int(len(ambiguous))

    def region_consistency(self, viewport, region):
        """Find visible viewport components with no pixel in the caption region.

        Connectivity is computed within the viewport, so unrelated page ink
        outside it cannot join components. The viewport is never cropped to the
        PF2 region. Connected antialiased edges remain part of their component.
        """
        import cv2
        import numpy as np

        x0, y0, x1, y1 = viewport
        ax, ay, bx, by = region
        visible = np.ascontiguousarray((self.labels[y0:y1, x0:x1] != 0) |
                                      (self.weak_labels[y0:y1, x0:x1] != 0), dtype=np.uint8)
        count, labels, stats, _ = cv2.connectedComponentsWithStats(
            visible, connectivity=8, ltype=cv2.CV_32S)
        ix0, iy0, ix1, iy1 = max(x0, ax), max(y0, ay), min(x1, bx), min(y1, by)
        seen = (np.unique(labels[iy0-y0:iy1-y0, ix0-x0:ix1-x0])
                if ix0 < ix1 and iy0 < iy1 else np.empty(0, dtype=np.int32))
        outside = np.setdiff1d(np.arange(1, count), seen)
        return {"status": "no_visible_ink" if count == 1 else "unassociated_ink" if len(outside) else "consistent",
                "viewport_px": viewport, "region_px": region,
                "unassociated_component_count": int(len(outside)),
                "unassociated_visible_pixels": int(stats[outside, cv2.CC_STAT_AREA].sum())}

    def visible_pixels(self, box):
        import numpy as np

        x0, y0, x1, y1 = box
        return int(np.count_nonzero((self.labels[y0:y1, x0:x1] != 0) |
                                    (self.weak_labels[y0:y1, x0:x1] != 0)))

    def barrier_loss(self, seed, panel, crop):
        """Count exact cut ink in full-page 8-connected seed components."""
        import numpy as np

        labels = self.labels
        px0, py0, px1, py1 = panel
        cx0, cy0, cx1, cy1 = crop
        selected, weak, ambiguous = self._selected(seed)
        if not len(selected) and not len(ambiguous):
            return {"seed_component_count": 0, "strict_lost_pixels": 0,
                    "ambiguous_weak_component_count": 0}
        target = (np.isin(labels[py0:py1, px0:px1], selected) |
                  np.isin(self.weak_labels[py0:py1, px0:px1], weak))
        sx0, sy0, sx1, sy1 = seed
        x0, y0, x1, y1 = max(px0, sx0), max(py0, sy0), min(px1, sx1), min(py1, sy1)
        if len(ambiguous) and x0 < x1 and y0 < y1:
            target[y0-py0:y1-py0, x0-px0:x1-px0] |= np.isin(
                self.weak_labels[y0:y1, x0:x1], ambiguous)
        kept = np.zeros_like(target, dtype=bool)
        x0, y0 = max(px0, cx0), max(py0, cy0)
        x1, y1 = min(px1, cx1), min(py1, cy1)
        if x0 < x1 and y0 < y1:
            kept[y0-py0:y1-py0, x0-px0:x1-px0] = True
        return {"seed_component_count": int(len(selected)),
                "strict_lost_pixels": int(np.count_nonzero(target & ~kept)),
                "ambiguous_weak_component_count": int(len(ambiguous))}
