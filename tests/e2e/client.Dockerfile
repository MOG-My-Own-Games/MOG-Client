# One image for both clients of the end-to-end test (see test_freeciv_save_sync.py):
#   - the Linux client runs MOG Client from /src with this image's Python, and the game through Wine (wine64);
#   - the Windows client runs the same source with Windows' own embeddable Python under Wine, so it takes every
#     `sys.platform == "win32"` path (no prefix, the .cmd launch file, %APPDATA%) and starts the game's .exe directly.
# The source is mounted at /src (Z:\src for the Windows side), never copied, so the test runs what is checked out.

FROM python:3.12-slim-trixie

ENV DEBIAN_FRONTEND=noninteractive \
    WINEDEBUG=-all \
    WINEARCH=win64

RUN dpkg --add-architecture i386 && \
    apt-get update && \
    apt-get install -y --no-install-recommends wine wine64 wine32:i386 ca-certificates curl unzip && \
    rm -rf /var/lib/apt/lists/*

# Windows' embeddable Python, checked against its known hash. Its ._pth file replaces sys.path (PYTHONPATH is ignored),
# so the mounted source is listed there.
ARG WINPYTHON_VERSION=3.12.10
ARG WINPYTHON_SHA256=4acbed6dd1c744b0376e3b1cf57ce906f9dc9e95e68824584c8099a63025a3c3
RUN curl -fsSL -o /tmp/winpython.zip \
        "https://www.python.org/ftp/python/${WINPYTHON_VERSION}/python-${WINPYTHON_VERSION}-embed-amd64.zip" && \
    echo "${WINPYTHON_SHA256}  /tmp/winpython.zip" | sha256sum -c - && \
    mkdir -p /opt/winpython && unzip -q /tmp/winpython.zip -d /opt/winpython && rm /tmp/winpython.zip && \
    printf 'python312.zip\r\n.\r\nZ:\\src\r\nimport site\r\n' > /opt/winpython/python312._pth

# The Windows client's "C:" (its %APPDATA% and %LOCALAPPDATA%, where it keeps its settings and games). Made now, so a test
# run does not wait for wineboot. The Linux client gets a prefix of its own in the game's folder, as MOG gives every game.
RUN wineboot --init && wineserver --wait

ENV PYTHONPATH=/src
WORKDIR /root
CMD ["sleep", "infinity"]
