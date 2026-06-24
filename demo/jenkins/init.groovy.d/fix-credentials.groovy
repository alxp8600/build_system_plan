/**
 * Jenkins init script — fix credentials when encryption keys change
 *
 * This script runs at Jenkins startup (via init.groovy.d) and ensures
 * that critical credentials are re-registered if the Jenkins encryption
 * key has been rotated (e.g. after a fresh deploy with a new jenkins_home).
 *
 * Strategy:
 *   1. Wait patiently for Jenkins + credentials plugin to be fully ready
 *      (polling loop, up to 5 minutes).
 *   2. Delete ALL credentials with the target IDs first — this removes
 *      any un-decryptable entries left from a previous master key.
 *   3. Re-create them with the current master key using env var values.
 *
 * Environment variables expected:
 *   MINIO_JENKINS_AK  — MinIO Jenkins access key
 *   MINIO_JENKINS_SK  — MinIO Jenkins secret key
 *   PG_PASSWORD       — Catalog DB password
 *   NEXUS_USER        — Nexus username
 *   NEXUS_PASS        — Nexus password
 *   SENTRY_AUTH_TOKEN — Sentry auth token
 */

import jenkins.model.Jenkins
import com.cloudbees.plugins.credentials.CredentialsProvider
import com.cloudbees.plugins.credentials.CredentialsScope
import com.cloudbees.plugins.credentials.SystemCredentialsProvider
import com.cloudbees.plugins.credentials.domains.Domain
import com.cloudbees.plugins.credentials.impl.UsernamePasswordCredentialsImpl
import org.jenkinsci.plugins.plaincredentials.impl.StringCredentialsImpl

def logger = java.util.logging.Logger.getLogger("init.groovy.d/fix-credentials")

logger.info("=== init script fix-credentials.groovy starting ===")

// ------------------------------------------------------------------
// 1. Wait for Jenkins to be fully loaded (poll, up to 5 minutes)
// ------------------------------------------------------------------
int maxAttempts = 60   // 60 * 5s = 5 minutes
int attempt = 0
Jenkins jenkinsInstance = null

while (attempt < maxAttempts) {
    try {
        jenkinsInstance = Jenkins.instanceOrNull
    } catch (Exception e) {
        // Jenkins not ready yet
    }

    if (jenkinsInstance != null && jenkinsInstance.isAcceptingTasks()) {
        logger.info("Jenkins instance ready after ${attempt}s")
        break
    }

    try { sleep 5000 } catch (Exception ignored) {}
    attempt += 5
}

if (jenkinsInstance == null) {
    logger.warning("Jenkins instance still null after waiting — aborting")
    return
}

if (!jenkinsInstance.isAcceptingTasks()) {
    logger.warning("Jenkins is not accepting tasks yet — will retry once more after 10s")
    try { sleep 10000 } catch (Exception ignored) {}
    if (!jenkinsInstance.isAcceptingTasks()) {
        logger.warning("Jenkins still not accepting tasks — aborting")
        return
    }
}

// ------------------------------------------------------------------
// 2. Locate the system credentials store (use standard API, not
//    fragile lookupStores + provider.systemContext filtering)
// ------------------------------------------------------------------
def store = SystemCredentialsProvider.getInstance().getStore()
def domain = Domain.global()
def env = System.getenv()

logger.info("System credentials store: ${store.getClass().name}")

// ------------------------------------------------------------------
// 3. Credential factory methods
// ------------------------------------------------------------------
def makeUsernamePassword = { String id, String description, String user, String pass ->
    new UsernamePasswordCredentialsImpl(
            CredentialsScope.GLOBAL, id, description, user, pass
    )
}

def makeString = { String id, String description, String value ->
    new StringCredentialsImpl(
            CredentialsScope.GLOBAL, id, description,
            new hudson.util.Secret(value)
    )
}

