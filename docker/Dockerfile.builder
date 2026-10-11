# Copyright 2026 The Lusoris Authors
# SPDX-License-Identifier: EUPL-1.2
#
# docker/Dockerfile.builder — Hermetic Multi-Architecture Kernel Builder Container
# Targets: x86_64, arm64, and riscv64 native and cross-compilation

FROM ubuntu:24.04

LABEL org.opencontainers.image.title="lusoris-kernel-builder" \
      org.opencontainers.image.description="Hermetic multi-architecture Linux kernel compiler and UKI synthesis engine" \
      org.opencontainers.image.vendor="Lusoris" \
      org.opencontainers.image.licenses="EUPL-1.2" \
      org.opencontainers.image.source="https://github.com/cordanaLLM/nucleus"

ENV DEBIAN_FRONTEND=noninteractive \
    TZ=Etc/UTC \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    SOURCE_DATE_EPOCH=1700000000 \
    KBUILD_BUILD_TIMESTAMP="Nov 15 2023"

# Install hermetic toolchains, cross-compilers, packaging utilities, and QEMU microVM emulators
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    clang \
    llvm \
    lld \
    gcc-aarch64-linux-gnu \
    g++-aarch64-linux-gnu \
    gcc-riscv64-linux-gnu \
    g++-riscv64-linux-gnu \
    bc \
    bison \
    flex \
    libelf-dev \
    libssl-dev \
    libncurses-dev \
    kmod \
    cpio \
    rsync \
    git \
    tar \
    xz-utils \
    zstd \
    dpkg-dev \
    debhelper \
    diffoscope \
    systemd-ukify \
    sbsigntool \
    dracut \
    qemu-system-x86 \
    qemu-system-arm \
    qemu-system-misc \
    python3 \
    python3-pip \
    ca-certificates \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Set up hermetic unprivileged build user
RUN useradd -u 1000 -m -s /bin/bash builder \
    && mkdir -p /opt/lusoris/src /opt/lusoris/output /opt/lusoris/build \
    && chown -R builder:builder /opt/lusoris

USER builder
WORKDIR /opt/lusoris/src

CMD ["/bin/bash"]
