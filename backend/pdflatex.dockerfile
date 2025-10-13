# Ubuntu-based TeX Live image for building LaTeX documents.
# Balanced install (not texlive-full) with common engines, fonts, and tools.

FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive \
    TZ=Etc/UTC \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        make \
        perl \
        xz-utils \
        latexmk \
        texlive-base \
        texlive-latex-base \
        texlive-latex-recommended \
        texlive-latex-extra \
        texlive-fonts-recommended \
        texlive-fonts-extra \
        texlive-lang-cyrillic \
        cm-super \
    && rm -rf /var/lib/apt/lists/*

# Note: To include everything (much larger image), replace the list above with:
# ```
# RUN apt-get update && apt-get install -y texlive-full && rm -rf /var/lib/apt/lists/*
# ```

# No entrypoint: call `latexmk`/`pdflatex` explicitly when running the container.
