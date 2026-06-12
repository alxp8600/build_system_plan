#!/usr/bin/env bash
# sdk-build-plan/scripts-stub/publish.sh
# 业务仓库 ci/scripts/publish.sh
# 用法: publish.sh <channel>   # channel = dev|staging|release
#
# 约定:
#   PRODUCT / VERSION / RELEASE / GIT_COMMIT 由 Jenkinsfile 注入
#   dist/<platform>/*.zip / dist/<platform>/symbols/*  由 package.sh 产出
#   dist/metadata.json 由 aggregate.sh 产出
#
# 环境凭证(全部从 Jenkins credentials 注入):
#   NEXUS_URL  NEXUS_USER  NEXUS_PASS
#   MINIO_ENDPOINT  AWS_ACCESS_KEY_ID  AWS_SECRET_ACCESS_KEY  AWS_REGION
#   SENTRY_URL  SENTRY_ORG  SENTRY_AUTH_TOKEN
#   FEISHU_WEBHOOK (可选)
set -euo pipefail

CHANNEL="${1:?usage: publish.sh dev|staging|release}"
: "${PRODUCT:?}"; : "${VERSION:?}"; : "${RELEASE:?}"; : "${GIT_COMMIT:?}"

echo ">>> publish $PRODUCT $VERSION to $CHANNEL"

NEXUS_RAW_REPO="sdk-raw-${CHANNEL}"
MINIO_SYM_BUCKET="sdk-symbols"
SENTRY_PROJECT="${PRODUCT}"
DIST=dist

# ---------------------------------------------------------------------------
# 1) 上传二进制 zip 到 Nexus raw
# ---------------------------------------------------------------------------
for zip in "${DIST}"/*/*.zip; do
    [ -e "$zip" ] || continue
    rel="${zip#${DIST}/}"                       # e.g. android/rtc-sdk-1.7.0-android.zip
    target="${NEXUS_URL}/repository/${NEXUS_RAW_REPO}/${PRODUCT}/${VERSION}/${rel}"
    echo "[nexus] PUT $target"
    curl -sSf -u "${NEXUS_USER}:${NEXUS_PASS}" --upload-file "$zip" "$target"

    # 顺带传 sha256
    sha="$(sha256sum "$zip" | awk '{print $1}')"
    echo -n "$sha" | curl -sSf -u "${NEXUS_USER}:${NEXUS_PASS}" \
        --data-binary @- -H 'Content-Type: text/plain' \
        "${target}.sha256"
done

# metadata.json / sbom.cdx.json 一并上传
for f in "${DIST}/metadata.json" "${DIST}/sbom.cdx.json"; do
    [ -f "$f" ] || continue
    bn="$(basename "$f")"
    curl -sSf -u "${NEXUS_USER}:${NEXUS_PASS}" --upload-file "$f" \
        "${NEXUS_URL}/repository/${NEXUS_RAW_REPO}/${PRODUCT}/${VERSION}/${bn}"
done

# ---------------------------------------------------------------------------
# 2) 上传符号到 MinIO + Sentry
# ---------------------------------------------------------------------------
# 2.1 全量原始符号 -> MinIO (取证存档, 长期保留)
if command -v aws >/dev/null; then
    for plat_dir in "${DIST}"/*/symbols; do
        [ -d "$plat_dir" ] || continue
        plat="$(basename "$(dirname "$plat_dir")")"
        prefix="s3://${MINIO_SYM_BUCKET}/${CHANNEL}/${PRODUCT}/${VERSION}/${plat}/"
        echo "[minio] sync ${plat_dir} -> ${prefix}"
        aws --endpoint-url "${MINIO_ENDPOINT}" s3 sync \
            "$plat_dir" "$prefix" --no-progress --only-show-errors
    done
fi

# 2.2 符号化所需子集 -> Sentry (供在线 stacktrace)
if command -v sentry-cli >/dev/null; then
    sentry-cli releases new   -p "$SENTRY_PROJECT" "$RELEASE"
    sentry-cli releases set-commits "$RELEASE" --commit "$(git config --get remote.origin.url | sed 's/.*[/:]//; s/\.git$//')@${GIT_COMMIT}"

    # iOS dSYM / Android mapping / Windows PDB / Linux debug-info
    for plat_dir in "${DIST}"/*/symbols; do
        [ -d "$plat_dir" ] || continue
        sentry-cli debug-files upload --include-sources -p "$SENTRY_PROJECT" "$plat_dir" || true
    done

    sentry-cli releases finalize "$RELEASE"
fi

# ---------------------------------------------------------------------------
# 3) Maven 通道 (Android / Java SDK)
# ---------------------------------------------------------------------------
if [ -f "build.gradle" ] || [ -f "build.gradle.kts" ]; then
    echo "[gradle] publish to sdk-maven-${CHANNEL}"
    ./gradlew \
        -Pversion="$VERSION" \
        -PnexusUrl="${NEXUS_URL}/repository/sdk-maven-${CHANNEL}/" \
        -PnexusUser="$NEXUS_USER" -PnexusPass="$NEXUS_PASS" \
        publish
fi

# ---------------------------------------------------------------------------
# 4) CocoaPods 通道 (iOS)
# ---------------------------------------------------------------------------
if [ -f "${PRODUCT}.podspec" ] && [ "$CHANNEL" = "release" ]; then
    echo "[pods] push to private spec repo"
    pod repo push my-private-spec "${PRODUCT}.podspec" --allow-warnings
fi

# ---------------------------------------------------------------------------
# 5) 标记 release(不可变) - release 通道下尝试再次 publish 同版本会被 ALLOW_ONCE 阻断
# ---------------------------------------------------------------------------
echo ">>> done. RELEASE=$RELEASE"