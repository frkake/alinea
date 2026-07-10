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

COPY docker/texlive/smoke-ja.tex /tmp/alinea-smoke-ja.tex

RUN cd /tmp \
  && latexmk -lualatex -interaction=nonstopmode -halt-on-error alinea-smoke-ja.tex \
  && test -s /tmp/alinea-smoke-ja.pdf \
  && latexmk -C alinea-smoke-ja.tex \
  && rm -f /tmp/alinea-smoke-ja.tex

WORKDIR /work
