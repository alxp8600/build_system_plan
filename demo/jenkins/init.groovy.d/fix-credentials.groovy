/**
 * Jenkins init script — fix credentials when encryption keys change
 *
 * This script runs at Jenkins startup (via init.groovy.d) and ensures
 * that critical credentials are re-registered if the Jenkins encryption
 * key has been rotated (e.g. after a fresh deploy with a new jenkins_home).
 *
 * Environment variables expected:
 *   MINIO_JENKINS_AK  — MinIO Jenkins access key
 *   MINIO_JENKINS_SK  — MinIO Jenkins secret key
 *   PG_PASSWORD       — Catalog DB password
 *   NEXUS_USER        — Nexus username
 *   NEXUS_PASS        — Nexus password
 */

import jenkins.model.Jenkins
import com.cloudbees.plugins.credentials.CredentialsProvider
import com.cloudbees.plugins.credentials.CredentialsScope
import com.cloudbees.plugins.credentials.domains.Domain
import com.cloudbees.plugins.credentials.impl.UsernamePasswordCredentialsImpl
import org.jenkinsci.plugins.plaincredentials.impl.StringCredentialsImpl

def logger = java.util.logging.Logger.getLogger("init.groovy.d/fix-credentials")

logger.info("Running init script: fix-credentials.groovy")

// Wait for Jenkins to be fully ready before touching credentials
// (credentials plugin may not be fully initialized during early startup)
try {
    sleep 5000
} catch (Exception e) {
    logger.warning("Sleep interrupted: ${e.message}")
}

def store = Jenkins.instanceOrNull?.getExtensionList(
    CredentialsProvider.class).find { it.systemContext }

if (!store) {
    logger.warning("System credentials provider not found — credentials not updated")
    return
}

def domain = Domain.global()

def registerOrUpdateString = { String id, String description, String value ->
    def existing = CredentialsProvider.lookupCredentials(
        StringCredentialsImpl.class, Jenkins.instance ?: null, null, null
    ).find { it.id == id }

    if (existing) {
        // Update existing credential
        def updated = new StringCredentialsImpl(
            CredentialsScope.GLOBAL, id, description,
            new hudson.util.Secret(value)
        )
        CredentialsProvider.lookupStores(Jenkins.instance ?: null)
            .each { cs ->
                cs.removeCredentials(domain, existing)
                cs.addCredentials(domain, updated)
            }
        logger.info("Updated credential: ${id}")
    } else {
        // Create new credential
        def cred = new StringCredentialsImpl(
            CredentialsScope.GLOBAL, id, description,
            new hudson.util.Secret(value)
        )
        CredentialsProvider.lookupStores(Jenkins.instance ?: null)
            .each { cs -> cs.addCredentials(domain, cred) }
        logger.info("Created credential: ${id}")
    }
}

def registerOrUpdateUsernamePassword = { String id, String description, String user, String pass ->
    def existing = CredentialsProvider.lookupCredentials(
        UsernamePasswordCredentialsImpl.class, Jenkins.instance ?: null, null, null
    ).find { it.id == id }

    if (existing) {
        def updated = new UsernamePasswordCredentialsImpl(
            CredentialsScope.GLOBAL, id, description, user, pass
        )
        CredentialsProvider.lookupStores(Jenkins.instance ?: null)
            .each { cs ->
                cs.removeCredentials(domain, existing)
                cs.addCredentials(domain, updated)
            }
        logger.info("Updated credential: ${id}")
    } else {
        def cred = new UsernamePasswordCredentialsImpl(
            CredentialsScope.GLOBAL, id, description, user, pass
        )
        CredentialsProvider.lookupStores(Jenkins.instance ?: null)
            .each { cs -> cs.addCredentials(domain, cred) }
        logger.info("Created credential: ${id}")
    }
}

// --- Register/update credentials from environment variables ---

def env = System.getenv()

if (env.MINIO_JENKINS_AK && env.MINIO_JENKINS_SK) {
    registerOrUpdateUsernamePassword(
        "minio-jenkins", "MinIO Jenkins Access/Secret Key",
        env.MINIO_JENKINS_AK, env.MINIO_JENKINS_SK
    )
}

if (env.PG_PASSWORD) {
    registerOrUpdateString(
        "pg-password", "Catalog DB Password",
        env.PG_PASSWORD
    )
}

if (env.NEXUS_USER && env.NEXUS_PASS) {
    registerOrUpdateUsernamePassword(
        "nexus", "Nexus Repository Credentials",
        env.NEXUS_USER, env.NEXUS_PASS
    )
}

if (env.SENTRY_AUTH_TOKEN) {
    registerOrUpdateString(
        "sentry-auth-token", "Sentry Auth Token",
        env.SENTRY_AUTH_TOKEN
    )
}

logger.info("init script fix-credentials.groovy completed")