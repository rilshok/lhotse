from typing import Optional, Tuple

from intervaltree import IntervalTree  # type: ignore


class TrimmingTree:
    """
    TrimmingTree is designed for easy calculation of a new point position
    in case of cutting several segments from the timeline.
    """

    def __init__(self, anchor: float = 0.0, ndigits: Optional[int] = None):
        self._anchor = anchor
        self._tree = IntervalTree()
        self._merged = False
        self._ndigits = ndigits

    def add_segment(self, start: float, duration: float) -> None:
        """Add a new time segment to the cut, defined by start time and duration"""
        self._tree.addi(start, start + duration)  # type: ignore
        self._merged = False

    def add_interval(self, start: float, end: float) -> None:
        """Add a new time interval to the cut, defined by its start and end times"""
        self._tree.addi(start, end)  # type: ignore
        self._merged = False

    def _merge(self) -> None:
        """Merge overlapping intervals in the tree if needed"""
        if self._merged:
            return
        self._tree.merge_overlaps()  # type: ignore
        self._merged = True

    def __call__(self, point: float) -> float:
        """
        Trims a single `point` based on a reference `anchor`
        and a collection of intervals to be deleted.

        Returns the trimmed time point.
        """
        self._merge()
        tree = self._tree.copy()
        tree.slice(self._anchor)  # type: ignore
        tree.slice(point)  # type: ignore
        overlap = tree.overlap(*sorted((point, self._anchor)))  # type: ignore
        delta = sum(o.end - o.begin for o in overlap)  # type: ignore
        if point >= self._anchor:
            delta *= -1
        answer = point + delta
        if self._ndigits is not None:
            return round(answer, self._ndigits)
        return answer

    def interval(self, start: float, end: float) -> Tuple[float, float]:
        """
        Returns a tuple containing the trimmed `start` and `end` points
        for a given interval defined by its `start` and `end` points
        """
        return self(start), self(end)

    def segment(self, start: float, duration: float) -> Tuple[float, float]:
        """
        Returns a tuple containing the trimmed `start` and `duration` points
        for a given segment defined by its `start` and `duration` points.
        """
        start_, end_ = self.interval(start, start + duration)
        return start_, end_ - start_

    def __repr__(self):
        """Representation of the trimmer tree"""
        return repr(self._tree)
