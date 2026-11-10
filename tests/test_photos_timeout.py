"""A photo server that sends a trickle of bytes can't hold up a photo run: each download has an overall time limit."""
import io
import time

import pytest

from importer import photos as ph


class Trickle(io.BytesIO):
    def read(self, n=-1):
        time.sleep(0.02)
        return super().read(10)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass


def test_slow_download_is_cut_off(monkeypatch):
    monkeypatch.setattr(ph.urllib.request, "urlopen", lambda req, timeout=0: Trickle(b"x" * 5000))
    monkeypatch.setattr(ph, "MAX_SECONDS", 0.2)
    with pytest.raises(TimeoutError):
        ph._fetch("https://img.example/slow.jpg", attempts=1)
