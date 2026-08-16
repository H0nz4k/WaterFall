from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

ALLOWED_EXTENSIONS = {".pcap", ".pcapng", ".cap"}


def safe_capture_name(name: str) -> str:
    base = Path(name or "capture.pcapng").name
    base = re.sub(r"[^A-Za-z0-9._-]+", "_", base).strip("._")
    if not base:
        base = "capture.pcapng"
    suffix = Path(base).suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise ValueError("Podporované přípony: .pcap, .pcapng, .cap")
    return base[:180]


class PcapInspector:
    def __init__(self, directory: str | Path):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)

    @property
    def tshark_path(self) -> str | None:
        return shutil.which("tshark")

    def capabilities(self) -> dict[str, Any]:
        return {
            "tshark_available": bool(self.tshark_path),
            "tshark_path": self.tshark_path or "",
            "extensions": sorted(ALLOWED_EXTENSIONS),
        }

    def _path(self, name: str) -> Path:
        safe = safe_capture_name(name)
        path = (self.directory / safe).resolve()
        root = self.directory.resolve()
        if root not in path.parents:
            raise ValueError("Neplatná cesta capture souboru")
        return path

    def list_files(self) -> list[dict[str, Any]]:
        items = []
        for p in sorted(self.directory.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
            if not p.is_file() or p.suffix.lower() not in ALLOWED_EXTENSIONS:
                continue
            st = p.stat()
            items.append({"name": p.name, "size": st.st_size, "mtime_ns": st.st_mtime_ns})
        return items

    def save_bytes(self, name: str, data: bytes) -> Path:
        if len(data) > 256 * 1024 * 1024:
            raise ValueError("Capture je větší než 256 MiB")
        path = self._path(name)
        if path.exists():
            stem, suffix = path.stem, path.suffix
            i = 1
            while True:
                candidate = path.with_name(f"{stem}_{i:03d}{suffix}")
                if not candidate.exists():
                    path = candidate
                    break
                i += 1
        path.write_bytes(data)
        return path

    def get_path(self, name: str) -> Path:
        path = self._path(name)
        if not path.exists():
            raise FileNotFoundError(name)
        return path

    def delete(self, name: str) -> None:
        self.get_path(name).unlink()

    def _run(self, args: list[str], timeout: int = 20) -> str:
        tshark = self.tshark_path
        if not tshark:
            raise RuntimeError("tshark není nainstalovaný")
        cp = subprocess.run(
            [tshark, *args],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
        if cp.returncode != 0:
            msg = (cp.stderr or cp.stdout or "tshark selhal").strip()
            raise RuntimeError(msg[-3000:])
        return cp.stdout

    def packet_list(self, name: str, display_filter: str = "", limit: int = 500) -> list[dict[str, Any]]:
        path = self.get_path(name)
        limit = max(1, min(int(limit), 5000))
        fields = [
            "frame.number", "frame.time_epoch", "frame.len",
            "_ws.col.Protocol", "_ws.col.Source", "_ws.col.Destination", "_ws.col.Info",
        ]
        args = ["-n", "-r", str(path)]
        if display_filter.strip():
            args += ["-Y", display_filter.strip()]
        args += ["-T", "fields", "-E", "separator=\t", "-E", "quote=n", "-E", "occurrence=f"]
        for field in fields:
            args += ["-e", field]

        out = self._run(args, timeout=30)
        rows = []
        for line in out.splitlines():
            cols = line.split("\t")
            cols += [""] * (len(fields) - len(cols))
            try:
                frame_no = int(cols[0])
            except ValueError:
                continue
            rows.append({
                "frame": frame_no, "time_epoch": cols[1], "length": cols[2],
                "protocol": cols[3], "source": cols[4],
                "destination": cols[5], "info": cols[6],
            })
            if len(rows) >= limit:
                break
        return rows

    def frame_detail(self, name: str, frame_number: int) -> dict[str, Any]:
        path = self.get_path(name)
        if frame_number < 1:
            raise ValueError("frame_number musí být >= 1")
        text = self._run(
            ["-n", "-r", str(path), "-Y", f"frame.number == {int(frame_number)}", "-V", "-x"],
            timeout=20,
        )
        return {"name": path.name, "frame": int(frame_number), "detail": text}