// ------------------------------------------------------------------
// 4. Register or update a credential (delete old, create new)
// ------------------------------------------------------------------
def upsertCredential = { credFactory, String id, String description, Object... args ->
    try {
        // 4a. Delete existing credential with same id (if any)
        //     We iterate ALL stores because old encrypted entries may only
        //     be reachable through user-scoped stores or alternative providers.
        CredentialsProvider.lookupStores(jenkinsInstance).each { cs ->
            try {
                def existingList = cs.getCredentials(domain)
                existingList.each { existing ->
                    if (existing.id == id) {
                        try {
                            cs.removeCredentials(domain, existing)
                            logger.info("Removed old credential '${id}' from store ${cs.getClass().simpleName}")
                        } catch (Exception ex2) {
                            logger.warning("Failed to remove old '${id}' from ${cs.getClass().simpleName}: ${ex2.message}")
                        }
                    }
                }
            } catch (Exception ex1) {
                // Some stores may be uninitialised — that's fine
            }
        }

        // 4b. Create new credential in the system store
        def cred = credFactory(id, description, *args)
        store.addCredentials(domain, cred)
        store.save()
        logger.info("Created/updated credential: ${id}")
        return true
    } catch (Exception ex) {
        logger.severe("Failed to upsert credential ${id}: ${ex.message}")
        ex.printStackTrace()
        return false
    }
}

// ------------------------------------------------------------------
// 5. Upsert all credentials from env vars
// ------------------------------------------------------------------
logger.info("Environment: MINIO_JENKINS_AK  = ${env.MINIO_JENKINS_AK != null ? 'set' : 'MISSING'}")
logger.info("Environment: MINIO_JENKINS_SK  = ${env.MINIO_JENKINS_SK != null ? 'set' : 'MISSING'}")
logger.info("Environment: PG_PASSWORD       = ${env.PG_PASSWORD      != null ? 'set' : 'MISSING'}")
logger.info("Environment: NEXUS_USER        = ${env.NEXUS_USER       != null ? 'set' : 'MISSING'}")
logger.info("Environment: NEXUS_PASS        = ${env.NEXUS_PASS       != null ? 'set' : 'MISSING'}")
logger.info("Environment: SENTRY_AUTH_TOKEN = ${env.SENTRY_AUTH_TOKEN != null ? 'set' : 'MISSING'}")

int count = 0
int failCount = 0

if (env.MINIO_JENKINS_AK && env.MINIO_JENKINS_SK) {
    if (upsertCredential(makeUsernamePassword, "minio-jenkins",
            "MinIO Jenkins Access/Secret Key", env.MINIO_JENKINS_AK, env.MINIO_JENKINS_SK)) {
        count++
    } else {
        failCount++
    }
} else {
    logger.warning("MINIO_JENKINS_AK or MINIO_JENKINS_SK not set — skipping minio-jenkins credential")
}

if (env.PG_PASSWORD) {
    if (upsertCredential(makeString, "pg-password",
            "Catalog DB Password", env.PG_PASSWORD)) {
        count++
    } else {
        failCount++
    }
}

if (env.NEXUS_USER && env.NEXUS_PASS) {
    if (upsertCredential(makeUsernamePassword, "nexus",
            "Nexus Repository Credentials", env.NEXUS_USER, env.NEXUS_PASS)) {
        count++
    } else {
        failCount++
    }
}

if (env.SENTRY_AUTH_TOKEN) {
    if (upsertCredential(makeString, "sentry-token",
            "Sentry Auth Token", env.SENTRY_AUTH_TOKEN)) {
        count++
    } else {
        failCount++
    }
}

if (env.MINIO_ENDPOINT) {
    if (upsertCredential(makeString, "minio-endpoint",
            "MinIO API endpoint (for agent upload)", env.MINIO_ENDPOINT)) {
        count++
    } else {
        failCount++
    }
}

logger.info("=== init script fix-credentials.groovy done: ${count} ok, ${failCount} failed ===")
