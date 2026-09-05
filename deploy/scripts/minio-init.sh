#!/bin/sh
# Pi platform - MinIO bucket 初始化（手册 §4.2）
# staging/ = 临时区；cas/ = 内容寻址，开启对象锁（WORM）。
set -eu

MC=/usr/bin/mc

# 等待 MinIO 就绪（mc ready 会轮询）
"$MC" alias set pi "$MINIO_ENDPOINT" "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD"
"$MC" ready pi

# staging：临时区，无对象锁
"$MC" mb --ignore-existing pi/staging

# cas：内容寻址，创建时开启对象锁（object-lock），并设默认合规保留（WORM 雏形）
"$MC" mb --ignore-existing --with-lock pi/cas
"$MC" retention set --default compliance 90d pi/cas

echo "minio-init: buckets ready"
"$MC" ls pi