// sdk-build-plan/scripts-stub/sdkPipeline.groovy
// 放进独立仓库 jenkins-shared-lib/vars/sdkPipeline.groovy
// 业务仓库 Jenkinsfile 内调用：
//   @Library('sdk-shared-lib@main') _
//   sdkPipeline(product:'rtc-sdk', platforms:['android','ios','windows','linux'])

def call(Map cfg) {
    def product   = cfg.product   ?: error('product is required')
    def platforms = cfg.platforms ?: ['linux']
    def notifyCfg = cfg.notify    ?: [:]

    pipeline {
        agent none
        options {
            timestamps()
            ansiColor('xterm')
            buildDiscarder(logRotator(numToKeepStr: '50'))
            timeout(time: 90, unit: 'MINUTES')
        }
        environment {
            PRODUCT   = "${product}"
            CHANNEL   = "${decideChannel(env.BRANCH_NAME, env.TAG_NAME)}"
            BUILD_ID  = "${env.BUILD_NUMBER}"
        }
        stages {
            stage('Prepare') {
                agent { label 'linux' }
                steps {
                    checkout scm
                    script {
                        env.VERSION = readVersion(env.CHANNEL, env.BUILD_ID)
                        env.RELEASE = "${env.PRODUCT}@${env.VERSION}+${env.GIT_COMMIT.take(7)}"
                        echo "Channel=${env.CHANNEL} Version=${env.VERSION} Release=${env.RELEASE}"
                    }
                    stash name: 'src', useDefaultExcludes: false
                }
            }

            stage('Build matrix') {
                steps {
                    script {
                        def branches = [:]
                        platforms.each { p ->
                            branches[p] = {
                                def lbl = (p == 'ios') ? 'mac' : (p == 'windows' ? 'win' : 'linux')
                                node(lbl) {
                                    unstash 'src'
                                    withEnv(["PLATFORM=${p}"]) {
                                        if (p == 'windows') {
                                            bat 'ci\\scripts\\build-windows.cmd'
                                            bat 'ci\\scripts\\package.sh windows'   // git-bash
                                        } else {
                                            sh "ci/scripts/build-${p}.sh"
                                            sh "ci/scripts/package.sh ${p}"
                                        }
                                    }
                                    stash name: "pkg-${p}", includes: "dist/${p}/**"
                                }
                            }
                        }
                        parallel branches
                    }
                }
            }

            stage('Aggregate') {
                agent { label 'linux' }
                steps {
                    script { platforms.each { unstash "pkg-${it}" } }
                    sh 'ci/scripts/aggregate.sh'   // 合并 manifest, 算 sha256, 生成 sbom
                    archiveArtifacts artifacts: 'dist/**/*.zip,dist/**/metadata.json,dist/**/sbom.cdx.json',
                                     fingerprint: true
                }
            }

            stage('Publish: dev') {
                when { expression { env.CHANNEL == 'dev' } }
                agent { label 'linux' }
                steps { sh 'ci/scripts/publish.sh dev' }
            }

            stage('Approve & publish: staging') {
                when { expression { env.CHANNEL == 'staging' } }
                steps {
                    script {
                        input message: '提测到 staging?',
                              submitter: 'qa-leads,tech-leads',
                              submitterParameter: 'APPROVER'
                    }
                    node('linux') {
                        sh 'ci/scripts/publish.sh staging'
                    }
                }
            }

            stage('Approve & publish: release') {
                when { expression { env.CHANNEL == 'release' } }
                steps {
                    script {
                        // 0) 校验 tag 已 GPG 签名
                        node('linux') {
                            unstash 'src'
                            sh '''
                                git verify-tag "$TAG_NAME" || {
                                  echo "release tag must be GPG signed"; exit 1; }
                            '''
                        }
                        input message: '正式发布到 release?',
                              submitter: 'release-managers',
                              submitterParameter: 'APPROVER'
                    }
                    node('linux') {
                        sh 'ci/scripts/publish.sh release'
                    }
                }
            }
        }
        post {
            success { script { notify(notifyCfg, 'SUCCESS') } }
            failure { script { notify(notifyCfg, 'FAILURE') } }
            aborted { script { notify(notifyCfg, 'ABORTED') } }
        }
    }
}

// ---------- helpers (放 vars/ 也行, 这里展示在一起) ----------

def decideChannel(branch, tag) {
    if (tag ==~ /^v\d+\.\d+\.\d+$/)            return 'release'
    if (tag ==~ /^v\d+\.\d+\.\d+-rc\.\d+$/)    return 'staging'
    if (branch ==~ /^release\/\d+\.\d+$/)       return 'staging'
    return 'dev'
}

def readVersion(channel, buildId) {
    def base = readFile('VERSION').trim()                // e.g. "1.7.0"
    def sha  = sh(script: 'git rev-parse --short=7 HEAD', returnStdout: true).trim()
    switch (channel) {
        case 'release': return base
        case 'staging': return env.TAG_NAME?.replaceFirst('^v','') ?: "${base}-rc.${buildId}+${sha}"
        default:        return "${base}-dev.${buildId}+${sha}"
    }
}

def notify(cfg, status) {
    if (!cfg) return
    def color  = (status == 'SUCCESS') ? '#2eb886' : (status == 'FAILURE' ? '#cc0000' : '#888888')
    def text   = "[${status}] ${env.PRODUCT} ${env.VERSION} (${env.CHANNEL})\n" +
                 "Branch: ${env.BRANCH_NAME ?: env.TAG_NAME}  Commit: ${env.GIT_COMMIT?.take(7)}\n" +
                 "Build: ${env.BUILD_URL}\n" +
                 "Release: ${env.RELEASE}"
    if (cfg.feishu) {
        httpRequest httpMode: 'POST',
                    url: cfg.feishu,
                    contentType: 'APPLICATION_JSON',
                    requestBody: groovy.json.JsonOutput.toJson([
                        msg_type: 'text',
                        content : [text: text]
                    ])
    }
    if (cfg.dingtalk) {
        httpRequest httpMode: 'POST',
                    url: cfg.dingtalk,
                    contentType: 'APPLICATION_JSON',
                    requestBody: groovy.json.JsonOutput.toJson([
                        msgtype : 'text',
                        text    : [content: text]
                    ])
    }
}