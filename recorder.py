#!/usr/bin/env python3
"""Record noises across all recording devices.

The soundfile module (https://PySoundFile.readthedocs.io/) has to be installed!

"""
import argparse
import tempfile
import queue
import sys
import threading

import sounddevice as sd
import soundfile as sf
import os
import re
from pathlib import Path
from queue import Queue
import time


# By default, the recording folder is relative to the current directory.
DEFAULT_RECORDINGS_DIR = "./noise_recordings"


class _RecordingSession(threading.Thread):
    def __init__(
        self, noise_name: str, device_index, recordings_dir=DEFAULT_RECORDINGS_DIR
    ):
        super().__init__()
        self._noise_name = noise_name
        self.device_index = device_index
        self._recordings_dir = recordings_dir
        self._exit = False
        self._chunk_queue = Queue()

    def stop(self):
        """Stop the recording.

        In general, this will try to wait for one more chunk before terminating.

        """
        self._exit = True

    def run(self):
        # Hard-coded to 16000MHz 1-channel FLAC for now
        #
        # TODO: Use the max sample rate available for the microphone?
        samplerate = 16000
        channels = 1

        # Establish mic directory
        device = sd.query_devices(self.device_index, "input")
        device_name = device["name"]
        mic_directory = re.sub("[^a-zA-Z0-9]+", "_", device_name)
        if len(mic_directory) > 40:
            mic_directory = mic_directory[:40]
        directory = Path(self._recordings_dir, mic_directory)
        directory.mkdir(parents=True, exist_ok=True)

        file_name = tempfile.mktemp(
            # TODO: Better dir navigation probably
            prefix=f"{self._noise_name}_",
            suffix=".flac",
            dir=directory,
        )

        try:
            with sf.SoundFile(
                file_name, mode="x", samplerate=samplerate, channels=channels,
            ) as file:
                with sd.InputStream(
                    samplerate=samplerate,
                    device=self.device_index,
                    channels=channels,
                    callback=self.callback,
                ):
                    print(f"Recording {self._noise_name} with {device_name}")
                    while not self._exit:
                        try:
                            file.write(self._chunk_queue.get(timeout=3))
                        except TimeoutError:
                            pass
        except Exception as e:
            print(f"Recording encountered an exception: \n  {file_name}\n  {e}")
            return
        print(f"Recording finished: {file_name}")

    def callback(self, indata, frames, time, status):
        """Called for each audio block. Writes the chunk."""
        if status:
            # TODO: Log the thread info too
            print(status, file=sys.stderr)
        self._chunk_queue.put(indata.copy())


def input_devices():
    """Attempt to yield all input devices.

    This method only gets devices that use the same input API as the default
    device. Note this may include multiple references to the same device. For
    example, the system's "default input" and the actual device it points.

    """
    # Only use the default API to avoid getting the same device across multiple
    # APIs
    default_api = sd.query_devices("input")["hostapi"]
    for index in sd.query_hostapis(default_api)["devices"]:
        try:
            yield index, sd.query_devices(index, "input")
        except ValueError:
            # Not an input device
            pass


class Recorder(object):
    """Used to record noises with multiple devices."""

    def __init__(self):
        self._recording_threads = []
        self._lock = threading.Lock()

    def record(self, noise_name):
        """Record `noise_name` with every input device."""
        with self._lock:
            if self._recording_threads:
                raise RuntimeError(
                    "Already recording. Please `stop` the previous recording first."
                )
            for index, device in input_devices():
                session = _RecordingSession(noise_name, index)
                self._recording_threads.append(session)
                session.start()

    def recording(self):
        """Is this `Recorder` currently recording?"""
        with self._lock:
            return bool(self._recording_threads)

    def stop(self):
        """Stop recording."""
        with self._lock:
            for session in self._recording_threads:
                session.stop()
            for session in self._recording_threads:
                session.join(timeout=1)
            self._recording_threads = []

    def __del__(self):
        # Avoid hanging threads inside Talon
        #
        # TODO: Establish the right way to do this
        try:
            self._stop()
        except:
            pass


if __name__ == "__main__":
    recorder = Recorder()
    recorder.record("test_noise")
    try:
        while recorder.recording():
            time.sleep(0.1)
    except:
        try:
            recorder.stop()
        except:
            pass
        raise
