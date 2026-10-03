# A throwaway Ubuntu desktop-ish environment for testing HomeStream on Linux:
# PulseAudio with a virtual "Speakers" output, and mpv (with MPRIS) as the music player.
#   tools/linux/test.sh    builds this and runs tools/linux/e2e.sh inside it
FROM ubuntu:24.04
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
      sudo ca-certificates curl dbus pulseaudio pulseaudio-utils mpv mpv-mpris ffmpeg \
    && rm -rf /var/lib/apt/lists/*
RUN useradd -m tester && echo "tester ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/tester
COPY --chown=tester . /home/tester/homestream
USER tester
WORKDIR /home/tester/homestream
# Exactly what a Linux user runs (not interactive, so it skips installing Tailscale).
RUN ./setup.sh < /dev/null
CMD ["bash", "tools/linux/e2e.sh"]
