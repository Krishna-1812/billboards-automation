"""Small real media files for the creative checks, made once per test run with ffmpeg."""

import os
import subprocess

import pytest

W, H = 640, 360


def ff(*args):
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args], check=True, timeout=120)


def make_all(d):
    v = lambda name: os.path.join(d, name)  # noqa: E731
    enc = ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", "25"]
    ff("-f", "lavfi", "-i", f"testsrc2=s={W}x{H}:r=25:d=10", *enc, v("good.mp4"))
    ff("-f", "lavfi", "-i", f"testsrc2=s={W}x{H}:r=25:d=15", *enc, v("long.mp4"))
    ff("-f", "lavfi", "-i", f"testsrc2=s={H}x{W}:r=25:d=10", *enc, v("portrait.mp4"))
    ff("-f", "lavfi", "-i", f"testsrc2=s={W}x{H}:r=25:d=10", "-f", "lavfi", "-i", "sine=f=440:d=10", *enc,
       "-c:a", "aac", "-shortest", v("sound.mp4"))
    ff("-f", "lavfi", "-i", f"color=black:s={W}x{H}:r=25:d=2", "-f", "lavfi", "-i", f"testsrc2=s={W}x{H}:r=25:d=8",
       "-filter_complex", "[0][1]concat=n=2:v=1", *enc, v("blackstart.mp4"))
    ff("-f", "lavfi", "-i", f"testsrc2=s={W}x260:r=25:d=10", "-vf", f"pad={W}:{H}:0:50:black", *enc,
       v("letterbox.mp4"))
    from PIL import Image
    Image.new("RGB", (W, H), "orange").save(v("good.png"))
    Image.new("CMYK", (W, H), (0, 100, 200, 0)).save(v("cmyk.jpg"))
    with open(v("good.mp4"), "rb") as fh:
        data = fh.read()
    with open(v("broken.mp4"), "wb") as fh:
        fh.write(data[: len(data) // 3])
    with open(v("notes.txt"), "w") as fh:
        fh.write("not a video")
    return {name: v(name) for name in os.listdir(d)}


@pytest.fixture(scope="session")
def media(tmp_path_factory):
    return make_all(str(tmp_path_factory.mktemp("media")))


def read(path):
    with open(path, "rb") as fh:
        return fh.read()
