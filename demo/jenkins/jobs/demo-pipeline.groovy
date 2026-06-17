// demo/jenkins/jobs/demo-pipeline.groovy
// 模拟完整 SDK 构建流水线：build → package → Nexus/MinIO/catalog DB
// 因为 demo 没有真实的 multi-platform toolchain，所以用 sleep + echo 模拟

pipelineJob("sdk-demo-pipeline") {
  description("Demo: Android + Linux 多平台模拟构建，上传 MinIO，写 catalog DB")
  definition {
    cps {
      script('''
pipeline {
    agent any
    options {
        timestamps()
        buildDiscarder(logRotator(numToKeepStr: "20"))
        timeout(time: 10, unit: "MINUTES")
    }
    environment {
        PRODUCT    = "rtc-sdk"
        VERSION    = "1.0.0-demo.${BUILD_NUMBER}"
        CHANNEL    = "dev"
        GIT_COMMIT = "a1b2c3d"
        BUILD_ID   = "${BUILD_NUMBER}"

        // MinIO 连接信息
        S3_ENDPOINT       = "http://minio:9000"
        AWS_ACCESS_KEY_ID = "${MINIO_JENKINS_AK}"
        AWS_SECRET_ACCESS_KEY = "${MINIO_JENKINS_SK}"

        // PostgreSQL catalog DB
        PGHOST     = "catalog-db"
        PGPORT     = "5432"
        PGUSER     = "catalog"
        PGPASSWORD = "${PG_PASSWORD}"
        PGDATABASE = "catalog"
    }
    stages {
        stage("Checkout") {
            steps {
                echo "Simulating git checkout..."
                sleep 2
                sh "mkdir -p dist/android dist/linux && echo '${VERSION}' > VERSION"
            }
        }
        stage("Build Android") {
            steps {
                echo "Simulating Android NDK build..."
                sleep 5
                sh """
                    echo 'fake android .so' > dist/android/libsdk.so
                    echo 'fake android dSYM' > dist/android/symbols/libsdk.so.sym
                    zip -j dist/android/sdk-android.zip dist/android/libsdk.so
                    sha256sum dist/android/sdk-android.zip | awk '{print \$1}' > dist/android/sdk-android.zip.sha256
                """
            }
        }
        stage("Build Linux") {
            steps {
                echo "Simulating Linux cmake build..."
                sleep 5
                sh """
                    echo 'fake linux .so' > dist/linux/libsdk.so
                    echo 'fake linux debug' > dist/linux/symbols/libsdk.so.sym
                    zip -j dist/linux/sdk-linux.zip dist/linux/libsdk.so
                    sha256sum dist/linux/sdk-linux.zip | awk '{print \$1}' > dist/linux/sdk-linux.zip.sha256
                """
            }
        }
        stage("Aggregate") {
            steps {
                echo "Aggregating artifacts & computing SBOM..."
                sh """
                    cat > dist/metadata.json <<JSON
{
  "product": "${PRODUCT}",
  "version": "${VERSION}",
  "channel": "${CHANNEL}",
  "git_commit": "${GIT_COMMIT}",
  "jenkins_url": "${BUILD_URL}",
  "build_id": ${BUILD_ID},
  "timestamp": "$(date -Iseconds)"
}
JSON
                """
            }
        }
        stage("Upload to MinIO") {
            steps {
                withCredentials([
                    usernamePassword(
                        credentialsId: "minio-jenkins",
                        usernameVariable: "MINIO_AK",
                        passwordVariable: "MINIO_SK"
                    )
                ]) {
                    sh """
                        for f in dist/android/sdk-android.zip dist/linux/sdk-linux.zip; do
                            plat=\$(dirname "\$f" | xargs basename)
                            key="dev/${PRODUCT}/${VERSION}/${plat}/\$(basename \$f)"
                            echo "[minio] uploading \$key"
                            curl -sSf --connect-timeout 5 \
                                -X PUT \
                                -H "Content-Type: application/zip" \
                                --upload-file "\$f" \
                                "http://minio:9000/sdk-packets/\${key}?access_key=\${MINIO_AK}&secret_key=\${MINIO_SK}"
                        done
                    """
                }
            }
        }
        stage("Write catalog DB") {
            steps {
                withCredentials([
                    usernamePassword(
                        credentialsId: "catalog-db",
                        usernameVariable: "DB_USER",
                        passwordVariable: "DB_PASS"
                    )
                ]) {
                    sh """
                        for plat in android linux; do
                            zip_file="dist/${plat}/sdk-${plat}.zip"
                            sha=\$(cat "dist/${plat}/sdk-${plat}.zip.sha256")
                            psql "postgresql://${DB_USER}:${DB_PASS}@catalog-db:5432/catalog" <<SQL
INSERT INTO artifacts (product, channel, platform, version, build_id, file_path, sha256)
VALUES (
    '${PRODUCT}',
    '${CHANNEL}',
    '${plat}',
    '${VERSION}',
    ${BUILD_ID},
    'dev/${PRODUCT}/${VERSION}/${plat}/sdk-${plat}.zip',
    '\${sha}'
)
ON CONFLICT (product, channel, platform, version) DO UPDATE SET
    build_id = EXCLUDED.build_id,
    file_path = EXCLUDED.file_path,
    sha256 = EXCLUDED.sha256,
    updated_at = now();
SQL
                        done
                        echo "Catalog updated for ${BUILD_ID}"
                    """
                }
            }
        }
    }
    post {
        success {
            echo "BUILD SUCCESS: ${PRODUCT} ${VERSION} (${CHANNEL}, build ${BUILD_ID})"
        }
        failure {
            echo "BUILD FAILED"
        }
    }
}
'''.stripIndent())
    }
  }
  logRotator {
    numToKeep(20)
  }
}