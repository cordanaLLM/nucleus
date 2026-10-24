# Copyright 2026 The Lusoris Authors
# SPDX-License-Identifier: EUPL-1.2
#
# docker/Dockerfile.builder — the kernel build environment CI uses, as a local container.
#
# The base is the ubuntu:26.04 digest every workflow pins, and the packages come from
# scripts/install-build-toolchain.sh, the one list build-matrix.yml and publish-release.yml
# install, so a local build compiles with the toolchain the matrix proved.
# tests/test_builder_image.py keeps both in step; build-matrix.yml builds this image.

FROM ubuntu:26.04@sha256:c098726dd9b9733f3e885f8de0ef375206a311b0a317eadc40d347b1115fb944

LABEL org.opencontainers.image.title="lusoris-kernel-builder" \
      org.opencontainers.image.description="The nucleus kernel build environment: ubuntu:26.04 with scripts/install-build-toolchain.sh" \
      org.opencontainers.image.vendor="Lusoris" \
      org.opencontainers.image.licenses="EUPL-1.2" \
      org.opencontainers.image.source="https://github.com/cordanaLLM/nucleus"

ENV LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    TZ=UTC

COPY scripts/install-build-toolchain.sh /usr/local/sbin/install-build-toolchain.sh
RUN /usr/local/sbin/install-build-toolchain.sh --with-qemu \
    && rm -rf /var/lib/apt/lists/*

# The ubuntu image already holds a user with uid 1000, so builder takes the next free one.
RUN useradd -m -s /bin/bash builder \
    && mkdir -p /opt/lusoris/src \
    && chown builder:builder /opt/lusoris/src

USER builder
WORKDIR /opt/lusoris/src

CMD ["/bin/bash"]
