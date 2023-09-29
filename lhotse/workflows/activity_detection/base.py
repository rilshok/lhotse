import abc
import multiprocessing
import sys
import warnings
from concurrent.futures import ProcessPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
from functools import partial
from itertools import chain
from multiprocessing.context import BaseContext
from typing import (
    Any,
    Callable,
    Dict,
    Generic,
    Iterable,
    List,
    Optional,
    Sequence,
    Type,
    TypeVar,
    Union,
)

import numpy as np

from lhotse.audio.recording import Recording
from lhotse.audio.recording_set import RecordingSet
from lhotse.supervision import SupervisionSegment, SupervisionSet

if sys.version_info >= (3, 8):
    from typing import Protocol
else:
    Protocol = object


@dataclass
class Activity:
    start: float
    duration: float


class ActivityDetector(abc.ABC):
    def __init__(self, detector_name: str, sampling_rate: int, device: str = "cpu"):
        self._detector_name = detector_name
        self._sampling_rate = sampling_rate
        self._device = device

    @property
    def name(self) -> str:
        return self._detector_name

    @property
    def device(self) -> str:
        return self._device

    @property
    def sampling_rate(self) -> int:
        return self._sampling_rate

    def __call__(self, recording: Recording) -> List[SupervisionSegment]:
        resampled = recording.resample(self._sampling_rate)
        audio = resampled.load_audio()  # type: ignore

        uid_template = "{recording_id}-{detector_name}-{channel}-{number:05}"

        result: List[SupervisionSegment] = []
        for channel, track in enumerate(audio):
            track = np.squeeze(track)
            activities = self.forward(track)

            for i, activity in enumerate(activities):
                uid = uid_template.format(
                    recording_id=recording.id,
                    detector_name=self.name,
                    channel=channel,
                    number=i,
                )
                segment = SupervisionSegment(
                    id=uid,
                    recording_id=recording.id,
                    start=activity.start,
                    duration=activity.duration,
                    channel=channel,
                )
                result.append(segment)

        return result

    @abc.abstractmethod
    def forward(self, track: np.ndarray) -> List[Activity]:  # pragma: no cover
        raise NotImplementedError()

    @classmethod
    def force_download(cls) -> None:  # pragma: no cover
        """Do some work for preloading / resetting the model state."""
        return None


class ActivityDetectionProcessor:
    _detectors: Dict[Optional[int], ActivityDetector] = {}

    def __init__(
        self,
        detector_kls: Type[ActivityDetector],
        num_jobs: int,
        device: str = "cpu",
        verbose: bool = False,
    ):
        self._make_detecor = partial(detector_kls, device=device)
        self._num_jobs = num_jobs
        self._verbose = verbose

    def _init_detector(self):
        pid = multiprocessing.current_process().pid
        self._detectors[pid] = self._make_detecor()

    def _process_recording(self, record: Recording) -> List[SupervisionSegment]:
        pid = multiprocessing.current_process().pid
        detector = self._detectors[pid]
        return detector(record)

    def __call__(self, recordings: RecordingSet) -> SupervisionSet:
        pool = ProcessPoolExecutor(
            max_workers=self._num_jobs,
            initializer=self._init_detector,
            mp_context=multiprocessing.get_context("spawn"),
        )

        with pool as executor:
            try:
                parts = executor.map(self._process_recording, recordings)
                if self._verbose:
                    from tqdm.auto import tqdm  # pylint: disable=C0415

                    parts = tqdm(
                        parts,
                        total=len(recordings),
                        desc="Detecting activities",
                        unit="rec",
                    )
                segments = chain.from_iterable(parts)
                return SupervisionSet.from_segments(segments)
            except KeyboardInterrupt as exc:  # pragma: no cover
                pool.shutdown(wait=False)
                if self._verbose:
                    print("Activity detection interrupted by the user.")
                raise exc
            except Exception as exc:  # pragma: no cover
                pool.shutdown(wait=False)
                raise RuntimeError(
                    "Activity detection failed. Please report this issue."
                ) from exc
            finally:
                self._detectors.clear()


CaseT = TypeVar("CaseT")
ResultT = TypeVar("ResultT")


class DoWork(Protocol[CaseT, ResultT]):
    def __call__(
        self,
        obj: CaseT,
        model: Callable[[CaseT], ResultT],
        **kwargs: Any,
    ) -> ResultT:
        pass


class ProcessWorker(Generic[CaseT, ResultT]):
    """A wrapper for a function that does the actual work in a multiprocessing context."""

    models: Dict[Optional[int], Callable[[CaseT], ResultT]] = {}

    def __init__(
        self,
        gen_model: Callable[[], Callable[[CaseT], ResultT]],
        do_work: DoWork[CaseT, ResultT],
        # "error", "ignore", "always", "default", "module", "once"
        warnings_mode: Optional[str] = None,
    ):
        self._gen_model = gen_model
        self._do_work = do_work
        self._warnings_mode = warnings_mode

    def _get_model(self) -> Callable[[CaseT, Any], ResultT]:
        pid = multiprocessing.current_process().pid
        model = self.models.get(pid)
        if model is None:
            model = self._gen_model()
            self.models[pid] = model
        return model

    def __call__(self, obj: CaseT, **kwargs: Any) -> ResultT:
        if self._warnings_mode is not None:
            warnings.simplefilter(self._warnings_mode)
        return self._do_work(obj, model=self._get_model(), **kwargs)

    def clear(self):
        self.models.clear()


class Processor(Generic[CaseT, ResultT]):
    """Multiprocessing wrapper for ProcessWorker."""

    def __init__(
        self,
        worker: ProcessWorker[CaseT, ResultT],
        *,
        num_jobs: int,
        verbose: bool = False,
        mp_context: Optional[Union[BaseContext, str]] = None,
    ):
        self._worker = worker
        self._num_jobs = num_jobs
        self._verbose = verbose

        if mp_context is None:
            self._mp_context = None
        else:
            if isinstance(mp_context, str):
                mp_context = multiprocessing.get_context(mp_context)
            self._mp_context = mp_context

    @contextmanager
    def handle_errors(self, pool: ProcessPoolExecutor):
        with pool as executor:
            try:
                yield executor
            except KeyboardInterrupt as exc:  # pragma: no cover
                pool.shutdown(wait=False)
                if self._verbose:
                    print("Processing interrupted by the user.")
                raise exc
            except Exception as exc:  # pragma: no cover
                pool.shutdown(wait=False)
                raise RuntimeError("Processing failed.") from exc
            finally:
                self._worker.clear()

    def __call__(
        self,
        sequence: Sequence[CaseT],
        **kwargs: Any,
    ) -> Iterable[ResultT]:
        """Iterate over the results of processing the sequence with the worker."""
        pool = ProcessPoolExecutor(
            max_workers=self._num_jobs,
            # initializer=...,
            mp_context=self._mp_context,
        )

        with self.handle_errors(pool) as executor:
            factory = partial(self._worker, **kwargs)
            results = executor.map(factory, sequence)

            if self._verbose:
                from tqdm.auto import tqdm  # pylint: disable=import-outside-toplevel

                results = tqdm(
                    results,
                    total=len(sequence),
                    desc="Multiprocessing",
                    unit="task",
                )

            yield from results
