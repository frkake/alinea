FROM texlive/texlive:latest

RUN apt-get update \
  && apt-get install -y --no-install-recommends \
    fontconfig \
    fonts-noto-cjk \
    fonts-noto-cjk-extra \
    ghostscript \
    qpdf \
  && rm -rf /var/lib/apt/lists/* \
  && fc-cache -f

WORKDIR /work
