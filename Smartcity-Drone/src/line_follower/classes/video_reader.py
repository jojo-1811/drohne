from __future__ import annotations

from threading import Thread
from typing import Optional

import av
import numpy as np


class VideoReader:
    """Empfängt und dekodiert den Videostream der Drohne im Hintergrund.

    Ersetzt djitellopys BackgroundFrameRead, weil dessen Empfang ohne Zeitlimit
    liest: Bleibt der Stream stehen, hängt der Thread für immer und hält den
    UDP-Port belegt – ein Neustart des Streams könnte den Port nicht mehr öffnen.
    Hier endet der Thread nach read_timeout Sekunden ohne Daten. Außerdem wird
    ein Paket, das der Decoder nicht verarbeiten kann, übersprungen statt den
    Empfang zu beenden, und das Corrupt-Flag des Decoders mitgeliefert.
    """

    def __init__(self, address: str, open_timeout: float = 5.0, read_timeout: float = 1.0) -> None:
        self._container = av.open(address, timeout=(open_timeout, read_timeout))
        self.frame: Optional[np.ndarray] = None  # RGB, wie bei djitellopy
        self.decoder_corrupt = False
        self._stopped = False
        self._thread = Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        try:
            for packet in self._container.demux(video=0):
                if self._stopped:
                    break
                try:
                    frames = packet.decode()
                except av.error.InvalidDataError:
                    continue
                for decoded in frames:
                    self.decoder_corrupt = decoded.is_corrupt
                    self.frame = decoded.to_ndarray(format="rgb24")
        except av.error.FFmpegError:
            pass  # Zeitlimit oder Abbruch: Die Hauptschleife merkt, dass keine Bilder mehr kommen.
        finally:
            self._container.close()

    def stop(self) -> None:
        self._stopped = True
        self._thread.join(timeout=2.0)
