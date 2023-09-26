from dataclasses import dataclass
from typing import Iterable, List, Optional, Tuple

import numpy as np
from intervaltree import IntervalTree  # type: ignore

from lhotse.tools.trimmer import TrimmerTree
from lhotse.utils import Seconds

from .transform import AudioTransform


@dataclass
class Defragmentation(AudioTransform):
    segments: List[Tuple[Seconds, Seconds]]

    def __init__(self, segments: Iterable[Tuple[Seconds, Seconds]]):
        self.segments = []
        for interval in self._intervals(segments):  # type: ignore
            ofset = interval.begin  # type: ignore
            duration = interval.end - interval.begin  # type: ignore
            self.segments.append((ofset, duration))  # type: ignore

    @staticmethod
    def _intervals(segments: Iterable[Tuple[Seconds, Seconds]]) -> IntervalTree:
        tree = IntervalTree()
        for ofset, duration in segments:
            tree.addi(ofset, ofset + duration)  # type: ignore
        tree.merge_overlaps()  # type: ignore
        return tree

    @property
    def trimmer(self) -> TrimmerTree:
        tree = IntervalTree()
        tree.addi(-float("inf"), float("inf"))  # type: ignore
        intervals = self._intervals(self.segments)
        for segment in intervals:  # type: ignore
            tree.slice(segment.begin)  # type: ignore
            tree.slice(segment.end)  # type: ignore
            tree.remove(segment)  # type: ignore
        trimmer = TrimmerTree(0.0)
        for interval in tree:  # type: ignore
            trimmer.add_interval(interval.begin, interval.end)  # type: ignore
        return trimmer

    def __call__(self, samples: np.ndarray, sampling_rate: int) -> np.ndarray:
        raise NotImplementedError()
        return samples

    def reverse_timestamps(
        self,
        offset: Seconds,
        duration: Optional[Seconds],
        sampling_rate: Optional[int],
    ) -> Tuple[Seconds, Optional[Seconds]]:
        if duration is None:
            return self.trimmer(offset), None
        return self.trimmer.segment(offset, duration)
