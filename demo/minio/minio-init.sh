#!/bin/sh
set -e

echo "=== Connecting to MinIO ==="
mc alias set local http://minio:9000 $MINIO_ROOT_USER $MINIO_ROOT_PASSWORD

echo "=== Creating buckets ==="
for b in sdk-logs sdk-packets; do
  mc mb -p local/$b || echo "bucket $b may already exist"
done

echo "=== Importing lifecycle policies ==="
for f in /lc/*.json; do
  bn=$(basename $f .json)
  mc ilm import local/$bn < $f || echo "lifecycle $bn may already exist"
done

echo "=== Creating access policies ==="
for f in /policies/*.json; do
  bn=$(basename $f .json)
  mc admin policy create local $bn $f || echo "policy $bn may already exist"
done

echo "=== Creating users ==="
# mc admin user add TARGET ACCESSKEY SECRETKEY
# The ACCESSKEY doubles as the username for policy attachment
mc admin user add local $MINIO_APP_AK $MINIO_APP_SK || echo "user $MINIO_APP_AK may already exist"
mc admin policy attach local app-uploader --user $MINIO_APP_AK || echo "policy attach app-uploader may already exist"

mc admin user add local $MINIO_PROXY_AK $MINIO_PROXY_SK || echo "user $MINIO_PROXY_AK may already exist"
mc admin policy attach local proxy-reader --user $MINIO_PROXY_AK || echo "policy attach proxy-reader may already exist"

mc admin user add local $MINIO_JENKINS_AK $MINIO_JENKINS_SK || echo "user $MINIO_JENKINS_AK may already exist"
mc admin policy attach local jenkins-uploader --user $MINIO_JENKINS_AK || echo "policy attach jenkins-uploader may already exist"

echo "=== DONE ==="